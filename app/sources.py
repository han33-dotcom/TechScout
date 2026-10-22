"""Safe source gate (MVP 04): the only path from TechScout to a retailer website.

Every attempt re-reads the registry, sends only an allow-listed GET to a checked public
address over a fresh, pinned and verified TLS connection, and ends in a typed outcome.
Per-source limits live here; run totals and cancellation belong to the run budget (MVP 11).
"""
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import threading
import time
from typing import Literal, NamedTuple, Protocol, runtime_checkable
import zlib

import httpcore2
import httpx2

from app.contracts import SourceLimits, SourcePolicy, ToolError, ToolResult
from app.source_registry import DEFAULT_REGISTRY, get_active_source

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WIRE_BYTES = MAX_RESPONSE_BYTES + 256 * 1024  # the body cap plus room for headers and framing
MAX_REDIRECTS = 3
PHASE_TIMEOUT_S = 5.0
SOFT_DEADLINE_S = 10.0
CALL_TIMEOUT_S = SOFT_DEADLINE_S + PHASE_TIMEOUT_S
MAX_WAIT_S = 10.0
BACKOFF_S = (1.0, 2.0)
WINDOW_S = 60.0
RUN_COUNT_TTL_S = 3600.0  # far above the 120-second run cap proposed in MVP 11: only ended runs are dropped
USER_AGENT = 'TechScout/0.1 (+https://github.com/han33-dotcom/TechScout)'
ACCEPT_ENCODING = 'gzip'
HEADERS = {'Accept-Encoding': ACCEPT_ENCODING, 'User-Agent': USER_AGENT}
Expect = Literal['json', 'html']
ACCEPT: dict[Expect, str] = {'json': 'application/json', 'html': 'text/html'}
CONTENT_ENCODINGS = frozenset({'identity', 'gzip'})
MAX_GZIP_LAYERS = 2
INFLATE_STEP_BYTES = 65536
REDIRECTS = frozenset({301, 302, 303, 307, 308})
TRANSIENT_STATUSES = frozenset({502, 503, 504})
# Words anywhere in the path or query that reach carts, accounts or forms, which change state
# even over GET (query routes such as ?rest_route=/wc/store/v1/cart), and WordPress's _method,
# which makes a REST GET act as another method.
ACTION_PATH_DENYLIST = frozenset({
    'cart', 'checkout', 'checkouts', 'my-account', 'account', 'login', 'register', 'wp-login.php',
    'wp-admin', 'xmlrpc.php', 'add-to-cart', 'batch', 'contact-form-7', 'wc-ajax', 'wc-api', '_method',
})
# Fixed table, never ipaddress's is_* flags, which change between Python patch releases.
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.0.0.0/24', '192.0.2.0/24', '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
    '2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20',
))
GLOBAL_UNICAST_V6 = ipaddress.ip_network('2000::/3')


class Reservation(Protocol):
    def commit(self, elapsed_seconds: float) -> None: ...

    def release(self) -> None: ...


@runtime_checkable
class RunBudget(Protocol):
    """The run controller's handle (MVP 11): reserve before sending, then commit or release.

    reserve_call returns None when the call does not fit (no calls or time left, cancelled).
    """
    run_id: str

    def reserve_call(self, source_id: str, max_seconds: float) -> Reservation | None: ...


def _error(code: str, source_id: str, reason: str, retryable: bool = False, token: str = '') -> ToolError:
    # Fixed wording only, never page content, so messages are safe to show the planner.
    prefix = f'{token} ' if token else ''
    return ToolError(code=code, message=f'{prefix}{source_id[:60]}: {reason}', retryable=retryable)


class _Stop(Exception):
    def __init__(self, code: str, source_id: str, reason: str, *, retryable: bool = False,
                 token: str = '', retry_after: float | None = None):
        super().__init__(reason)
        self.error = _error(code, source_id, reason, retryable, token)
        self.retry_after = retry_after


class _Refusal(_Stop):
    """Refused before anything was sent; ends the fetch."""


class _Failure(_Stop):
    """An attempt failed; retried when retryable and the limits allow."""


@dataclass(frozen=True)
class FetchOutcome:
    """One logical fetch. The body stays out of ToolResult; Evidence uses the metadata."""
    source_id: str
    origin_url: str
    redirect_chain: tuple[str, ...]
    attempts: int
    duration_ms: int
    errors: tuple[ToolError, ...] = ()
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    content_encoding: str | None = None
    body: bytes | None = None
    response_sha256: str | None = None
    observed_at: datetime | None = None

    @property
    def status(self) -> str:
        return 'error' if self.errors else 'ok'

    def entry(self) -> dict:
        return {
            'source_id': self.source_id, 'origin_url': self.origin_url, 'final_url': self.final_url,
            'redirect_chain': list(self.redirect_chain), 'status_code': self.status_code,
            'content_type': self.content_type, 'content_encoding': self.content_encoding,
            'byte_length': None if self.body is None else len(self.body),
            'response_sha256': self.response_sha256,
            'observed_at': None if self.observed_at is None else self.observed_at.isoformat(),
            'attempts': self.attempts, 'outcome': self.status,
            'error_code': self.errors[0].code if self.errors else None,
        }


def to_tool_result(outcomes: Iterable[FetchOutcome]) -> ToolResult:
    """Fetches in request order; a failure never removes or changes an earlier result."""
    outcomes = list(outcomes)
    if not outcomes:
        raise ValueError('No fetch outcomes to report')
    errors = [e for o in outcomes for e in o.errors]
    status = 'ok' if not errors else 'partial' if any(o.status == 'ok' for o in outcomes) else 'error'
    return ToolResult(status=status, data={'fetches': [o.entry() for o in outcomes]}, errors=errors,
                      duration_ms=sum(o.duration_ms for o in outcomes))


@dataclass
class _Slot:
    run_id: str
    source_id: str
    limits: SourceLimits
    planned: float
    wait: float
    queued: bool
    sent: bool = False


class SourceLimiter:
    """Per-source minute window, concurrency, cool-down and per-run counts.

    State is shared by every run that uses the same limiter; gates built without one share a
    process-wide limiter. Concurrency waits block on a condition, never on sleep(), and
    requests in flight count against the store's current limit, even after it is lowered.
    A request that waited is checked again against the limits the registry gives after the
    wait. A run's counts are dropped once it has asked for nothing for RUN_COUNT_TTL_S.
    """

    def __init__(self, concurrency_wait_s: float = 10.0, max_wait_s: float = MAX_WAIT_S,
                 condition_factory=threading.Condition):
        self.concurrency_wait_s = concurrency_wait_s
        self.max_wait_s = max_wait_s
        self._lock = condition_factory()
        self._dispatches: dict[str, list[float]] = {}
        self._used: dict[tuple[str, str], int] = {}
        self._last_asked: dict[tuple[str, str], float] = {}  # least recently asked first
        self._cool_until: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}
        self._latest = float('-inf')

    def _now(self, now: float) -> float:
        # A clock that steps backwards must not expire earlier dispatches early.
        self._latest = max(self._latest, now)
        return self._latest

    def used(self, run_id: str, source_id: str) -> int:
        with self._lock:
            return self._used.get((run_id, source_id), 0)

    def cool_down(self, source_id: str, now: float, seconds: float) -> None:
        with self._lock:
            until = self._now(now) + seconds
            self._cool_until[source_id] = max(until, self._cool_until.get(source_id, until))

    def acquire(self, run_id: str, source_id: str, limits: SourceLimits, now: float, wait: float) -> _Slot:
        with self._lock:
            now = self._now(now)
            self._prune(now)
            self._last_asked.pop((run_id, source_id), None)
            self._last_asked[run_id, source_id] = now
            if self._used.get((run_id, source_id), 0) >= limits.max_requests_per_run:
                raise _Refusal('BUDGET_EXHAUSTED', source_id, 'per-run request cap reached')
            window = sorted(t for t in self._dispatches.get(source_id, ()) if t > now - WINDOW_S)
            wait = max(wait, self._cool_until.get(source_id, now) - now)
            if len(window) >= limits.requests_per_minute:
                wait = max(wait, window[len(window) - limits.requests_per_minute] + WINDOW_S - now)
            if wait > self.max_wait_s:
                raise _Refusal('RATE_LIMIT', source_id, 'per-source rate limit or cool-down in effect',
                               retryable=True)
            queued = self._in_flight.get(source_id, 0) >= limits.concurrency
            slot = _Slot(run_id, source_id, limits, now + wait, wait, queued)
            self._dispatches[source_id] = [*window, slot.planned]
            self._used[run_id, source_id] = self._used.get((run_id, source_id), 0) + 1
            if not self._lock.wait_for(lambda: self._in_flight.get(source_id, 0) < limits.concurrency,
                                       self.concurrency_wait_s):
                self._forget(slot)
                raise _Refusal('RATE_LIMIT', source_id, 'concurrency limit busy', retryable=True)
            if self._cool_until.get(source_id, now) - now > self.max_wait_s:  # set while it queued
                self._forget(slot)
                raise _Refusal('RATE_LIMIT', source_id, 'store asked for a pause', retryable=True)
            self._in_flight[source_id] = self._in_flight.get(source_id, 0) + 1
        return slot

    def recheck(self, slot: _Slot, limits: SourceLimits) -> None:
        """After a wait: refuse if the store asked for a pause or its limits were lowered meanwhile.

        Nothing has been sent, so a refused slot is given back by release() as usual.
        """
        with self._lock:
            # A pause known at acquire ends by slot.planned, so a later one was asked for during the wait.
            if self._cool_until.get(slot.source_id, slot.planned) > slot.planned:
                raise _Refusal('RATE_LIMIT', slot.source_id, 'store asked for a pause', retryable=True)
            if limits == slot.limits:
                return  # unchanged, so acquire's checks still hold
            # This slot is already counted in each total, and in flight.
            if self._used.get((slot.run_id, slot.source_id), 0) > limits.max_requests_per_run:
                raise _Refusal('BUDGET_EXHAUSTED', slot.source_id, 'per-run request cap reached')
            minute = [t for t in self._dispatches[slot.source_id] if t <= slot.planned < t + WINDOW_S]
            if len(minute) > limits.requests_per_minute:
                raise _Refusal('RATE_LIMIT', slot.source_id, 'per-source rate limit or cool-down in effect',
                               retryable=True)
            if self._in_flight[slot.source_id] > limits.concurrency:
                raise _Refusal('RATE_LIMIT', slot.source_id, 'concurrency limit busy', retryable=True)

    def mark_sent(self, slot: _Slot, now: float) -> None:
        with self._lock:
            window = self._dispatches[slot.source_id]
            if slot.planned in window:
                window.remove(slot.planned)
            window.append(self._now(now))
            slot.sent = True

    def release(self, slot: _Slot) -> None:
        with self._lock:
            if not slot.sent:
                self._forget(slot)
            self._in_flight[slot.source_id] -= 1
            self._lock.notify_all()

    def _forget(self, slot: _Slot) -> None:
        # Nothing was sent: give back the minute slot and the per-run count. The caller holds the lock.
        window = self._dispatches[slot.source_id]
        if slot.planned in window:
            window.remove(slot.planned)
        if (slot.run_id, slot.source_id) in self._used:  # gone only if held past RUN_COUNT_TTL_S
            self._used[slot.run_id, slot.source_id] -= 1

    def _prune(self, now: float) -> None:
        # Least recently asked first, so each acquire stops at the first run still in use.
        while self._last_asked:
            key = next(iter(self._last_asked))
            if now - self._last_asked[key] <= RUN_COUNT_TTL_S:
                return
            del self._last_asked[key]
            self._used.pop(key, None)


# Process-wide, like run recovery's single-process assumption; several workers need shared limits (MVP 11).
_SHARED_LIMITER = SourceLimiter()  # so gates built separately still keep each store's limits


class _AttemptGuard:
    """One attempt's soft deadline, and its cap on bytes received before any decoding.

    Socket timeouts apply to each wait separately, and 1xx replies or compressed data that
    decodes to nothing never reach the body loop, so the wire itself is bounded here.
    """

    def __init__(self, source_id: str, clock: Callable[[], float], start: float):
        self._source_id, self._clock = source_id, clock
        self._deadline, self._received = start + SOFT_DEADLINE_S, 0

    def check(self) -> float:
        left = self._deadline - self._clock()
        if left <= 0:
            raise _Failure('TIMEOUT', self._source_id, 'attempt passed its soft deadline', retryable=True)
        return left

    def timeout(self, phase: float | None) -> float:
        left = self.check()
        return left if phase is None else min(phase, left)

    def count(self, data: bytes) -> bytes:
        self._received += len(data)
        if self._received > MAX_WIRE_BYTES:
            raise _too_large(self._source_id)
        return data


class _PinnedBackend(httpcore2.NetworkBackend):
    """Connects to the address the gate checked; TLS still verifies the URL's hostname."""

    def __init__(self, backend: httpcore2.NetworkBackend, address: str, guard: _AttemptGuard):
        self._backend, self._address, self._guard = backend, address, guard

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = self._backend.connect_tcp(self._address, port, self._guard.timeout(timeout), local_address,
                                           socket_options)
        return _GuardedStream(stream, self._guard)


class _GuardedStream(httpcore2.NetworkStream):
    def __init__(self, stream: httpcore2.NetworkStream, guard: _AttemptGuard):
        self._stream, self._guard = stream, guard

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._guard.count(self._stream.read(max_bytes, self._guard.timeout(timeout)))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, self._guard.timeout(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> httpcore2.NetworkStream:
        self._stream = self._stream.start_tls(ssl_context, server_hostname, self._guard.timeout(timeout))
        return self

    def close(self) -> None:
        self._stream.close()

    def get_extra_info(self, info: str):
        return self._stream.get_extra_info(info)


class _Reply(NamedTuple):
    content_type: str
    content_encoding: str | None
    body: bytes
    observed_at: datetime


def system_resolver(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceGate:
    """Fetches allow-listed public endpoints of admitted sources.

    Runtime conditions (registry state, addresses, DNS, redirects, replies, limits, budget,
    network) become typed errors; programming mistakes raise TypeError or ValueError.
    A limiter keeps times from one clock, so a gate given another clock (a test's) needs its
    own SourceLimiter rather than the shared one.
    """

    def __init__(self, registry_path: Path | str = DEFAULT_REGISTRY, *, resolver=system_resolver,
                 clock=time.monotonic, wall_clock=_utc_now, sleep=time.sleep,
                 limiter: SourceLimiter | None = None):
        self._registry_path = registry_path
        self._resolver, self._clock, self._wall_clock, self._sleep = resolver, clock, wall_clock, sleep
        self._limiter = limiter if limiter is not None else _SHARED_LIMITER
        self._observed_now()
        self._transport = None
        self._backend = httpcore2.SyncBackend()

    @classmethod
    def _for_tests(cls, *args, transport=None, network_backend=None, **kwargs):
        """Test seam: a fake transport, or a fake backend beneath the pinning wrapper."""
        gate = cls(*args, **kwargs)
        gate._transport = transport
        gate._backend = network_backend or gate._backend
        return gate

    def requests_used(self, run_id: str, source_id: str) -> int:
        """Requests counted against the store's per-run cap, for the run controller (MVP 11)."""
        return self._limiter.used(run_id, source_id)

    def fetch(self, budget: RunBudget, source_id: str, url: str, *, expect: Expect) -> FetchOutcome:
        """GET one allowed endpoint; expect is 'json' or 'html'. The gate is the only retry loop."""
        if not isinstance(budget, RunBudget):
            raise TypeError('fetch needs the run budget handle')
        if type(source_id) is not str or type(url) is not str:  # a str subclass can fake equality
            raise TypeError('source_id and url must be plain strings')
        if expect not in ACCEPT:
            raise ValueError("expect must be 'json' or 'html'")
        started = self._clock()
        chain, attempts, retries, wait, failure = [url], 0, 0, 0.0, None
        while True:
            try:
                reply = self._attempt(budget, source_id, chain[-1], expect, wait)
            except _Refusal as stop:
                errors = [stop.error] if failure is None else [failure, stop.error]
                break
            except _Failure as stop:
                attempts += 1
                failure, hint = stop.error, stop.retry_after or 0.0
                # A long pause asked for by the store has already cooled it down (_attempt).
                if hint > self._limiter.max_wait_s or not failure.retryable or retries == len(BACKOFF_S):
                    errors = [failure]
                    break
                wait, retries = max(BACKOFF_S[retries], hint), retries + 1
                continue
            attempts += 1
            if isinstance(reply, str):
                if len(chain) > MAX_REDIRECTS:
                    errors = [_error('BLOCKED', source_id, f'more than {MAX_REDIRECTS} redirects',
                                     token='blocked:redirect')]
                    break
                chain.append(reply)
                wait, failure = 0.0, None
                continue
            return FetchOutcome(
                source_id, url, tuple(chain), attempts, self._duration_ms(started), final_url=chain[-1],
                status_code=200, content_type=reply.content_type, content_encoding=reply.content_encoding,
                body=reply.body, response_sha256=hashlib.sha256(reply.body).hexdigest(),
                observed_at=reply.observed_at)
        return FetchOutcome(source_id, url, tuple(chain), attempts, self._duration_ms(started), tuple(errors))

    def _attempt(self, budget: RunBudget, source_id: str, url: str, expect: Expect,
                 wait: float) -> str | _Reply:
        endpoints, limits = self._allowed(source_id, url)
        slot = self._limiter.acquire(budget.run_id, source_id, limits, self._clock(), wait)
        reservation, start = None, None
        try:
            reservation = budget.reserve_call(source_id, slot.wait + CALL_TIMEOUT_S)
            if reservation is None:
                raise _Refusal('BUDGET_EXHAUSTED', source_id, 'the run budget refused the call')
            if slot.wait:
                self._sleep(slot.wait)
            if slot.wait or slot.queued:  # the store may have been switched off, paused or limited meanwhile
                endpoints, limits = self._allowed(source_id, url)
                self._limiter.recheck(slot, limits)
            start = self._clock()
            guard = _AttemptGuard(source_id, self._clock, start)
            address = self._checked_address(source_id, httpx2.URL(url).host)
            guard.check()
            self._limiter.mark_sent(slot, self._clock())
            return self._exchange(source_id, url, address, expect, endpoints, guard)
        except _Failure as stop:
            if (stop.retry_after or 0.0) > self._limiter.max_wait_s:  # before a queued request wakes
                self._limiter.cool_down(source_id, self._clock(), stop.retry_after)
            raise
        finally:
            try:
                if reservation is not None:
                    if slot.sent:
                        reservation.commit(max(0.0, self._clock() - start))
                    else:
                        reservation.release()
            finally:
                self._limiter.release(slot)  # also when the budget raises, or the store stays locked

    def _allowed(self, source_id: str, url: str) -> tuple[list[str], SourceLimits]:
        endpoints, limits = self._admitted(source_id)
        if url not in endpoints:
            raise _Refusal('BLOCKED', source_id, 'not an allowed endpoint', token='blocked:url')
        return endpoints, limits

    def _admitted(self, source_id: str) -> tuple[list[str], SourceLimits]:
        """Re-read the registry each attempt; an unselected or unsafe entry disables the source."""
        try:
            entry = get_active_source(source_id, self._registry_path)
            registry = json.loads(Path(self._registry_path).read_text(encoding='utf-8'))
            # Both reads must show the same entry, so a file edited in between cannot mix versions.
            entries = registry['sources']
            unchanged = (all(isinstance(s, dict) for s in entries)
                         and [s for s in entries if s.get('id') == source_id] == [entry])
            selected = registry['live_release']['admitted_source_ids']
            limits = SourcePolicy.model_validate(entry).limits
            # Every endpoint (at least one) shares the origin's host, so checking them covers it.
            host = httpx2.URL(entry['origin']).host
            endpoints = entry['allowed_endpoints']
            if not (unchanged and isinstance(selected, list) and source_id in selected
                    and all(_safe_url(u, host) for u in endpoints)):
                raise ValueError('Source is not selected or not safely configured')
        except (OSError, ValueError, LookupError, TypeError, httpx2.InvalidURL) as exc:
            raise _Refusal('SOURCE_DISABLED', source_id, 'source is not admitted') from exc
        return endpoints, limits

    def _checked_address(self, source_id: str, host: str) -> str:
        try:
            answers = list(self._resolver(host, 443))
        except OSError as exc:
            raise _Failure('NETWORK_ERROR', source_id, 'DNS lookup failed', retryable=True) from exc
        if not answers:
            raise _Failure('NETWORK_ERROR', source_id, 'DNS lookup returned no address', retryable=True)
        addresses = [_public_address(a) for a in answers]
        if None in addresses:
            raise _Refusal('BLOCKED', source_id, 'a resolved address is not public', token='blocked:dns')
        return addresses[0]

    def _build_transport(self, address: str, guard: _AttemptGuard) -> httpx2.BaseTransport:
        """A fresh pool and TLS context per attempt, pinned to the checked address and guarded.

        truststore holds a context's lock through each handshake, so a shared context would
        queue every store behind a slow one. No proxy, and the gate reads no TLS settings from
        the environment (on Linux, OpenSSL itself still reads SSL_CERT_FILE and SSL_CERT_DIR).
        """
        if self._transport is not None:
            return self._transport
        context = httpx2.create_ssl_context(trust_env=False)
        transport = httpx2.HTTPTransport(verify=context, trust_env=False)
        transport._pool = httpcore2.ConnectionPool(
            ssl_context=context, max_keepalive_connections=0,
            network_backend=_PinnedBackend(self._backend, address, guard))
        return transport

    def _exchange(self, source_id: str, url: str, address: str, expect: Expect, endpoints: list[str],
                  guard: _AttemptGuard) -> str | _Reply:
        # Straight to the transport: a client object would add a cookie jar and parse Location
        # headers itself (raising on some) even when it does not follow redirects.
        request = httpx2.Request('GET', url, headers={**HEADERS, 'Accept': ACCEPT[expect]},
                                 extensions={'timeout': httpx2.Timeout(PHASE_TIMEOUT_S).as_dict()})
        with self._build_transport(address, guard) as transport:
            try:
                response = transport.handle_request(request)
                try:
                    return self._reply(source_id, url, response, expect, endpoints, guard)
                finally:
                    response.close()
            except httpx2.HTTPError as exc:
                raise _transport_failure(source_id, exc) from exc

    def _reply(self, source_id: str, url: str, response: httpx2.Response, expect: Expect,
               endpoints: list[str], guard: _AttemptGuard) -> str | _Reply:
        status = response.status_code
        if status in REDIRECTS:
            try:
                target = str(httpx2.URL(url).join(response.headers['location']))
            except (KeyError, httpx2.InvalidURL):
                target = None
            if target not in endpoints:
                raise _Failure('BLOCKED', source_id, 'redirect is not to an allowed endpoint',
                               token='blocked:redirect')
            return target
        if status == 429 or status in TRANSIENT_STATUSES:
            hint = _retry_after(response.headers.get('retry-after'), self._observed_now())
            code = 'RATE_LIMIT' if status == 429 else 'NETWORK_ERROR'
            raise _Failure(code, source_id, f'store answered {status}', retryable=True, retry_after=hint)
        if not 200 <= status < 300:
            raise _Failure('HTTP_ERROR', source_id, 'permanent HTTP status', token=f'http:{status}')
        if status != 200:
            raise _Failure('INVALID_SCHEMA', source_id, f'unexpected status {status}')
        encodings = [e.strip().lower()
                     for e in response.headers.get_list('content-encoding', split_commas=True)]
        layers = encodings.count('gzip')
        if not set(encodings) <= CONTENT_ENCODINGS or layers > MAX_GZIP_LAYERS:
            raise _Failure('BLOCKED', source_id, 'unsupported content encoding', token='blocked:encoding')
        declared = response.headers.get('content-length')
        if declared is not None and not (declared.isascii() and declared.isdigit()):
            raise _Failure('INVALID_SCHEMA', source_id, 'invalid Content-Length')
        declared = None if declared is None else int(declared)
        if declared is not None and declared > MAX_RESPONSE_BYTES:
            raise _too_large(source_id)
        chunked = 'chunked' in [t.strip().lower()
                                for t in response.headers.get_list('transfer-encoding', split_commas=True)]
        if declared is None and not chunked:
            # A reply ended only by closing the connection looks complete when it is cut off.
            raise _Failure('INVALID_SCHEMA', source_id, 'reply has no length framing')
        content_type = response.headers.get('content-type', '')
        if content_type.split(';')[0].strip().lower() != ACCEPT[expect]:
            raise _Failure('INVALID_SCHEMA', source_id, f'content type is not {ACCEPT[expect]}')
        body = self._read_body(source_id, response, layers, declared, guard)
        if not body or (expect == 'json' and not _strict_json(body)):
            raise _Failure('INVALID_SCHEMA', source_id, f'empty or malformed {expect} body')
        # The checked media type only: the header's parameters are store text the planner would see.
        return _Reply(ACCEPT[expect], ', '.join(encodings) or None, body, self._observed_now())

    def _read_body(self, source_id: str, response: httpx2.Response, layers: int, declared: int | None,
                   guard: _AttemptGuard) -> bytes:
        # Decoded here, as httpx2 passes on a gzip stream cut short or followed by more data.
        # Decoded bytes are capped, so compressed bombs stop early; partial bodies are discarded.
        def received() -> Iterator[bytes]:
            for data in response.iter_raw():
                if declared is not None and response.num_bytes_downloaded > declared:
                    raise _too_large(source_id)
                guard.check()
                yield data
        inflaters = [zlib.decompressobj(zlib.MAX_WBITS | 16) for _ in range(layers)]
        pieces = received()
        for inflater in inflaters:
            pieces = _inflate(inflater, pieces)
        chunks, size = [], 0
        try:
            for chunk in pieces:
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise _too_large(source_id)
                chunks.append(chunk)
        except zlib.error as exc:
            raise _Failure('INVALID_SCHEMA', source_id, 'corrupt compressed body') from exc
        if not all(i.eof and not i.unused_data for i in inflaters):
            raise _Failure('INVALID_SCHEMA', source_id, 'cut or extended compressed body')
        return b''.join(chunks)

    def _observed_now(self) -> datetime:
        now = self._wall_clock()
        if now.utcoffset() is None:
            raise ValueError('wall_clock must return timezone-aware datetimes')
        return now.astimezone(timezone.utc)

    def _duration_ms(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))


def _safe_url(url: str, host: str) -> bool:
    """Canonical HTTPS on the origin's named host: nothing two parsers could read differently."""
    parsed = httpx2.URL(url)
    labels = host.split('.')
    # Every word of the path and query, so '+', '[]' or a path-style query key cannot hide one;
    # PHP reads '.' in a parameter name as '_', so each word is also checked in that form.
    words = set(re.findall(r'[a-z0-9._-]+', parsed.raw_path.decode('ascii').lower()))
    words |= {word.replace('.', '_') for word in words}
    return (str(parsed) == url and parsed.scheme == 'https' and parsed.host == host
            and parsed.port is None and not parsed.userinfo and not parsed.fragment and '%' not in url
            and len(labels) > 1 and all(labels) and labels[-1] != 'localhost'
            and not re.fullmatch(r'\d+|0x[0-9a-f]*', labels[-1], re.IGNORECASE)
            and not words & ACTION_PATH_DENYLIST)


def _inflate(inflater, pieces: Iterator[bytes]) -> Iterator[bytes]:
    """One gzip layer, in bounded steps; it stops at data after the end of its stream."""
    for data in pieces:
        out = inflater.decompress(data, INFLATE_STEP_BYTES)
        while out:
            yield out
            out = inflater.decompress(inflater.unconsumed_tail, INFLATE_STEP_BYTES)
        if inflater.unused_data:
            return


def _public_address(answer) -> str | None:
    try:
        ip = ipaddress.ip_address(answer) if isinstance(answer, str) else None
    except ValueError:
        return None
    if ip is None or (ip.version == 6 and ip not in GLOBAL_UNICAST_V6):
        return None
    return None if any(ip in network for network in BLOCKED_NETWORKS) else str(ip)


def _retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds from a Retry-After header; anything unparseable is no hint."""
    if value is None:
        return None
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: a date field too large to hold
        return None
    return None if when.tzinfo is None else max(0.0, (when - now).total_seconds())


def _reject_constant(name: str):
    raise ValueError(f'{name} is not valid JSON')


def _strict_json(body: bytes) -> bool:
    # Python's json accepts NaN and Infinity by default; they must not reach Decimal money.
    try:
        json.loads(body.decode('utf-8'), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return False
    return True


def _too_large(source_id: str) -> _Failure:
    return _Failure('BLOCKED', source_id, f'reply is over {MAX_RESPONSE_BYTES} bytes', token='blocked:size')


def _certificate_failure(exc: BaseException) -> bool:
    # httpcore keeps the SSL error only as __context__, so walk causes and contexts both.
    seen, pending = set(), [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        seen.add(id(current))
        pending += [current.__cause__, current.__context__]
    return False


def _transport_failure(source_id: str, exc: httpx2.HTTPError) -> _Failure:
    if isinstance(exc, httpx2.TimeoutException):
        return _Failure('TIMEOUT', source_id, type(exc).__name__, retryable=True)
    if _certificate_failure(exc):
        return _Failure('NETWORK_ERROR', source_id, 'TLS certificate verification failed')
    return _Failure('NETWORK_ERROR', source_id, type(exc).__name__,
                    retryable=isinstance(exc, (httpx2.NetworkError, httpx2.RemoteProtocolError)))
