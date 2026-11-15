"""Run control (MVP 11): one shopping search, from request to ranked shortlist, within its caps.

The controller runs the stages in order (clarify, retrieve, assess, investigate, verify, results),
holds the run budget that every gate request reserves against, and traces each step. Fixed rules
decide eligibility and cost; the planner only picks the next evidence action from a menu built
here from open gaps and what still fits, and it never sees seller text.

A run is 'partial' when the run budget (time, external calls, spend) or the planner turn cap
stopped it while an affordable action could still settle an open gap; a store's own per-run
request cap only removes actions from the menu. A planner turn whose cost became unknown also
ends the run as partial. Planner time counts against the time budget: a turn starts only when
the planner's longest call and an action both fit before the verification reserve, and the
chosen action is checked again just before it runs. A run in which no store could be searched
(every search failed, or none could run) is an 'error' run, never an ordinary abstention.
'cancelled' and 'error' runs never carry recommendations. A run interrupted by a crash is closed
as partial, never resumed, by the next web-app start or command-line search once it is older
than the recovery lease (app.storage).
"""
import argparse
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import math
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from pydantic import ValidationError

from app.adapters.base import AdapterResult, CandidateQuery, PolicyDocument, StoreAdapter
from app.assessment import Assessment, EvidenceGap
from app.contracts import (
    BudgetUsage, Money, Offer, OfferIssue, Recommendation, RunResult, ShoppingRequest, SourceCoverage,
    SourcePolicy, ToolError, ToolResult,
)
from app.planner import (
    DEFAULT_CONFIG, NoInvestigationPlanner, Planner, PlannerAction, PlannerDecision, PlannerState, RulePlanner,
    request_constraints,
)
from app.source_registry import DEFAULT_REGISTRY, get_active_source
from app.sources import CALL_TIMEOUT_S, SourceGate
from app.storage import RUN_LEASE, Storage
from app.tracing import Tracer

log = logging.getLogger('techscout.runs')

USD = 'USD'
_ZERO = Decimal('0')
SEARCH_CALLS = 2  # one product per store today: its Store API record, then its page
OFFER_CALLS = 2
POLICY_CALLS = 1
# What a fresh product read can settle. It never shows branch stock, so pickup gaps get no re-read.
OFFER_GAP_FIELDS = frozenset({'price', 'online_stock', 'purchasable'})
MAX_RECOMMENDATIONS = 3
# A run's time cap stays well inside the recovery lease, so a request or planner call in flight at
# the cap, a slow lookup or a slow database write never makes a live run look abandoned.
MAX_RUN_SECONDS = RUN_LEASE.total_seconds() / 2

# Fixed stop reasons; RunResult.stop_reason is always one of these.
STOP_NO_GAPS = 'no open evidence gaps remain'
STOP_NO_ACTION = 'no available action can settle the open gaps'
STOP_GAPS_WITHHELD = 'open gaps could not be shown to the planner safely'
STOP_STORE_CAPS = 'store request caps leave no action for the open gaps'
STOP_PLANNER = 'the planner chose to stop'
STOP_NO_INVESTIGATION = 'investigation is disabled for this run'
STOP_TURNS = 'planner turn cap reached'
STOP_CALLS = 'external call budget reached'
STOP_TIME = 'time budget reached'
STOP_SPEND = 'spend budget reached'
STOP_SPEND_UNKNOWN = 'planner cost is unknown'
STOP_CANCELLED = 'run was cancelled'
STOP_ERROR = 'run stopped on an unexpected error'
STOP_NO_SOURCES = 'no admitted source has a reader'
STOP_NO_STORE_READ = 'no store could be searched'
PARTIAL_STOPS = frozenset({STOP_TURNS, STOP_CALLS, STOP_TIME, STOP_SPEND, STOP_SPEND_UNKNOWN})

# Fixed reasons for offers held back from the shortlist.
REASON_STALE = 'evidence was not observed during this run'
REASON_CURRENCY = 'total is not in the budget currency'
REASON_UNKNOWN_CHARGE = 'offer lists a charge with unknown terms'
REASON_CANCELLED = 'not verified: the run was cancelled'
REASON_ERROR = 'not verified: the run stopped on an error'

_LIMIT_STOPS = {'calls': STOP_CALLS, 'time': STOP_TIME, 'spend': STOP_SPEND,
                'spend_unknown': STOP_SPEND_UNKNOWN, 'turns': STOP_TURNS}
_GATE_LIMITS = ('calls', 'time')  # refusals the gate can meet inside a store reader call
_NOT_SEARCHED = {
    'cancelled': 'not searched; the run was cancelled',
    'calls': 'not searched; the run call budget has no room',
    'time': 'not searched; the run time budget has no room',
    'store': 'not searched; the store request cap has no room',
    'error': 'not searched; the run stopped on an error',
}
# What may reach the planner: plain identifiers (the planner's own check) and short printable
# reasons, never free text. A gap that fails is withheld, so it cannot spoil a whole turn's state;
# when only withheld gaps remain, investigation ends with STOP_GAPS_WITHHELD. Offer IDs must be
# plain source:product:variant IDs as the store readers build them (app.adapters.base.offer_id);
# the contract allows any text, so gaps on offers with other IDs are always withheld.
_PLAIN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}')
_OFFER_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}){2}')
MAX_GAP_REASON = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _check_seconds(name: str, value) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0):
        raise ValueError(f'{name} must be a finite, non-negative number of seconds')


def _check_spend(value) -> None:
    if not isinstance(value, Decimal):
        raise TypeError('Spend amounts are Decimal, never float')
    if not value.is_finite() or value < 0:
        raise ValueError('Spend amounts must be finite and non-negative')


def _config_money(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Spend caps are decimal strings, never floats')
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Spend caps are decimal strings') from exc


@dataclass(frozen=True)
class RunCaps:
    """One run's limits (issue #16 defaults; calibrate them with the evaluation runs).

    Each reserve is held back for final verification and is part of its total, never extra.
    planner_turns=0 turns investigation off (the no-investigation baseline).
    """
    seconds: float = 120.0
    external_calls: int = 40
    planner_turns: int = 6
    spend: Decimal = Decimal('1.00')
    reserve_seconds: float = 30.0
    reserve_calls: int = 10
    reserve_spend: Decimal = Decimal('0.25')

    def __post_init__(self):
        for name in ('seconds', 'reserve_seconds'):
            _check_seconds(name, getattr(self, name))
        for name in ('external_calls', 'planner_turns', 'reserve_calls'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f'{name} must be a non-negative integer')
        for name in ('spend', 'reserve_spend'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f'{name} must be a finite, non-negative Decimal')
        if self.reserve_seconds >= self.seconds or self.reserve_calls >= self.external_calls:
            raise ValueError('The reserves must leave time and calls before the final phase')
        if self.seconds > MAX_RUN_SECONDS:
            raise ValueError(f'A run may last at most {MAX_RUN_SECONDS:g} seconds, '
                             'well inside the recovery lease')
        if self.reserve_spend > self.spend:
            raise ValueError('The spend reserve is part of the spend cap')

    @classmethod
    def from_config(cls, caps: Mapping) -> 'RunCaps':
        """The 'caps' section of config/evaluation.json; amounts are decimal strings, never floats."""
        try:
            return cls(seconds=caps['seconds'], external_calls=caps['external_calls'],
                       planner_turns=caps['planner_turns'], spend=_config_money(caps['spend_usd']),
                       reserve_seconds=caps['reserve_seconds'], reserve_calls=caps['reserve_calls'],
                       reserve_spend=_config_money(caps['reserve_spend_usd']))
        except KeyError as exc:
            raise ValueError(f'The caps section is missing {exc.args[0]}') from exc


class CallReservation:
    """One external call the gate may send: commit after sending, release if nothing was sent."""
    __slots__ = ('_budget', 'source_id', 'settled')

    def __init__(self, budget: 'RunBudget', source_id: str):
        self._budget, self.source_id, self.settled = budget, source_id, False

    def still_wanted(self) -> bool:
        """Asked by the gate just before sending, after any wait: False once the run was
        cancelled or closed, so a request that was only waiting is dropped unsent."""
        return self._budget._still_open()

    def commit(self, elapsed_seconds: float) -> None:
        _check_seconds('elapsed_seconds', elapsed_seconds)
        self._budget._settle_call(self, float(elapsed_seconds))

    def release(self) -> None:
        self._budget._settle_call(self, None)


class SpendReservation:
    """A conservative charge held before a paid call: commit the actual cost, or release it."""
    __slots__ = ('_budget', 'amount', 'settled')

    def __init__(self, budget: 'RunBudget', amount: Decimal):
        self._budget, self.amount, self.settled = budget, amount, False

    def commit(self, actual: Decimal | None) -> None:
        """Record what the call cost. More than reserved, or None (unknown), is recorded too and
        stops further spend in this run."""
        if actual is not None:
            _check_spend(actual)
        self._budget._settle_spend(self, actual, spent=True)

    def release(self) -> None:
        """Nothing was spent: the paid call was not made."""
        self._budget._settle_spend(self, None, spent=False)


class RunBudget:
    """One run's totals; it satisfies app.sources.RunBudget, which the gate reserves per attempt.

    A call is counted when reserved (conservatively), kept by commit and given back by release
    when nothing was sent. Before enter_final_phase() the verification reserves are held back,
    so they are never extra budget. Once cancelled or closed, every reservation and planner turn
    is refused. Thread-safe: the gate may reserve from several threads.

    on_usage, if given, is called (without the lock) whenever the totals grow: after each granted
    call reservation, spend reservation and planner turn, before anything is sent or spent, and
    when a recorded cost exceeds its reservation. The run controller writes the totals to storage
    there, so a crash never loses the usage in flight. If it raises, the reservation or turn it
    was told about is given back and the error propagates.
    """

    def __init__(self, run_id: str, caps: RunCaps = RunCaps(), clock: Callable[[], float] = time.monotonic, *,
                 cancel_signal: Callable[[], bool] | None = None, on_usage: Callable[[], None] | None = None):
        if type(run_id) is not str or not run_id.strip():
            raise ValueError('A run budget needs a run ID')
        if not isinstance(caps, RunCaps):
            raise TypeError('caps must be RunCaps')
        self.run_id, self.caps = run_id, caps
        self._clock, self._signal, self._on_usage = clock, cancel_signal, on_usage
        self._lock = threading.Lock()
        self._start = clock()
        self._final = self._cancelled = self._closed = False
        self._calls, self._by_source, self._call_seconds = 0, {}, 0.0
        self._turns = 0
        self._spent = self._held = _ZERO
        self._spend_blocked = self._spend_unknown = False
        self._refusals: dict[str, int] = {}

    # Calls

    def reserve_call(self, source_id: str, max_seconds: float) -> CallReservation | None:
        """Count one external call now; None when it does not fit or the run has ended.

        It must fit the call cap and the deadline (now + max_seconds); before the final phase
        both exclude the verification reserve. The gate asks once per attempt, so retries count.
        """
        if type(source_id) is not str:
            raise TypeError('source_id must be a plain string')
        _check_seconds('max_seconds', max_seconds)
        self._poll_signal()
        with self._lock:
            calls, deadline, _ = self._limits()
            reason = self._ended() or ('calls' if self._calls + 1 > calls
                                       else 'time' if self._clock() + max_seconds > deadline else None)
            if reason:
                self._refuse(reason)
                return None
            self._calls += 1
            self._by_source[source_id] = self._by_source.get(source_id, 0) + 1
            reservation = CallReservation(self, source_id)
        self._grew(reservation.release)
        return reservation

    def _settle_call(self, reservation: CallReservation, elapsed: float | None) -> None:
        with self._lock:
            if reservation.settled:
                raise RuntimeError('A call reservation is settled once')
            reservation.settled = True
            if elapsed is None:  # released: nothing was sent
                self._calls -= 1
                self._by_source[reservation.source_id] -= 1
            else:
                self._call_seconds += elapsed

    def shortfall(self, calls: int, seconds: float) -> str | None:
        """Why `calls` more external calls, taking up to `seconds` in all, would not fit now
        ('cancelled', 'closed', 'calls' or 'time'); None when they fit. Nothing is reserved."""
        self._poll_signal()
        with self._lock:
            limit, deadline, _ = self._limits()
            return self._ended() or ('calls' if self._calls + calls > limit
                                     else 'time' if self._clock() + seconds > deadline else None)

    # Spend

    def reserve_spend(self, amount: Decimal | None) -> SpendReservation | None:
        """Hold a conservative charge before a paid call. None (refused) when the amount is unknown,
        it does not fit, an earlier cost overran or was unknown, or the run has ended. A refusal
        after an unknown cost is counted as 'spend_unknown', not as 'spend'."""
        if amount is not None:
            _check_spend(amount)
        self._poll_signal()
        with self._lock:
            _, _, limit = self._limits()
            reason = self._ended() or (
                'spend_unknown' if amount is None or self._spend_unknown
                else 'spend' if self._spend_blocked or self._spent + self._held + amount > limit else None)
            if reason:
                self._refuse(reason)
                return None
            self._held += amount
            reservation = SpendReservation(self, amount)
        self._grew(reservation.release)
        return reservation

    def _settle_spend(self, reservation: SpendReservation, actual: Decimal | None, *, spent: bool) -> None:
        with self._lock:
            if reservation.settled:
                raise RuntimeError('A spend reservation is settled once')
            reservation.settled = True
            self._held -= reservation.amount
            if not spent:
                return
            overran = actual is not None and actual > reservation.amount
            if actual is None:
                self._spend_unknown = self._spend_blocked = True
                actual = reservation.amount  # the most that is known; further spend stops
            elif overran:
                self._spend_blocked = True  # recorded anyway, but the estimates can no longer be trusted
            self._spent += actual
        if overran:
            self._grew()

    def record_unreserved_spend(self, amount: Decimal | None) -> None:
        """Record spend reported without a reservation (None: an unknown cost). The caps could not
        be checked first, so further spend stops."""
        if amount is not None:
            _check_spend(amount)
        with self._lock:
            self._spend_blocked = True
            if amount is None:
                self._spend_unknown = True
            else:
                self._spent += amount
        if amount:
            self._grew()

    # Turns, phases and the end of the run

    def count_planner_turn(self) -> bool:
        """Count one planner turn; False, counting nothing, at the turn cap or once the run has ended."""
        self._poll_signal()
        with self._lock:
            reason = self._ended() or ('turns' if self._turns >= self.caps.planner_turns else None)
            if reason:
                self._refuse(reason)
                return False
            self._turns += 1
        self._grew(self._uncount_turn)
        return True

    def enter_final_phase(self) -> None:
        """The verification reserves become usable; the totals stay the caps."""
        with self._lock:
            self._final = True

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def close(self) -> None:
        """The run is over: refuse whatever a leftover handle still asks for."""
        with self._lock:
            self._closed = True

    @property
    def final_phase(self) -> bool:
        return self._final

    @property
    def cancelled(self) -> bool:
        self._poll_signal()
        return self._cancelled

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def call_seconds(self) -> float:
        """Seconds the gate reported for the calls it sent."""
        return self._call_seconds

    @property
    def spend_unknown(self) -> bool:
        """True when some recorded spend had an unknown cost, so the spend total is a lower bound."""
        return self._spend_unknown

    @property
    def refusals(self) -> dict[str, int]:
        """Refused requests by reason, in the order each reason first occurred."""
        with self._lock:
            return dict(self._refusals)

    # Reading the totals

    def calls_used(self, source_id: str | None = None) -> int:
        """External calls counted so far (reserved or sent), for one store or the whole run."""
        with self._lock:
            return self._calls if source_id is None else self._by_source.get(source_id, 0)

    def turns_used(self) -> int:
        return self._turns

    def spend_total(self) -> Decimal:
        """Spend recorded plus spend still reserved."""
        with self._lock:
            return self._spent + self._held

    def usage(self) -> BudgetUsage:
        """Totals so far, in USD. Calls and spend still reserved count as used: they may have been
        sent or spent. Elapsed time is the run clock's, which the caps use."""
        with self._lock:
            return BudgetUsage(elapsed_ms=_ms(self._clock() - self._start), external_calls=self._calls,
                               planner_turns=self._turns,
                               spend=Money(amount=self._spent + self._held, currency=USD))

    def remaining(self) -> dict[str, int | str]:
        """What is left before the verification reserve: counters only, safe to show the planner."""
        with self._lock:
            calls, deadline, limit = self._limits()
            spend = _ZERO if self._spend_blocked else max(_ZERO, limit - self._spent - self._held)
            return {'external_calls': max(0, calls - self._calls),
                    'seconds': max(0, math.floor(deadline - self._clock())),
                    'planner_turns': max(0, self.caps.planner_turns - self._turns),
                    'spend_usd': str(spend)}

    # Internals; the caller holds the lock unless noted.

    def _poll_signal(self) -> None:
        # Called without the lock, so a caller's signal may take its own locks.
        if self._signal is not None and not self._cancelled and self._signal():
            with self._lock:
                self._cancelled = True

    def _ended(self) -> str | None:
        return 'closed' if self._closed else 'cancelled' if self._cancelled else None

    def _grew(self, undo: Callable[[], None] | None = None) -> None:
        # Called without the lock: the hook reads the totals.
        if self._on_usage is None:
            return
        try:
            self._on_usage()
        except BaseException:
            if undo is not None:
                undo()
            raise

    def _uncount_turn(self) -> None:
        with self._lock:
            self._turns -= 1

    def _still_open(self) -> bool:
        # Called without the lock, like the public methods: it polls the cancel signal first.
        self._poll_signal()
        with self._lock:
            reason = self._ended()
            if reason:
                self._refuse(reason)
            return reason is None

    def _refuse(self, reason: str) -> None:
        self._refusals[reason] = self._refusals.get(reason, 0) + 1

    def _limits(self) -> tuple[int, float, Decimal]:
        caps = self.caps
        if self._final:
            return caps.external_calls, self._start + caps.seconds, caps.spend
        return (caps.external_calls - caps.reserve_calls, self._start + caps.seconds - caps.reserve_seconds,
                caps.spend - caps.reserve_spend)


def default_assess(request: ShoppingRequest, offers: Sequence[Offer], *,
                   now: datetime | None = None) -> list[tuple[Offer, Assessment]]:
    """Deduplicate, then check each offer's eligibility and cost with the fixed rules (MVP 07-09)."""
    # Imported here: the rules are separate tasks, and the controller's tests inject stand-ins.
    from app.costs import calculate_cost
    from app.eligibility import check_eligibility
    from app.identity import deduplicate, normalize_variant
    pairs = []
    for offer in deduplicate(offers).offers:
        eligibility = check_eligibility(request, offer, normalized=normalize_variant(offer))
        pairs.append((offer, Assessment(offer_id=offer.offer_id, eligibility=eligibility,
                                        cost=calculate_cost(request, offer, now=now))))
    return pairs


Assess = Callable[[ShoppingRequest, Sequence[Offer]], Sequence[tuple[Offer, Assessment]]]


@dataclass(frozen=True)
class RunDependencies:
    """What one run needs. assess=None uses the fixed rules; planner=None the RulePlanner baseline."""
    gate: SourceGate
    storage: Storage
    adapters: Mapping[str, StoreAdapter]
    planner: Planner | None = None
    assess: Assess | None = None
    registry_path: Path | str = DEFAULT_REGISTRY
    wall_clock: Callable[[], datetime] = _utc_now
    clock: Callable[[], float] = time.monotonic


def default_dependencies(storage: Storage | None = None, *, planner: Planner | None = None,
                         registry_path: Path | str = DEFAULT_REGISTRY) -> RunDependencies:
    """The production wiring: the process's gate, the admitted stores' readers and the database."""
    from app.adapters import ADAPTERS  # imported here so this module loads without the readers
    return RunDependencies(gate=SourceGate(registry_path), storage=storage or Storage(), adapters=ADAPTERS,
                           planner=planner, registry_path=registry_path)


def new_run_id() -> str:
    return f'run-{uuid.uuid4().hex}'


def run_search(request: ShoppingRequest, deps: RunDependencies, *, run_id: str | None = None,
               caps: RunCaps = RunCaps(), cancel=None) -> RunResult:
    """Run one search and store its RunResult.

    cancel is a threading.Event or a callable returning True once the buyer cancels; it is
    checked before every step, when each gate request is reserved and again just before it is
    sent, so a request still waiting out a backoff is dropped. An unexpected exception ends the run as
    'error' (traced, no recommendations). Exceptions before the run is stored (an unknown request
    type, a planner without a valid max_call_seconds, name or JSON config, a duplicate run ID, or a
    reader registered under another store's ID) are raised. So is a storage failure after it is
    stored, such as the run having been closed by another process; nothing more is sent, and the
    run is left for recovery or already closed.
    """
    if not isinstance(request, ShoppingRequest):
        raise TypeError('run_search needs a ShoppingRequest')
    return _Run(request, deps, run_id or new_run_id(), caps, cancel).execute()


def _cancel_signal(cancel) -> Callable[[], bool] | None:
    if cancel is None:
        return None
    if callable(getattr(cancel, 'is_set', None)):
        return cancel.is_set
    if callable(cancel):
        return lambda: bool(cancel())
    raise TypeError('cancel is a threading.Event or a callable returning a bool')


def _planner_record(planner) -> dict:
    """The planner's name and settings as JSON, recorded once with each run. Planner configs hold
    no credentials (the model planner's holds its validated settings and price table)."""
    name, config = getattr(planner, 'name', None), getattr(planner, 'config', None)
    if type(name) is not str or not name.strip() or not isinstance(config, Mapping):
        raise TypeError('A planner has a name and a config mapping')
    try:
        config = json.loads(json.dumps(config, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError('A planner config holds JSON values only') from exc
    return {'name': name, 'config': config}


def _policy_topics(adapter, mode: str) -> dict[str, tuple[str, ...]]:
    """The policy pages a store offers for this run's route, each with the paths it may settle.

    A reader that knows some pages speak only to delivery orders (policy_topics_for) leaves those
    paths out for pickup, so a pickup run never spends requests on a page that cannot settle them.
    """
    topics_for = getattr(adapter, 'policy_topics_for', None)
    return topics_for(mode) if callable(topics_for) else adapter.policy_topics


def _covers(settles: str, field_path: str) -> bool:
    """A policy settling 'warranty' covers a gap on 'warranty.months', and one settling
    'warranty.months' covers a gap on 'warranty'."""
    return (field_path == settles or field_path.startswith(settles + '.')
            or settles.startswith(field_path + '.'))


def _safe_gap(gap: EvidenceGap) -> bool:
    return (_OFFER_ID.fullmatch(gap.offer_id) is not None and _PLAIN.fullmatch(gap.source_id) is not None
            and _PLAIN.fullmatch(gap.field_path) is not None and len(gap.reason) <= MAX_GAP_REASON
            and gap.reason.isprintable())


def _with_error(result: ToolResult, error: ToolError, produced: bool) -> ToolResult:
    return ToolResult(status='partial' if produced else 'error', data=result.data,
                      evidence_ids=result.evidence_ids, errors=[*result.errors, error],
                      duration_ms=result.duration_ms)


@dataclass
class _Coverage:
    source_id: str
    candidates: int = 0
    search_failed: bool = False  # the search produced nothing usable, or never ran
    errors: list[ToolError] = field(default_factory=list)

    @classmethod
    def not_searched(cls, source_id: str, reason: str) -> '_Coverage':
        # The code the gate itself gives a request the run budget refuses.
        error = ToolError(code='BUDGET_EXHAUSTED', message=f'{source_id[:60]}: {_NOT_SEARCHED[reason]}',
                          retryable=False)
        return cls(source_id, search_failed=True, errors=[error])

    def contract(self) -> SourceCoverage:
        status = 'ok' if not self.errors else 'error' if self.search_failed else 'partial'
        return SourceCoverage(source_id=self.source_id, status=status, candidates_seen=self.candidates,
                              errors=list(self.errors))


class _SourceFetch:
    """A reader's Fetch: the gate bound to this run's budget and to the reader's own store. Each
    reply it returns is noted (body hash, observed_at), so evidence can be matched to this run."""

    def __init__(self, gate, budget: RunBudget, source_id: str, observed: set):
        self._gate, self._budget, self._source_id, self._observed = gate, budget, source_id, observed

    def __call__(self, source_id: str, url: str, *, expect: str):
        if source_id != self._source_id:
            raise ValueError('A store reader may only fetch from its own store')
        outcome = self._gate.fetch(self._budget, source_id, url, expect=expect)
        if outcome.response_sha256 is not None and outcome.observed_at is not None:
            self._observed.add((outcome.response_sha256, outcome.observed_at))
        return outcome


class _SpendView:
    """The planner's only handle on the budget: spend reservations."""
    __slots__ = ('_budget',)

    def __init__(self, budget: RunBudget):
        self._budget = budget

    def reserve_spend(self, amount: Decimal | None) -> SpendReservation | None:
        return self._budget.reserve_spend(amount)


def _apply(adapter: StoreAdapter, offer: Offer, policies: Sequence[PolicyDocument]) -> Offer:
    for policy in policies:
        updated = adapter.apply_policy(offer, policy)
        if not isinstance(updated, Offer) or updated.offer_id != offer.offer_id:
            raise ValueError('apply_policy must return the same offer')
        offer = updated
    return offer


class _Run:
    """The state of one run; run_search is its public entry point."""

    def __init__(self, request: ShoppingRequest, deps: RunDependencies, run_id: str, caps: RunCaps, cancel):
        self.request, self.deps, self.run_id, self.caps = request, deps, run_id, caps
        self.tracer = Tracer(deps.storage, run_id)
        self.budget = RunBudget(run_id, caps, deps.clock, cancel_signal=_cancel_signal(cancel),
                                on_usage=self._write_ahead)
        self.query = CandidateQuery.from_request(request)
        self.planner = deps.planner if deps.planner is not None else RulePlanner()
        # Charged against the time budget before every turn (see _menu).
        self.planner_seconds = getattr(self.planner, 'max_call_seconds', None)
        _check_seconds('planner max_call_seconds', self.planner_seconds)
        self.planner_record = _planner_record(self.planner)  # stored once, with the clarify stage
        self.assess_fn = deps.assess or (lambda req, offers: default_assess(req, offers, now=self._now()))
        self.planner_view = request_constraints(request)
        self.sources: list[str] = []
        self.offers: list[Offer] = []
        self.assessed: list[tuple[Offer, Assessment]] = []
        self.coverage: dict[str, _Coverage] = {}
        self.policies: dict[str, list[PolicyDocument]] = {}
        self.attempted: set[tuple[str, str, str]] = set()  # (tool, store, target) tried this run
        self.stored_bodies: set[str] = set()
        self.observed: set[tuple[str, datetime]] = set()  # (body hash, observed_at) of this run's replies
        self.saved: dict[str, Offer] = {}
        self.withheld: dict[str, list[str]] = {}
        self.history: list[dict] = []
        self.limit: str | None = None  # the first run limit that stopped work early
        self.stop_reason: str | None = None  # why investigation ended
        self.withheld_gaps = 0  # open gaps last held back from the planner (_open_gaps)
        self.stage = 'clarify'
        self.tool: dict[str, str] | None = None  # the store tool in progress, named in a failure record
        self.started_at: datetime | None = None

    def execute(self) -> RunResult:
        try:
            self.started_at = self._now()
            self.sources = self._select_sources()
            self.deps.storage.start_run(self.run_id, self.request, started_at=self.started_at)
            try:
                return self._pipeline()
            except Exception as exc:
                return self._fail(exc)
        finally:
            self.budget.close()  # nothing may run under this budget once the run is over

    def _pipeline(self) -> RunResult:
        with self._stage('clarify') as data:
            data['sources'] = list(self.sources)
            data['planner'] = self.planner_record
        if not self.sources:
            return self._finish(error=STOP_NO_SOURCES)
        with self._stage('retrieve') as data:
            self._retrieve(data)
        with self._stage('assess') as data:
            self._reassess()
            data.update(self._counts())
        with self._stage('investigate') as data:
            self._investigate()
            data.update(turns=self.budget.turns_used(), stop_reason=self.stop_reason,
                        withheld_gaps=self.withheld_gaps)
        with self._stage('verify') as data:
            finalists = self._verify(data)
        return self._finish(finalists)

    @contextmanager
    def _stage(self, name: str):
        # Recorded when the stage completes, after its tool calls; a failed stage is reported by results.
        self.stage = name
        started, data = self.deps.clock(), {}
        yield data
        self.tracer.stage(name, duration_ms=_ms(self.deps.clock() - started), data=data)

    # Clarify and retrieve

    def _select_sources(self) -> list[str]:
        """Admitted, active stores that have a reader, in registry order; registered in storage."""
        try:
            registry = json.loads(Path(self.deps.registry_path).read_text(encoding='utf-8'))
            admitted = registry['live_release']['admitted_source_ids']
        except (OSError, ValueError, LookupError, TypeError):
            return []
        selected = []
        for source_id in admitted if isinstance(admitted, list) else ():
            if not isinstance(source_id, str) or source_id in selected or source_id not in self.deps.adapters:
                continue
            if getattr(self.deps.adapters[source_id], 'source_id', source_id) != source_id:
                raise ValueError('A store reader is registered under another store ID')
            try:
                policy = SourcePolicy.model_validate(get_active_source(source_id, self.deps.registry_path))
            except (PermissionError, ValueError):
                continue
            if self.deps.storage.load_source(source_id) is None:
                self.deps.storage.save_source(policy)
            selected.append(source_id)
        return selected

    def _retrieve(self, data: dict) -> None:
        rooms, seen, skipped = self._rooms(), {}, {}
        for source_id in self.sources:
            reason = self._blocked(SEARCH_CALLS, rooms[source_id])
            if reason is not None:
                if reason in _GATE_LIMITS:
                    self._hit(reason)
                skipped[source_id] = reason
                self.coverage[source_id] = _Coverage.not_searched(source_id, reason)
                continue
            adapter = self.deps.adapters[source_id]
            result, offers, policies = self._run_tool(
                source_id, 'search_candidates', {'source_id': source_id, 'query': self._query_arguments()},
                lambda fetch: adapter.search_candidates(fetch, self.query))
            self.coverage[source_id] = _Coverage(source_id, candidates=len(offers),
                                                 search_failed=result.status == 'error',
                                                 errors=list(result.errors))
            seen[source_id] = len(offers)
            self._absorb(source_id, offers, policies)
            self.tool = None
        data.update(candidates=seen, skipped=skipped)

    def _query_arguments(self) -> dict:
        query, place = self.query, self.query.fulfillment
        return {'manufacturer': query.manufacturer[:60], 'model': query.model[:60],
                'storage_gb': query.storage_gb, 'mode': place.mode, 'city': place.city[:60],
                'branch_id': None if place.branch_id is None else place.branch_id[:60]}

    def _run_tool(self, source_id: str, tool: str, arguments: dict, invoke):
        """Call one store reader through a fetch bound to its store, keep what it returned, trace it.
        The caller clears self.tool once it has absorbed the results."""
        self.tool = {'tool': tool, 'source_id': source_id}
        before = self.budget.calls_used(source_id)
        result = invoke(_SourceFetch(self.deps.gate, self.budget, source_id, self.observed))
        if not isinstance(result, AdapterResult):
            raise TypeError('Store readers return an AdapterResult')
        offers, policies, dropped = self._keep(source_id, result)
        tool_result = result.tool_result
        if dropped:
            error = ToolError(code='INVALID_SCHEMA', retryable=False, message=(
                f'{source_id[:60]}: {dropped} result(s) cite replies that were not stored'))
            tool_result = _with_error(tool_result, error, produced=bool(offers or policies))
        self.tracer.tool(tool, tool_result, source_id=source_id, arguments=arguments,
                         external_calls=self.budget.calls_used(source_id) - before)
        return tool_result, offers, policies

    def _keep(self, source_id: str, result: AdapterResult):
        """Store every returned body first; keep only results whose evidence cites a stored body."""
        for snapshot in result.snapshots:
            self.stored_bodies.add(self.deps.storage.save_snapshot(snapshot.body, snapshot.media_type))
        offers = [o for o in result.offers if o.source_id == source_id
                  and all(e.response_sha256 in self.stored_bodies for e in o.evidence)]
        policies = [p for p in result.policies if p.source_id == source_id
                    and p.response_sha256 in self.stored_bodies]
        dropped = len(result.offers) - len(offers) + len(result.policies) - len(policies)
        return offers, policies, dropped

    def _absorb(self, source_id: str, offers: Sequence[Offer], policies: Sequence[PolicyDocument]) -> None:
        """Add new observations; every store policy read in this run applies to that store's offers."""
        adapter = self.deps.adapters[source_id]
        known = self.policies.setdefault(source_id, [])
        if policies:
            self.offers = [_apply(adapter, o, policies) if o.source_id == source_id else o
                           for o in self.offers]
            known.extend(policies)
            self.attempted.update(('fetch_policy', source_id, p.policy_id) for p in policies)
        self.offers.extend(_apply(adapter, o, known) for o in offers)
        self._persist(self.offers)

    def _persist(self, offers: Sequence[Offer]) -> None:
        for offer in offers:
            if self.saved.get(offer.offer_id) != offer:
                self.deps.storage.save_offer(offer)
                self.saved[offer.offer_id] = offer

    # Assess

    def _assess(self, offers: Sequence[Offer]) -> list[tuple[Offer, Assessment]]:
        unique = {}
        for offer, assessment in self.assess_fn(self.request, list(offers)):
            if (not isinstance(offer, Offer) or not isinstance(assessment, Assessment)
                    or assessment.offer_id != offer.offer_id):
                raise ValueError('Each assessment must cover its own offer')
            unique[offer.offer_id] = (offer, assessment)  # the latest observation of an offer wins
        return list(unique.values())

    def _reassess(self) -> None:
        self.assessed = self._assess(self.offers)
        self.offers = [offer for offer, _ in self.assessed]
        self._persist(self.offers)

    def _counts(self) -> dict:
        decisions = [assessment.decision for _, assessment in self.assessed]
        counts = {d: decisions.count(d) for d in ('recommendable', 'unresolved', 'rejected')}
        return {'offers': len(decisions), **counts}

    # Investigate

    def _investigate(self) -> None:
        if self.caps.planner_turns == 0:
            self.stop_reason = STOP_NO_INVESTIGATION
            return
        while True:
            gaps, withheld = self._open_gaps()
            self.withheld_gaps = withheld
            if not gaps:  # gaps held back for their shape get no action, so they end investigation
                self.stop_reason = (STOP_CANCELLED if self._cancelled()
                                    else STOP_GAPS_WITHHELD if withheld else STOP_NO_GAPS)
                return
            menu, shortfall, any_candidate = self._menu(gaps)
            if not menu:
                if shortfall == 'cancelled':
                    self.stop_reason = STOP_CANCELLED
                elif shortfall in _GATE_LIMITS:
                    self._hit(shortfall)
                    self.stop_reason = _LIMIT_STOPS[shortfall]
                else:
                    self.stop_reason = STOP_STORE_CAPS if any_candidate else STOP_NO_ACTION
                return
            if not self.budget.count_planner_turn():
                if self._cancelled():
                    self.stop_reason = STOP_CANCELLED
                else:
                    self._hit('turns')
                    self.stop_reason = STOP_TURNS
                return
            if not self._turn(gaps, withheld, menu):
                return

    def _turn(self, gaps: list[EvidenceGap], withheld: int, menu: list[PlannerAction]) -> bool:
        """One counted planner turn; False when investigation stops."""
        turn = self.budget.turns_used()
        state = PlannerState(request=self.planner_view, gaps=gaps, actions=menu, turn=turn,
                             max_turns=self.caps.planner_turns, remaining=self.budget.remaining(),
                             history=[dict(entry) for entry in self.history])
        spend_before, refusals_before = self.budget.spend_total(), self.budget.refusals
        unknown_before = self.budget.spend_unknown
        started = self.deps.clock()
        decision = self.planner.decide(state, _SpendView(self.budget))
        duration = _ms(self.deps.clock() - started)
        if not isinstance(decision, PlannerDecision):
            raise TypeError('A planner returns a PlannerDecision')
        spend = self._charge(decision, spend_before)
        # This turn's cost could not be known (committed as unknown, or reported in another
        # currency): an unknown cost stops further spend, so investigation ends here.
        unknown_cost = self.budget.spend_unknown and not unknown_before
        refusals = self.budget.refusals
        refused = next((r for r in ('spend_unknown', 'spend')
                        if refusals.get(r, 0) > refusals_before.get(r, 0)), None)
        action = (next((a for a in menu if a.action_id == decision.action_id), None)
                  if decision.kind == 'act' else None)
        # Checked again now: a cancel, or a planner call that ran past its declared time, may have
        # left no room for the chosen action. An action that does not fit is not run at all.
        not_run = None if action is None else (
            'spend_unknown' if unknown_cost else self._blocked(action.calls, self._rooms()[action.source_id]))
        self.tracer.planner_turn(
            decision, duration_ms=duration, spend=spend, action=action, not_run=not_run,
            rejected='action is not in the menu' if decision.kind == 'act' and action is None else None,
            arguments={'turn': turn, 'gaps': len(gaps), 'withheld_gaps': withheld,
                       'actions': [a.action_id for a in menu]})
        if unknown_cost:  # whatever the planner decided
            self._hit('spend_unknown')
            self.stop_reason = STOP_SPEND_UNKNOWN
            return False
        if action is None:
            if refused is not None:  # the planner could not pay for its model call
                self._hit(refused)
                self.stop_reason = _LIMIT_STOPS[refused]
                return False
            if decision.kind == 'stop':
                self.stop_reason = STOP_PLANNER
                return False
            self.history.append({'turn': turn, 'outcome': 'invalid'})
            return True
        if not_run is not None:
            return True  # the next menu ends the run with the matching reason, or offers what still fits
        self._execute(action, turn)
        return True

    def _charge(self, decision: PlannerDecision, spend_before: Decimal) -> Decimal:
        """The turn's spend as the budget recorded it. A cost the planner reports without having
        reserved it is recorded now, and stops further spend, so usage never under-counts."""
        usage = decision.usage
        if usage is not None:
            recorded = self.budget.spend_total() - spend_before
            if usage.cost.currency != USD:
                self.budget.record_unreserved_spend(None)
            elif usage.cost.amount > recorded:
                self.budget.record_unreserved_spend(usage.cost.amount - recorded)
        return self.budget.spend_total() - spend_before

    def _execute(self, action: PlannerAction, turn: int) -> None:
        source_id, target = action.source_id, action.target
        adapter = self.deps.adapters[source_id]
        self.attempted.add((action.tool, source_id, target))  # tried once per run, whatever the outcome
        invoke = ((lambda fetch: adapter.fetch_policy(fetch, target)) if action.tool == 'fetch_policy'
                  else (lambda fetch: adapter.fetch_offer(fetch, target, self.query)))
        result, offers, policies = self._run_tool(
            source_id, action.tool,
            {'source_id': source_id, 'target': target, 'action_id': action.action_id, 'turn': turn}, invoke)
        self.coverage[source_id].errors.extend(result.errors)
        self._absorb(source_id, offers, policies)
        self.tool = None  # a failure in the rules below is not the store tool's
        self._reassess()
        still_open = {gap.gap_id for gap in self._open_gaps()[0]}
        self.history.append({'turn': turn, 'tool': action.tool, 'source_id': source_id, 'target': target,
                             'outcome': result.status, 'error_codes': sorted({e.code for e in result.errors}),
                             'gaps_settled': len(set(action.settles) - still_open)})

    def _open_gaps(self) -> tuple[list[EvidenceGap], int]:
        """Gaps of unresolved offers in a fixed, safe shape, and how many others were withheld."""
        gaps, withheld = [], 0
        for offer, assessment in self.assessed:
            if assessment.decision != 'unresolved':
                continue
            for gap in assessment.gaps:
                if gap.source_id == offer.source_id and gap.source_id in self.sources and _safe_gap(gap):
                    gaps.append(gap)
                else:
                    withheld += 1
        return gaps, withheld

    def _candidates(self, gaps: list[EvidenceGap]) -> list[tuple]:
        """(tool, store, target, gap IDs, calls) for every action that may settle an open gap."""
        by_source: dict[str, list[EvidenceGap]] = {}
        for gap in gaps:
            by_source.setdefault(gap.source_id, []).append(gap)
        candidates = []
        for source_id in self.sources:
            source_gaps = by_source.get(source_id, [])
            if not source_gaps:
                continue
            topics = _policy_topics(self.deps.adapters[source_id], self.query.fulfillment.mode)
            for policy_id, settles in sorted(topics.items()):
                if (('fetch_policy', source_id, policy_id) in self.attempted
                        or not _PLAIN.fullmatch(policy_id)):
                    continue
                ids = [g.gap_id for g in source_gaps if any(_covers(s, g.field_path) for s in settles)]
                if ids:
                    candidates.append(('fetch_policy', source_id, policy_id, ids, POLICY_CALLS))
            per_offer: dict[str, list[str]] = {}
            for gap in source_gaps:
                if gap.field_path in OFFER_GAP_FIELDS:
                    per_offer.setdefault(gap.offer_id, []).append(gap.gap_id)
            for offer_id, ids in sorted(per_offer.items()):
                if ('fetch_offer', source_id, offer_id) not in self.attempted:
                    candidates.append(('fetch_offer', source_id, offer_id, ids, OFFER_CALLS))
        return candidates

    def _menu(self, gaps: list[EvidenceGap]) -> tuple[list[PlannerAction], str | None, bool]:
        """Actions that fit every limit now, numbered A1, A2...; why the others do not fit; and
        whether any action could settle a gap at all.

        Each action must still fit after the planner's own call (its max_call_seconds), so a turn
        starts only when the planner and at least one action both fit before the verification
        reserve; an empty menu for time ends the run before the turn is counted or paid for.
        """
        candidates, rooms = self._candidates(gaps), self._rooms()
        menu, reasons = [], set()
        for tool, source_id, target, settles, calls in candidates:
            reason = self._blocked(calls, rooms[source_id], self.planner_seconds)
            if reason is None:
                menu.append(PlannerAction(action_id=f'A{len(menu) + 1}', tool=tool, source_id=source_id,
                                          target=target, settles=settles, calls=calls))
            else:
                reasons.add(reason)
        shortfall = next((r for r in ('cancelled', 'calls', 'time', 'store') if r in reasons), None)
        return menu, shortfall, bool(candidates)

    def _rooms(self) -> dict[str, int]:
        """Requests each store's per-run cap still allows, re-read from the registry as the gate does."""
        rooms = {}
        for source_id in self.sources:
            try:
                entry = get_active_source(source_id, self.deps.registry_path)
                limits = SourcePolicy.model_validate(entry).limits
            except (PermissionError, ValueError):
                rooms[source_id] = 0
            else:
                rooms[source_id] = max(0, limits.max_requests_per_run - self.budget.calls_used(source_id))
        return rooms

    def _blocked(self, calls: int, room: int, extra_seconds: float = 0.0) -> str | None:
        """Why a tool needing `calls` gate requests cannot start now (or after `extra_seconds`
        more, such as a planner call): the run's own limits first, each request counted at the
        gate's full per-attempt timeout, then the store's cap."""
        reason = self.budget.shortfall(calls, extra_seconds + calls * CALL_TIMEOUT_S)
        if reason in ('cancelled', 'closed'):
            return 'cancelled'
        return reason or ('store' if room < calls else None)

    def _hit(self, reason: str) -> None:
        if self.limit is None:
            self.limit = _LIMIT_STOPS[reason]

    # Verify and results

    def _verify(self, data: dict) -> list[tuple[Offer, Assessment]]:
        if self._cancelled():
            return []
        self.budget.enter_final_phase()
        finalists = [(o, a) for o, a in self.assessed if a.decision == 'recommendable']
        verified = self._final_check(finalists)
        data.update(finalists=len(finalists), verified=len(verified))
        return verified

    def _final_check(self, finalists: list[tuple[Offer, Assessment]]) -> list[tuple[Offer, Assessment]]:
        """Re-assess the finalists on evidence observed during this run: every record must cite a
        reply this run's gate returned (its body hash and observation time), which a step of the
        wall clock cannot fake or spoil.

        MVP 13 adds a live re-fetch of each finalist here, within the final-phase reserve, and
        drops or re-ranks the offers that changed.
        """
        fresh = []
        for offer, _ in finalists:
            if all((e.response_sha256, e.observed_at) in self.observed for e in offer.evidence):
                fresh.append(offer)
            else:
                self.withheld[offer.offer_id] = [REASON_STALE]
        if not fresh:
            return []
        again = self._assess(fresh)
        updated = {offer.offer_id: (offer, assessment) for offer, assessment in again}
        self.assessed = [updated.get(offer.offer_id, (offer, assessment))
                         for offer, assessment in self.assessed]
        return [(o, a) for o, a in again if a.decision == 'recommendable']

    def _shortlist(self, finalists, verified_at: datetime) -> list[Recommendation]:
        """Up to three, cheapest verified total first, then offer ID."""
        recommendations = []
        ranked = sorted(finalists, key=lambda pair: (pair[1].cost.verified_total.amount, pair[0].offer_id))
        for offer, assessment in ranked:
            total = assessment.cost.verified_total
            if total.currency != self.request.budget.currency:
                self.withheld[offer.offer_id] = [REASON_CURRENCY]
            elif len(recommendations) < MAX_RECOMMENDATIONS:
                try:
                    recommendations.append(Recommendation(offer=offer, payable_total=total,
                                                          verified_at=verified_at))
                except ValidationError:  # the offer still lists a charge whose terms are unknown
                    self.withheld[offer.offer_id] = [REASON_UNKNOWN_CHARGE]
        return recommendations

    def _finish(self, finalists=(), *, error: str | None = None, failure: dict | None = None) -> RunResult:
        self.stage = 'results'
        cancelled = error is None and self._cancelled()
        if error is not None or cancelled:
            reason = REASON_CANCELLED if cancelled else REASON_ERROR
            for offer, assessment in self.assessed:
                if assessment.decision == 'recommendable':
                    self.withheld.setdefault(offer.offer_id, [reason])
            finalists = ()
        verified_at = max(self._now(), self.started_at)
        recommendations = self._shortlist(finalists, verified_at)
        if error is not None:
            status, stop = 'error', error
        elif cancelled:
            status, stop = 'cancelled', STOP_CANCELLED
        elif (limit := self._limit()) is not None:
            status, stop = 'partial', self.stop_reason if self.stop_reason in PARTIAL_STOPS else limit
        elif not recommendations and self.coverage and all(c.search_failed for c in self.coverage.values()):
            status, stop = 'error', STOP_NO_STORE_READ  # every store's search failed or could not run
        else:
            status, stop = 'complete' if recommendations else 'abstained', self.stop_reason or STOP_NO_GAPS
        shown = {r.offer.offer_id for r in recommendations}
        unresolved, rejected = [], []
        for offer, assessment in self.assessed:
            if offer.offer_id in shown:
                continue  # verified offers ranked below the shortlist are not listed either
            if offer.offer_id in self.withheld:
                unresolved.append(OfferIssue(offer_id=offer.offer_id, reasons=self.withheld[offer.offer_id]))
            elif assessment.decision != 'recommendable':
                issues = rejected if assessment.decision == 'rejected' else unresolved
                issues.append(OfferIssue(offer_id=offer.offer_id, reasons=assessment.reasons()))
        if error is not None:  # offers found before the error that were never assessed
            listed = {offer.offer_id for offer, _ in self.assessed}
            found = dict.fromkeys(o.offer_id for o in self.offers)
            unresolved += [OfferIssue(offer_id=offer_id, reasons=[REASON_ERROR])
                           for offer_id in found if offer_id not in listed]
        finished_at = max(self._now(), verified_at)
        usage = self.budget.usage()  # elapsed on the run clock the caps use, never the wall clock
        result = RunResult(
            run_id=self.run_id, request_id=self.request.request_id, status=status, stop_reason=stop,
            started_at=self.started_at, finished_at=finished_at, recommendations=recommendations,
            unresolved=unresolved, rejected=rejected,
            source_coverage=[c.contract() for c in self.coverage.values()], usage=usage)
        self.tracer.finish(status, stop)
        metrics = self.tracer.summary()
        data = {'status': status, 'stop_reason': stop, 'recommendations': len(recommendations),
                'unresolved': len(unresolved), 'rejected': len(rejected), 'metrics': metrics,
                'budget': {'refusals': self.budget.refusals, 'spend_unknown': self.budget.spend_unknown,
                           'gate_ms': _ms(self.budget.call_seconds)}}
        if failure is not None:
            data['failure'] = failure
        # Usage no record holds yet (a call or turn cut short by an error) goes on this record,
        # so the stored run totals always match the result.
        self.tracer.stage('results', data=data,
                          external_calls=max(0, usage.external_calls - metrics['external_calls']),
                          planner_turns=max(0, usage.planner_turns - metrics['planner_turns']),
                          spend=max(_ZERO, usage.spend.amount - Decimal(metrics['spend']['amount'])))
        self.deps.storage.finish_run(result)
        return result

    def _fail(self, exc: Exception) -> RunResult:
        # Type, stage and the store tool in progress (registry IDs) only: exception text can quote
        # store data, so it stays out of the trace and the error log; the traceback goes to this
        # logger's DEBUG level for developers.
        name, tool = type(exc).__name__, self.tool
        log.error('Run %s stopped on an unexpected %s during %s%s', self.run_id, name, self.stage,
                  f" ({tool['tool']} for {tool['source_id']})" if tool else '')
        log.debug('Run %s failure details', self.run_id, exc_info=exc)
        if tool is not None:  # the store whose tool was running keeps a coverage entry saying so
            source_id = tool['source_id']
            entry = self.coverage.setdefault(source_id, _Coverage(source_id, search_failed=True))
            entry.errors.append(ToolError(code='INVALID_SCHEMA', retryable=False, message=(
                f"{source_id[:60]}: {tool['tool']} stopped on an unexpected error")))
        for source_id in self.sources:  # stores the run never reached
            self.coverage.setdefault(source_id, _Coverage.not_searched(source_id, 'error'))
        return self._finish(error=STOP_ERROR, failure={'stage': self.stage, 'error': name, **(tool or {})})

    def _limit(self) -> str | None:
        """The first run limit that cut work short, including refusals the gate met inside a tool."""
        if self.limit is not None:
            return self.limit
        refusals = self.budget.refusals
        return next((_LIMIT_STOPS[r] for r in refusals if r in _GATE_LIMITS), None)

    def _cancelled(self) -> bool:
        return self.budget.cancelled

    def _write_ahead(self) -> None:
        """Store the run's totals, counting what is reserved, before a request, paid call or planner
        turn goes ahead (the budget's on_usage hook), so recovery after a crash never under-counts
        the step in flight. A run another process has closed refuses it, so nothing more is sent."""
        usage = self.budget.usage()
        self.tracer.note(external_calls=usage.external_calls, planner_turns=usage.planner_turns,
                         spend=usage.spend.amount)

    def _now(self) -> datetime:
        now = self.deps.wall_clock()
        if now.utcoffset() is None:
            raise ValueError('wall_clock must return timezone-aware datetimes')
        return now.astimezone(timezone.utc)


# Command line: one search by hand, for development and the demo

def _planner(name: str) -> Planner:
    """The planner a command-line search uses, so the run records the right one: 'rule' is the
    deterministic baseline, 'none' the no-investigation baseline (its run also has no planner
    turns, so it is never asked) and 'model' the configured AnthropicPlanner, which needs
    ANTHROPIC_API_KEY."""
    if name == 'model':
        from app.planner import AnthropicPlanner, load_planner_config
        return AnthropicPlanner(load_planner_config())
    if name == 'none':
        return NoInvestigationPlanner()
    return RulePlanner()


def main(argv: Sequence[str] | None = None, *, dependencies: RunDependencies | None = None) -> int:
    """python -m app.runs search REQUEST.json: run one search and print its RunResult as JSON.

    It reads the admitted stores' public pages through the gate (a few requests per store) and
    stores the run, so apply the migrations first. It first closes runs a crash left 'running'
    longer than the recovery lease. Ctrl+C cancels the run; the result says so.
    """
    parser = argparse.ArgumentParser(prog='python -m app.runs')
    commands = parser.add_subparsers(dest='command', required=True)
    search = commands.add_parser('search', help='run one search for a ShoppingRequest JSON file')
    search.add_argument('request', type=Path, help='a ShoppingRequest as JSON')
    search.add_argument('--planner', choices=('rule', 'none', 'model'), default='rule',
                        help="'rule' investigates with the rule baseline, 'none' never investigates and "
                             "'model' uses the configured model planner (paid; needs ANTHROPIC_API_KEY)")
    args = parser.parse_args(argv)
    try:
        request = ShoppingRequest.model_validate_json(args.request.read_text(encoding='utf-8'))
    except (OSError, ValidationError) as exc:
        print(f'Cannot read the request: {type(exc).__name__}', file=sys.stderr)
        return 2
    caps = RunCaps.from_config(json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'))['caps'])
    if args.planner == 'none':
        caps = replace(caps, planner_turns=0)
    try:
        deps = dependencies or default_dependencies(planner=_planner(args.planner))
    except (ImportError, ValueError) as exc:  # the SDK is missing or no credentials are configured
        print(f'Cannot set up the {args.planner} planner: {type(exc).__name__}', file=sys.stderr)
        return 2
    cancel, done, outcome, run_id = threading.Event(), threading.Event(), {}, new_run_id()

    def work():
        try:
            closed = deps.storage.recover_interrupted_runs()  # never a run that may still be live
            if closed:
                log.warning('Marked %d interrupted run(s) as partial: %s', len(closed), closed)
            outcome['result'] = run_search(request, deps, run_id=run_id, caps=caps, cancel=cancel)
        except Exception as exc:  # before the run was stored, or a storage failure after it
            outcome['error'] = exc
        finally:
            done.set()

    worker = threading.Thread(target=work, name='techscout-run', daemon=True)
    worker.start()
    # Wait on an Event, never Thread.join: on Python 3.11 a join interrupted by Ctrl+C can return
    # while the worker still runs, which left the run behind without a result.
    try:
        while not done.wait(0.2):
            pass
    except KeyboardInterrupt:
        cancel.set()  # the run stops before its next step or request; one already sent ends within its timeout
        done.wait()
    if 'result' not in outcome:
        error = outcome.get('error')
        name = type(error).__name__ if error is not None else 'interruption'
        try:
            status = deps.storage.run_status(run_id)
        except Exception:
            status = 'unknown'
        if status is None:
            print(f'The run could not start: {name}', file=sys.stderr)
        else:
            print(f'Run {run_id} stopped on an unexpected {name} without storing its result '
                  f'(stored status: {status})', file=sys.stderr)
        return 1
    result = outcome['result']
    print(result.model_dump_json(indent=2))
    return 1 if result.status == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
