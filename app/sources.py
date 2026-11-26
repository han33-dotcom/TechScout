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
from typing import NamedTuple, Protocol, runtime_checkable
import zlib

import httpcore2
import httpx2

from app.contracts import SourceLimits, SourcePolicy, ToolError, ToolResult
from app.source_capabilities import ACTION_PATH_DENYLIST as ACTION_PATH_DENYLIST
from app.source_capabilities import CapabilityRef, Expect, safe_endpoint
from app.source_registry import DEFAULT_REGISTRY, get_active_source, load_registry

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WIRE_BYTES = MAX_RESPONSE_BYTES + 256 * 1024  # the body cap plus room for headers and framing
MAX_REDIRECTS = 3
PHASE_TIMEOUT_S = 5.0
SOFT_DEADLINE_S = 10.0
CALL_TIMEOUT_S = SOFT_DEADLINE_S + PHASE_TIMEOUT_S
MAX_WAIT_S = 10.0
BACKOFF_S = (1.0, 2.0)
WINDOW_S = 60.0
# A run that asks no store for this long is treated as ended. It is far above the 120-second run
# cap the run budget (MVP 11) enforces, and a finished run's budget refuses every request.
RUN_COUNT_TTL_S = 3600.0
USER_AGENT = 'TechScout/0.1 (+https://github.com/han33-dotcom/TechScout)'
ACCEPT_ENCODING = 'gzip'
HEADERS = {'Accept-Encoding': ACCEPT_ENCODING, 'User-Agent': USER_AGENT}
ACCEPT: dict[Expect, tuple[str, ...]] = {
    'json': ('application/json',),
    'html': ('text/html',),
    'script': ('text/javascript', 'application/javascript', 'application/x-javascript'),
    'xml': ('application/xml', 'text/xml', 'application/rss+xml', 'application/atom+xml'),
}
CONTENT_ENCODINGS = frozenset({'identity', 'gzip'})
MAX_GZIP_LAYERS = 2
INFLATE_STEP_BYTES = 65536
REDIRECTS = frozenset({301, 302, 303, 307, 308})
TRANSIENT_STATUSES = frozenset({502, 503, 504})
# Fixed table, never ipaddress's is_* flags, which change between Python patch releases.
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.0.0.0/24', '192.0.2.0/24', '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
    '2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20',
))
GLOBAL_UNICAST_V6 = ipaddress.ip_network('2000::/3')


class Reservation(Protocol):
    """A reserved call. It may also offer still_wanted() -> bool, asked just before sending:
    False (the run was cancelled or closed meanwhile) drops the request unsent."""

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
class CacheValidators:
    """Validators from one exact final URL, for a subsequent read of that representation."""
    url: str
    etag: str | None
    last_modified: str | None

    def headers(self, url: str) -> dict[str, str]:
        if url != self.url:
            return {}
        headers = {}
        if self.etag and re.fullmatch(r'(?:W/)?"[!#-~]{0,250}"', self.etag):
            headers['If-None-Match'] = self.etag
        if self.last_modified and re.fullmatch(
            r'[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9:]{8} GMT', self.last_modified
        ):
            headers['If-Modified-Since'] = self.last_modified
        return headers


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
    validators: CacheValidators | None = None

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
    source_id: str
    counts: dict[str, int]  # its run's per-store counts, which this request was added to
    planned: float
    wait: float
    queued: bool
    sent: bool = False


class SourceLimiter:
    """Per-source minute window, concurrency, cool-down and per-run counts.

    State is shared by every run that uses the same limiter; gates built without one share a
    process-wide limiter. Concurrency waits block on a condition, never on sleep(), and
    requests in flight count against the store's current limit, even after it is lowered.
    Every request is checked again just before it is sent, against the limits the registry
    gives after any wait. A run's counts are dropped once it has asked no store for RUN_COUNT_TTL_S.
    """

    def __init__(self, concurrency_wait_s: float = 10.0, max_wait_s: float = MAX_WAIT_S,
                 condition_factory=threading.Condition):
        self.concurrency_wait_s = concurrency_wait_s
        self.max_wait_s = max_wait_s
        self._lock = condition_factory()
        self._dispatches: dict[str, list[float]] = {}
        self._used: dict[str, dict[str, int]] = {}  # run id -> store id -> requests counted
        self._last_asked: dict[str, float] = {}  # run id -> its latest request, least recent first
        self._cool_until: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}
        self._latest = float('-inf')

    def _now(self, now: float) -> float:
        # A clock that steps backwards must not expire earlier dispatches early.
        self._latest = max(self._latest, now)
        return self._latest

    def used(self, run_id: str, source_id: str) -> int:
        with self._lock:
            return self._used.get(run_id, {}).get(source_id, 0)

    def cool_down(self, source_id: str, now: float, seconds: float) -> None:
        with self._lock:
            until = self._now(now) + seconds
            self._cool_until[source_id] = max(until, self._cool_until.get(source_id, until))

    def acquire(self, run_id: str, source_id: str, limits: SourceLimits, now: float, wait: float) -> _Slot:
        with self._lock:
            now = self._now(now)
            self._prune(now)
            self._last_asked.pop(run_id, None)
            self._last_asked[run_id] = now
            counts = self._used.setdefault(run_id, {})
            if counts.get(source_id, 0) >= limits.max_requests_per_run:
                raise _Refusal('BUDGET_EXHAUSTED', source_id, 'per-run request cap reached')
            window = sorted(t for t in self._dispatches.get(source_id, ()) if t > now - WINDOW_S)
            wait = max(wait, self._cool_until.get(source_id, now) - now)
            if len(window) >= limits.requests_per_minute:
                wait = max(wait, window[len(window) - limits.requests_per_minute] + WINDOW_S - now)
            if wait > self.max_wait_s:
                raise _Refusal('RATE_LIMIT', source_id, 'per-source rate limit or cool-down in effect',
                               retryable=True)
            queued = self._in_flight.get(source_id, 0) >= limits.concurrency
            slot = _Slot(source_id, counts, now + wait, wait, queued)
            self._dispatches[source_id] = [*window, slot.planned]
            counts[source_id] = counts.get(source_id, 0) + 1
            if not self._lock.wait_for(lambda: self._in_flight.get(source_id, 0) < limits.concurrency,
                                       self.concurrency_wait_s):
                self._forget(slot)
                raise _Refusal('RATE_LIMIT', source_id, 'concurrency limit busy', retryable=True)
            if self._cool_until.get(source_id, now) - now > self.max_wait_s:  # set while it queued
                self._forget(slot)
                raise _Refusal('RATE_LIMIT', source_id, 'store asked for a pause', retryable=True)
            self._in_flight[source_id] = self._in_flight.get(source_id, 0) + 1
        return slot

    def recheck(self, slot: _Slot, limits: SourceLimits, now: float) -> None:
        """Just before sending: refuse if the store asked for a pause or the request no longer fits.

        limits are the ones the registry gives after any wait, and the minute is counted back
        from now, the actual send, so requests sent while this one waited or stalled count too.
        Nothing has been sent, so a refused slot is given back by release() as usual.
        """
        with self._lock:
            # A pause known at acquire ends by slot.planned, so a later one was asked for during the wait.
            if self._cool_until.get(slot.source_id, slot.planned) > slot.planned:
                raise _Refusal('RATE_LIMIT', slot.source_id, 'store asked for a pause', retryable=True)
            # This slot is already counted in each total, and in flight.
            if slot.counts[slot.source_id] > limits.max_requests_per_run:
                raise _Refusal('BUDGET_EXHAUSTED', slot.source_id, 'per-run request cap reached')
            at = max(slot.planned, self._now(now))  # a queued request is sent later than planned
            minute = [t for t in self._dispatches[slot.source_id] if t <= at < t + WINDOW_S]
            if at >= slot.planned + WINDOW_S:  # held so long that its own entry has left that minute
                minute.append(at)
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
        # The counts it was added to, never newer ones started after its run was dropped.
        slot.counts[slot.source_id] -= 1

    def _prune(self, now: float) -> None:
        # Least recently active run first, so each acquire stops at the first run still in use.
        while self._last_asked:
            run_id = next(iter(self._last_asked))
            if now - self._last_asked[run_id] <= RUN_COUNT_TTL_S:
                return
            del self._last_asked[run_id]
            self._used.pop(run_id, None)


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
    content_type: str | None
    content_encoding: str | None
    body: bytes | None
    observed_at: datetime
    validators: CacheValidators | None = None


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

    def check_scope(self, source_id, url, *, expect, capability=None):
        """Read-only preflight; dispatch still repeats all gate checks."""
        try:
            return self._allowed(source_id, url, expect, capability)[1]
        except _Stop as exc:
            raise ValueError('Source target is not admitted') from exc

    def admitted_policy(self, source_id):
        """Return current reviewed policy, without authorizing any network request."""
        try:
            return self._admitted(source_id)[1]
        except _Stop as exc:
            raise ValueError('Source is not admitted') from exc

    def check_provider_target(self, source_id, url, *, expect, capability=None):
        """Managed provider preflight: source scope plus public destination DNS."""
        limits = self.check_scope(source_id,url,expect=expect,capability=capability)
        try:
            self._checked_address(source_id,httpx2.URL(url).host)
        except _Stop as exc:
            raise ValueError('Managed target is not a public destination') from exc
        return limits

    def fetch(self, budget: RunBudget, source_id: str, url: str, *, expect: Expect,
              capability: CapabilityRef | None = None, validators: CacheValidators | None = None) -> FetchOutcome:
        """GET an exact endpoint or the scope named by a current capability reference."""
        if not isinstance(budget, RunBudget):
            raise TypeError('fetch needs the run budget handle')
        if type(source_id) is not str or type(url) is not str:  # a str subclass can fake equality
            raise TypeError('source_id and url must be plain strings')
        if capability is not None and type(capability) is not CapabilityRef:
            raise TypeError('capability must be a CapabilityRef')
        if validators is not None and type(validators) is not CacheValidators:
            raise TypeError('validators must be scoped CacheValidators')
        if expect not in ACCEPT or (expect == 'xml' and capability is None):
            raise ValueError("expect must be 'json' or 'html', or 'xml' with a capability")
        started = self._clock()
        chain, attempts, retries, wait, failure = [url], 0, 0, 0.0, None
        while True:
            try:
                conditional = validators.headers(chain[-1]) if validators else {}
                reply = self._attempt(budget, source_id, chain[-1], expect, wait, capability, conditional)
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
                status_code=304 if reply.body is None else 200, content_type=reply.content_type,
                content_encoding=reply.content_encoding, body=reply.body,
                response_sha256=hashlib.sha256(reply.body).hexdigest() if reply.body is not None else None,
                observed_at=reply.observed_at, validators=reply.validators)
        return FetchOutcome(source_id, url, tuple(chain), attempts, self._duration_ms(started), tuple(errors))

    def _attempt(self, budget: RunBudget, source_id: str, url: str, expect: Expect,
                 wait: float, capability: CapabilityRef | None, conditional: dict[str, str]) -> str | _Reply:
        allowed, limits = self._allowed(source_id, url, expect, capability)
        slot = self._limiter.acquire(budget.run_id, source_id, limits, self._clock(), wait)
        reservation, start = None, None
        try:
            seconds = slot.wait + CALL_TIMEOUT_S
            reserve_resource = getattr(type(budget), 'reserve_resource', None)
            reservation = (reserve_resource(budget, source_id, url, seconds) if reserve_resource
                           else budget.reserve_call(source_id, seconds))
            if reservation is None:
                raise _Refusal('BUDGET_EXHAUSTED', source_id, 'the run budget refused the call')
            if slot.wait:
                self._sleep(slot.wait)
            if slot.wait or slot.queued:  # the store may have been switched off, paused or limited meanwhile
                allowed, limits = self._allowed(source_id, url, expect, capability)
            self._limiter.recheck(slot, limits, self._clock())  # a stall without a wait counts too
            start = self._clock()
            guard = _AttemptGuard(source_id, self._clock, start)
            address = self._checked_address(source_id, httpx2.URL(url).host)
            guard.check()
            still_wanted = getattr(reservation, 'still_wanted', None)
            if still_wanted is not None and not still_wanted():  # cancelled during the wait or lookup
                raise _Refusal('BUDGET_EXHAUSTED', source_id, 'the run budget refused the call')
            self._limiter.mark_sent(slot, self._clock())
            return self._exchange(source_id, url, address, expect, allowed, guard, conditional)
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

    def _allowed(self, source_id: str, url: str, expect: Expect,
                 capability: CapabilityRef | None) -> tuple[Callable[[str], bool], SourceLimits]:
        endpoints, policy = self._admitted(source_id)
        if capability is None:
            allowed = endpoints.__contains__
        else:
            rule = next((rule for rule in policy.endpoint_capabilities
                         if rule.capability_id == capability.capability_id
                         and rule.version == capability.version and rule.expect == expect), None)
            if rule is None:
                raise _Refusal('BLOCKED', source_id, 'capability or response format is not configured',
                               token='blocked:url')
            allowed = rule.matches
        if not allowed(url):
            raise _Refusal('BLOCKED', source_id, 'not an allowed endpoint', token='blocked:url')
        return allowed, policy.limits

    def _admitted(self, source_id: str) -> tuple[list[str], SourcePolicy]:
        """Re-read the registry each attempt; an unselected or unsafe entry disables the source."""
        try:
            entry = get_active_source(source_id, self._registry_path)
            registry = load_registry(self._registry_path)
            # Both reads must show the same entry, so a file edited in between cannot mix versions.
            entries = registry['sources']
            unchanged = (all(isinstance(s, dict) for s in entries)
                         and [s for s in entries if s.get('id') == source_id] == [entry])
            selected = registry['live_release']['admitted_source_ids']
            policy = SourcePolicy.model_validate(entry)
            host = httpx2.URL(entry['origin']).host
            endpoints = entry.get('allowed_endpoints', [])
            if not (unchanged and isinstance(selected, list) and source_id in selected
                    and all(safe_endpoint(u, host) for u in endpoints)):
                raise ValueError('Source is not selected or not safely configured')
        except (OSError, ValueError, LookupError, TypeError, httpx2.InvalidURL) as exc:
            raise _Refusal('SOURCE_DISABLED', source_id, 'source is not admitted') from exc
        return endpoints, policy

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

    def _exchange(self, source_id: str, url: str, address: str, expect: Expect,
                  allowed: Callable[[str], bool], guard: _AttemptGuard, conditional: dict[str, str]) -> str | _Reply:
        # Straight to the transport: a client object would add a cookie jar and parse Location
        # headers itself (raising on some) even when it does not follow redirects.
        request = httpx2.Request('GET', url, headers={**HEADERS, 'Accept': ', '.join(ACCEPT[expect]), **conditional},
                                 extensions={'timeout': httpx2.Timeout(PHASE_TIMEOUT_S).as_dict()})
        with self._build_transport(address, guard) as transport:
            try:
                response = transport.handle_request(request)
                try:
                    return self._reply(source_id, url, response, expect, allowed, guard, conditional)
                finally:
                    response.close()
            except httpx2.HTTPError as exc:
                raise _transport_failure(source_id, exc) from exc

    def _reply(self, source_id: str, url: str, response: httpx2.Response, expect: Expect,
               allowed: Callable[[str], bool], guard: _AttemptGuard, conditional: dict[str, str]) -> str | _Reply:
        status = response.status_code
        if status == 304 and conditional:
            tag = response.headers.get('etag')
            sent = conditional.get('If-None-Match')
            if tag and sent and tag.removeprefix('W/') != sent.removeprefix('W/'):
                raise _Failure('INVALID_SCHEMA', source_id, 'conflicting conditional response')
            return _Reply(None, None, None, self._observed_now())
        if status in REDIRECTS:
            try:
                target = str(httpx2.URL(url).join(response.headers['location']))
            except (KeyError, httpx2.InvalidURL):
                target = None
            if target is None or not allowed(target):
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
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if content_type not in ACCEPT[expect]:
            raise _Failure('INVALID_SCHEMA', source_id, f'content type is not {", ".join(ACCEPT[expect])}')
        body = self._read_body(source_id, response, layers, declared, guard)
        if not body or (expect == 'json' and not _strict_json(body)):
            raise _Failure('INVALID_SCHEMA', source_id, f'empty or malformed {expect} body')
        # The checked media type only: the header's parameters are store text the planner would see.
        cached = CacheValidators(url, response.headers.get('etag'), response.headers.get('last-modified'))
        safe = cached.headers(url)
        validators = CacheValidators(url, safe.get('If-None-Match'), safe.get('If-Modified-Since')) if safe else None
        return _Reply(content_type, ', '.join(encodings) or None, body, self._observed_now(), validators)

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
