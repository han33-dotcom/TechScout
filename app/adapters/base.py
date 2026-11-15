"""Store reader interfaces (MVP 05): gate replies become version 1 contracts with evidence.

Readers never open connections: every request goes through a Fetch bound to one run's budget,
functools.partial(SourceGate.fetch, budget). Each call returns the offers and policy facts it
found, plus the raw bodies to store before any evidence that cites them.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import re
from typing import Literal, Protocol

from app.contracts import ArrivalWindow, Evidence, Fulfillment, Offer, ShoppingRequest, ToolError, ToolResult
from app.sources import Expect, FetchOutcome

MAX_EXCERPT = 500
# Lebanon keeps UTC+2 in winter and UTC+3 in summer. Windows use the offset that makes them wider,
# so a claim is never read as faster than the store said.
_UTC2, _UTC3 = timezone(timedelta(hours=2)), timezone(timedelta(hours=3))
# Lebanon's fixed-date official holidays, as (month, day): New Year, Armenian Christmas, Saint
# Maroun, the Annunciation, Labour Day, the Assumption, Independence Day and Christmas. They are
# skipped only when counting the latest bound of a working-day window, so a holiday can make a
# deadline harder to pass but never causes a fail. Movable religious holidays (the Eids, both
# Good Fridays and Easters, Ashura, the Prophet's birthday, the Hijri New Year) and a store's
# dispatch cutoff are not modelled, so a window can still end too early around them.
_FIXED_HOLIDAYS = frozenset({(1, 1), (1, 6), (2, 9), (3, 25), (5, 1), (8, 15), (11, 22), (12, 25)})
_DIGITS = re.compile(r'[0-9]+')


class Fetch(Protocol):
    """A gate fetch bound to one run's budget."""
    def __call__(self, source_id: str, url: str, *, expect: Expect) -> FetchOutcome: ...


@dataclass(frozen=True)
class CandidateQuery:
    """What to look for and where the buyer receives it; offers are built for this route."""
    manufacturer: str
    model: str
    storage_gb: int
    fulfillment: Fulfillment

    @classmethod
    def from_request(cls, request: ShoppingRequest) -> 'CandidateQuery':
        return cls(request.manufacturer, request.model, request.storage_gb, request.fulfillment)


@dataclass(frozen=True)
class Snapshot:
    """A raw reply body; Storage.save_snapshot must keep it before evidence cites its hash."""
    sha256: str
    media_type: str
    body: bytes

    @classmethod
    def of(cls, outcome: FetchOutcome) -> 'Snapshot':
        if outcome.body is None or outcome.response_sha256 is None:
            raise ValueError('Only a successful fetch has a body to store')
        return cls(outcome.response_sha256, outcome.content_type or 'application/octet-stream', outcome.body)


PolicyTopic = Literal['payment', 'tax', 'delivery_fee', 'delivery_time', 'warranty', 'returns', 'pickup']


@dataclass(frozen=True)
class PolicyFact:
    """One store-wide statement, with the bounded store text that shows it."""
    topic: PolicyTopic
    value: dict  # JSON-safe and topic-specific; each store adapter documents its keys
    excerpt: str


@dataclass(frozen=True)
class PolicyDocument:
    source_id: str
    policy_id: str
    url: str
    final_url: str
    observed_at: datetime
    response_sha256: str
    adapter_version: str
    facts: tuple[PolicyFact, ...] = ()


@dataclass(frozen=True)
class AdapterResult:
    """One adapter call: the trace record, what it found, and the bodies to store."""
    tool_result: ToolResult
    offers: tuple[Offer, ...] = ()
    policies: tuple[PolicyDocument, ...] = ()
    snapshots: tuple[Snapshot, ...] = ()


class StoreAdapter(Protocol):
    source_id: str
    adapter_version: str
    # Policy pages it can read, each with the offer field paths it may settle. A reader may also
    # define policy_topics_for(mode) giving the paths for one route ('delivery' or 'pickup'); the
    # run controller then uses that instead.
    policy_topics: dict[str, tuple[str, ...]]

    def search_candidates(self, fetch: Fetch, query: CandidateQuery) -> AdapterResult: ...

    def fetch_offer(self, fetch: Fetch, offer_id: str, query: CandidateQuery) -> AdapterResult: ...

    def fetch_policy(self, fetch: Fetch, policy_id: str) -> AdapterResult: ...

    def apply_policy(self, offer: Offer, policy: PolicyDocument) -> Offer: ...


def offer_id(source_id: str, product_id: str, variant_id: str) -> str:
    return f'{source_id}:{product_id}:{variant_id}'


def parse_offer_id(value: str) -> tuple[str, str, str]:
    parts = value.split(':')
    if len(parts) != 3 or not all(parts):
        raise ValueError('An offer ID is source:product:variant')
    return parts[0], parts[1], parts[2]


def evidence_id(source_id: str, subject: str, field_path: str, sha256: str, observed_at: datetime,
                tag: str = '') -> str:
    """Unique per observation, so a stored record never changes when the same page is read again."""
    stamp = observed_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    suffix = f':{tag}' if tag else ''
    return f'{source_id}/{subject}/{field_path}/{sha256[:16]}/{stamp}{suffix}'


def excerpt(text: str) -> str:
    """Whitespace collapsed and bounded, as Evidence.excerpt requires."""
    text = ' '.join(text.split())
    if not text:
        raise ValueError('An excerpt needs text')
    return text if len(text) <= MAX_EXCERPT else text[:MAX_EXCERPT - 1] + '…'


def json_pointer(*parts) -> str:
    """An RFC 6901 pointer into the reply that holds the fact."""
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def make_evidence(outcome: FetchOutcome, *, adapter_version: str, field_path: str, scope: str,
                  product_id: str | None = None, variant_id: str | None = None,
                  json_pointer: str | None = None, text: str | None = None, tag: str = '') -> Evidence:
    """Evidence bound to one successful fetch; text becomes a bounded excerpt."""
    if outcome.response_sha256 is None or outcome.observed_at is None or outcome.final_url is None:
        raise ValueError('Evidence needs a successful fetch')
    subject = variant_id or product_id or 'source'
    return Evidence(
        evidence_id=evidence_id(outcome.source_id, subject, field_path, outcome.response_sha256,
                                outcome.observed_at, tag),
        source_id=outcome.source_id, product_id=product_id, variant_id=variant_id, scope=scope,
        field_path=field_path, origin_url=outcome.origin_url, final_url=outcome.final_url,
        observed_at=outcome.observed_at, response_sha256=outcome.response_sha256,
        adapter_version=adapter_version, json_pointer=json_pointer,
        excerpt=None if text is None else excerpt(text))


def policy_evidence(policy: PolicyDocument, fact: PolicyFact, field_path: str) -> Evidence:
    """Store-wide evidence for one offer field; the same statement and field always give the same ID.

    The tag names the statement's place on the page, so two statements on one topic (delivery
    times for two zones) never share an ID.
    """
    return Evidence(
        evidence_id=evidence_id(policy.source_id, f'policy-{policy.policy_id}', field_path,
                                policy.response_sha256, policy.observed_at,
                                f'{fact.topic}{policy.facts.index(fact)}'),
        source_id=policy.source_id, scope='source', field_path=field_path, origin_url=policy.url,
        final_url=policy.final_url, observed_at=policy.observed_at,
        response_sha256=policy.response_sha256, adapter_version=policy.adapter_version,
        excerpt=excerpt(fact.excerpt))


def minor_units(raw: str, digits: int) -> Decimal:
    """'110000' with two fractional digits is 1100.00; anything else is a schema error."""
    if type(raw) is not str or not _DIGITS.fullmatch(raw) or type(digits) is not int or not 0 <= digits <= 6:
        raise ValueError('Prices are unsigned integer strings in declared minor units')
    return Decimal(raw).scaleb(-digits)


def tool_result(outcomes, errors: list[ToolError], data: dict, evidence_ids, produced: bool) -> ToolResult:
    """Fetch entries and parse errors together; 'error' only when nothing usable was produced."""
    outcomes = list(outcomes)
    errors = [e for o in outcomes for e in o.errors] + list(errors)
    status = 'ok' if not errors else 'partial' if produced else 'error'
    return ToolResult(status=status, data={'fetches': [o.entry() for o in outcomes], **data},
                      evidence_ids=sorted(set(evidence_ids)), errors=errors,
                      duration_ms=sum(o.duration_ms for o in outcomes))


def _working_days(start: date, count: int, *, skip_holidays: bool = False) -> date:
    """The count-th Monday-to-Friday day after start; with skip_holidays, fixed holidays do not count."""
    day = start
    while count > 0:
        day += timedelta(days=1)
        if day.weekday() < 5 and not (skip_holidays and (day.month, day.day) in _FIXED_HOLIDAYS):
            count -= 1
    return day


def arrival_window(observed_at: datetime, min_days: int, max_days: int, *,
                   unit: Literal['calendar', 'working'], basis: Literal['estimate', 'guaranteed']) -> ArrivalWindow:
    """A 'N to M days' claim as absolute times, counted from the observation in Beirut.

    The earliest time is the start of day N (now, for zero days); the latest is the end of day M.
    Working days run Monday to Friday; only the latest bound also skips Lebanon's fixed-date
    holidays, which can only widen the window.
    """
    if not 0 <= min_days <= max_days:
        raise ValueError('Day ranges are non-negative and ordered')

    def shift(start: date, days: int, *, skip_holidays: bool = False) -> date:
        if unit == 'calendar':
            return start + timedelta(days=days)
        return _working_days(start, days, skip_holidays=skip_holidays)

    if min_days == 0:
        earliest = observed_at
    else:
        # The earlier local date, and midnight at UTC+3, which comes first.
        earliest = datetime.combine(shift(observed_at.astimezone(_UTC2).date(), min_days), time(), _UTC3)
    # The later local date, and the end of day at UTC+2, which comes last.
    last_day = shift(observed_at.astimezone(_UTC3).date(), max_days, skip_holidays=True)
    latest = datetime.combine(last_day, time.max, _UTC2)
    return ArrivalWindow(earliest=max(earliest, observed_at), latest=latest, basis=basis)
