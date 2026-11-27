"""Safe public reads for the scout agent.

Every retailer request goes through ``SafeWeb.get``:

* HTTPS GET only, to a host whose every DNS answer is public. The connection is pinned
  to the checked address (the same defence against DNS rebinding as app.sources).
* No cookies, no credentials, no proxy from the environment, an honest TechScout
  user agent, and no cart, checkout, account, login or admin paths.
* robots.txt is fetched once per host per run and obeyed (app.scout.robots).
* Per-host pacing and caps, plus a run-wide request cap, keep the workload small.
* Bodies are capped; redirects are re-checked hop by hop.

Errors are ``FetchError`` with fixed wording, never page text, so the agent can be
told what went wrong without letting a page speak to it through an error message.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import re
import socket
import threading
import time
from typing import Callable
import posixpath
from urllib.parse import unquote, urljoin, urlsplit

import httpcore2
import httpx2

from app.scout import robots as robots_rules

USER_AGENT = 'TechScout/0.1 (+https://github.com/han33-dotcom/TechScout)'
MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 4
CONNECT_TIMEOUT_S = 6.0
READ_TIMEOUT_S = 12.0
# Paths a read-only shopping assistant never needs. Matched on the lowercased path.
DENIED_PATH = re.compile(
    r'/(cart|carts|checkout|checkouts|account|accounts|login|logout|signin|sign-in|register|'
    r'admin|wp-admin|my-account|orders|wishlist|customer|password)(/|$|\?|\.)', re.IGNORECASE)
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.0.0.0/24', '192.0.2.0/24', '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
    '2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20', 'fc00::/7', 'fe80::/10', '::/128', '::1/128'))
GLOBAL_UNICAST_V6 = ipaddress.ip_network('2000::/3')
ACTION_QUERY = re.compile(r'(^|&)(add-to-cart|add_to_cart|wc-ajax|remove_item|customer-logout|action|'
                          r'removed_item|apply_coupon)=', re.IGNORECASE)


class FetchError(Exception):
    """A refused or failed read. ``code`` is one of a fixed set; ``reason`` is fixed wording."""

    def __init__(self, code: str, reason: str):
        super().__init__(f'{code}: {reason}')
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Page:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    sha256: str
    observed_at: datetime
    elapsed_ms: int

    def text(self) -> str:
        return self.body.decode('utf-8', 'replace')


def public_address(answer: str) -> str | None:
    try:
        ip = ipaddress.ip_address(answer)
    except ValueError:
        return None
    if ip.version == 6 and ip not in GLOBAL_UNICAST_V6:
        return None
    return None if any(ip in net for net in BLOCKED_NETWORKS) else str(ip)


def system_resolver(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


class _Pinned(httpcore2.NetworkBackend):
    """Connects to the checked address; TLS still verifies the URL's hostname."""

    def __init__(self, address: str):
        self._backend, self._address = httpcore2.SyncBackend(), address

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return self._backend.connect_tcp(self._address, port, timeout, local_address, socket_options)


@dataclass
class _HostState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_at: float = 0.0
    used: int = 0
    robots: robots_rules.Robots | None = None
    robots_lock: threading.Lock = field(default_factory=threading.Lock)


class SafeWeb:
    """Read-only HTTPS client for public retailer pages and endpoints, shared by one run."""

    def __init__(self, *, transport: httpx2.BaseTransport | None = None,
                 resolver: Callable[[str, int], list[str]] = system_resolver,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep,
                 min_interval_s: float = 0.35, per_host_cap: int = 40, run_cap: int = 220,
                 obey_robots: bool = True, max_bytes: int = MAX_BYTES):
        self._transport, self._resolver = transport, resolver
        self._clock, self._wall, self._sleep = clock, wall_clock, sleep
        self.min_interval_s, self.per_host_cap, self.run_cap = min_interval_s, per_host_cap, run_cap
        self.obey_robots, self.max_bytes = obey_robots, max_bytes
        self._hosts: dict[str, _HostState] = defaultdict(_HostState)
        self._lock = threading.Lock()
        self.requests = 0
        self.log: list[dict] = []

    # ---- public API -------------------------------------------------------------------
    def get(self, url: str, *, accept: str = 'any', deadline: float | None = None,
            timeout: float | None = None) -> Page:
        """GET ``url`` (following checked redirects). Raises FetchError."""
        self._check_url(url)
        if self.obey_robots and not self.robots_for(url, deadline=deadline).allows(url):
            raise FetchError('ROBOTS', "the shop's robots.txt does not allow this page")
        chain = [url]
        started = self._clock()
        while True:
            page_or_next = self._once(chain[-1], accept, deadline, started, url, read_timeout=timeout)
            if isinstance(page_or_next, Page):
                return page_or_next
            target = page_or_next
            if len(chain) > MAX_REDIRECTS:
                raise FetchError('REDIRECTS', 'too many redirects')
            self._check_url(target)
            if not same_site(url, target):
                # A redirect to another site would credit that site's page to the shop that was asked.
                raise FetchError('OFF_SITE', 'the shop redirected to a different website')
            if self.obey_robots and not self.robots_for(target, deadline=deadline).allows(target):
                raise FetchError('ROBOTS', "the shop's robots.txt does not allow the redirect target")
            chain.append(target)

    def robots_for(self, url: str, *, deadline: float | None = None) -> robots_rules.Robots:
        host = _host(url)
        state = self._hosts[host]
        with state.robots_lock:
            if state.robots is None:
                robots_url = f'https://{host}/robots.txt'
                try:
                    page = self._once(robots_url, 'any', deadline, self._clock(), robots_url,
                                      allow_status=True, count_robots=False)
                    if isinstance(page, str):  # redirected robots.txt: one more checked hop on the same site
                        self._check_url(page)
                        if not same_site(robots_url, page):
                            raise FetchError('OFF_SITE', 'robots.txt redirected to a different website')
                        page = self._once(page, 'any', deadline, self._clock(), robots_url,
                                          allow_status=True, count_robots=False)
                    status = page.status if isinstance(page, Page) else None
                    text = page.text() if isinstance(page, Page) else None
                    state.robots = robots_rules.from_response(status, text)
                except FetchError as exc:
                    state.robots = (robots_rules.ALLOW_ALL if exc.code == 'HTTP_4XX'
                                    else robots_rules.DISALLOW_ALL)
            return state.robots

    # ---- internals --------------------------------------------------------------------
    def _check_url(self, url: str) -> None:
        if not isinstance(url, str) or len(url) > 2048:
            raise FetchError('BAD_URL', 'not a usable URL')
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise FetchError('BAD_URL', 'only plain https URLs are read')
        if parts.port not in (None, 443):
            raise FetchError('BAD_URL', 'only the standard https port is read')
        if '%2f' in parts.path.lower() or '%5c' in parts.path.lower():
            raise FetchError('BAD_URL', 'encoded slashes in the path are refused')
        path = parts.path
        for _ in range(3):  # decode repeatedly so %2563art cannot hide /cart
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        path = posixpath.normpath(path or '/').lower()
        if DENIED_PATH.search(path + '/'):
            raise FetchError('DENIED_PATH', 'cart, checkout, account and admin pages are never read')
        if ACTION_QUERY.search(unquote(parts.query or '')):
            raise FetchError('DENIED_PATH', 'store actions (add to cart, log out, ajax actions) are never requested')

    def _address(self, host: str) -> str:
        try:
            answers = list(self._resolver(host, 443))
        except OSError:
            raise FetchError('DNS', 'the shop host did not resolve') from None
        checked = [public_address(a) for a in answers]
        if not checked or None in checked:
            raise FetchError('NOT_PUBLIC', 'the host does not resolve only to public addresses')
        return checked[0]

    def _pace(self, host: str, deadline: float | None, count: bool) -> None:
        state = self._hosts[host]
        with self._lock:
            if count and self.requests >= self.run_cap:
                raise FetchError('RUN_CAP', 'the run used its retailer request allowance')
            if count:
                self.requests += 1
        with state.lock:
            if count and state.used >= self.per_host_cap:
                raise FetchError('HOST_CAP', 'this shop already received its request allowance in this run')
            if count:  # robots.txt is paced like any request but not charged to the shop's allowance
                state.used += 1
            now = self._clock()
            delay = self.min_interval_s
            if state.robots is not None and state.robots.crawl_delay:
                delay = max(delay, min(state.robots.crawl_delay, 5.0))
            wait = max(0.0, state.next_at - now)
            if deadline is not None and now + wait >= deadline:
                raise FetchError('DEADLINE', 'no time left for this read')
            state.next_at = max(now, state.next_at) + delay
        if wait:
            self._sleep(wait)

    def _client(self, address: str) -> httpx2.BaseTransport:
        if self._transport is not None:
            return self._transport
        context = httpx2.create_ssl_context(trust_env=False)
        transport = httpx2.HTTPTransport(verify=context, trust_env=False)
        transport._pool = httpcore2.ConnectionPool(ssl_context=context, max_keepalive_connections=0,
                                                   network_backend=_Pinned(address))
        return transport

    def _once(self, url: str, accept: str, deadline: float | None, started: float, original: str, *,
              allow_status: bool = False, count_robots: bool = True, read_timeout: float | None = None) -> Page | str:
        host = _host(url)
        self._pace(host, deadline, count=count_robots)
        address = self._address(host)  # checked even when a test supplies the transport
        timeout = read_timeout or READ_TIMEOUT_S
        if deadline is not None:
            timeout = max(0.5, min(timeout, deadline - self._clock()))
        headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip', 'Accept': _accept(accept)}
        request = httpx2.Request('GET', url, headers=headers, extensions={'timeout': httpx2.Timeout(
            timeout, connect=min(CONNECT_TIMEOUT_S, timeout)).as_dict()})
        t0 = self._clock()
        entry = dict(host=host, path=urlsplit(url).path[:120], status=None, ms=None, bytes=0)
        self.log.append(entry)
        transport = self._client(address)
        try:
            response = transport.handle_request(request)
            try:
                status = response.status_code
                entry['status'] = status
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise FetchError('REDIRECTS', 'redirect without a location')
                    return urljoin(url, location)
                body = self._read(response, deadline)
            finally:
                response.close()
        except httpx2.TimeoutException:
            entry['status'] = 'timeout'
            raise FetchError('TIMEOUT', 'the shop did not answer in time') from None
        except httpx2.HTTPError:
            entry['status'] = 'network'
            raise FetchError('NETWORK', 'the connection to the shop failed') from None
        finally:
            entry['ms'] = round((self._clock() - t0) * 1000)
            if self._transport is None:
                transport.close()
        entry['bytes'] = len(body)
        if not allow_status:
            if status == 429:
                raise FetchError('RATE_LIMITED', 'the shop asked us to slow down')
            if 400 <= status < 500:
                raise FetchError('HTTP_4XX', f'the shop answered {status}')
            if status >= 500:
                raise FetchError('HTTP_5XX', f'the shop answered {status}')
        if allow_status and 400 <= status < 500:
            raise FetchError('HTTP_4XX', f'the shop answered {status}')
        if allow_status and status >= 500:
            raise FetchError('HTTP_5XX', f'the shop answered {status}')
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if _challenge(status, content_type, body):
            raise FetchError('BOT_CHALLENGE', 'the shop answered with a bot challenge; it is not bypassed')
        return Page(original, url, status, content_type, body, hashlib.sha256(body).hexdigest(),
                    self._wall(), round((self._clock() - started) * 1000))

    def _read(self, response: httpx2.Response, deadline: float | None) -> bytes:
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > self.max_bytes:
                raise FetchError('TOO_LARGE', 'the page is larger than the read limit')
            if deadline is not None and self._clock() > deadline:
                raise FetchError('DEADLINE', 'no time left for this read')
            chunks.append(chunk)
        return b''.join(chunks)


def same_site(a: str, b: str) -> bool:
    """Same host, allowing only a www. prefix difference."""
    return _host(a).removeprefix('www.') == _host(b).removeprefix('www.')


def _host(url: str) -> str:
    return (urlsplit(url).hostname or '').lower()


def _accept(kind: str) -> str:
    return {'json': 'application/json', 'html': 'text/html,application/xhtml+xml',
            'xml': 'application/xml,text/xml,application/rss+xml'}.get(kind, '*/*')


def _challenge(status: int, content_type: str, body: bytes) -> bool:
    """SiteGround/Cloudflare-style interstitials: tiny HTML that refreshes or redirects in script."""
    if status == 202 and len(body) < 4096:
        return True
    if content_type.startswith('text/html') and len(body) < 2048:
        head = body[:2048].lower()
        return b'sgcaptcha' in head or b'cf-chl' in head or b'window.location.href' in head
    return False
