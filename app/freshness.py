"""Deterministic, field-scoped reuse decisions. Checking a page never retimes a fact."""
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from pydantic import model_validator

from app.contracts import Model, Text, UtcTime
from app.contracts_v2 import Evidence, RunReference
from app.corpus_contracts import FieldObservation


class FreshnessContext(Model):
    now: UtcTime
    product_id: Text | None = None
    variant_id: Text | None = None
    branch_id: Text | None = None
    route_id: Text | None = None
    package_fingerprint: Text | None = None
    run: RunReference | None = None
    run_started_at: UtcTime | None = None

    @model_validator(mode='after')
    def coherent_run(self):
        if (self.run is None) != (self.run_started_at is None):
            raise ValueError('A run needs its start time')
        if self.run_started_at and self.run_started_at > self.now:
            raise ValueError('A run cannot start in the future')
        return self


class QuoteValidity(Model):
    evidence_id: Text
    route_id: Text
    package_fingerprint: Text
    valid_until: UtcTime


@dataclass(frozen=True)
class FreshnessPolicy:
    specifications: timedelta = timedelta(days=30)
    policies: timedelta = timedelta(days=7)

    def __post_init__(self):
        if self.specifications <= timedelta(0) or self.policies <= timedelta(0):
            raise ValueError('Freshness ages must be positive')


@dataclass(frozen=True)
class Freshness:
    state: Literal['fresh', 'historical', 'unknown', 'conflict', 'retired']
    reason: str
    evidence_ids: tuple[str, ...]

    @property
    def reusable(self):
        return self.state == 'fresh'


def assess_evidence(receipt: Evidence, context: FreshnessContext, *,
                    policy: FreshnessPolicy = FreshnessPolicy(),
                    fact_state: str = 'known', quote: QuoteValidity | None = None,
                    expires_at: UtcTime | None = None, check_status: str | None = None,
                    superseded: bool = False) -> Freshness:
    """Assess applicability, never purchase eligibility or reservation.

    Callers must supply the requested exact scope and any earlier source expiry.
    Unknown field types fail closed. Quotes require an explicit unchanged package.
    """
    def result(state, reason):
        return Freshness(state, reason, (receipt.evidence_id,))

    for field in ('product_id', 'variant_id', 'branch_id', 'route_id'):
        actual, requested = getattr(receipt, field), getattr(context, field)
        if actual is not None and actual != requested:
            return result('unknown', f'{field} does not match the requested scope')
    if receipt.field_path in {'price', 'stock', 'purchasable'}:
        if not receipt.variant_id or not receipt.product_id or (
            receipt.field_path == 'stock' and receipt.branch_id != context.branch_id
        ):
            return result('unknown', 'Price/stock requires the exact configuration and stock scope')
    if receipt.observed_at > context.now:
        return result('unknown', 'Observation is in the future')
    if fact_state == 'conflict' or receipt.conflicts:
        return result('conflict', 'Resolve conflicting evidence before reuse')
    if fact_state != 'known':
        return result('unknown', 'The source fact is unresolved')
    if superseded:
        return result('historical', 'A newer document supersedes this evidence')
    if check_status in {'removed', 'discontinued'}:
        return result('retired', 'Supported source removal; history is retained')
    if check_status in {'failed', 'denied', 'absent'}:
        return result('historical', 'Refresh failed or absence is ambiguous')
    if expires_at is not None:
        from pydantic import TypeAdapter
        expires_at = TypeAdapter(UtcTime).validate_python(expires_at)
        if context.now >= expires_at:
            return result('historical', 'Explicit source validity has expired')
    if quote is not None and receipt.field_path not in {'price', 'stock', 'purchasable'}:
        if (quote.evidence_id != receipt.evidence_id or quote.route_id != receipt.route_id
                or quote.route_id != context.route_id
                or quote.package_fingerprint != context.package_fingerprint):
            return result('unknown', 'Quote route or package assumptions changed')
        if context.now >= quote.valid_until:
            return result('historical', 'Item-specific quote expired')
        return result('fresh', 'Quote is within its scoped validity')
    if receipt.field_path in {'price', 'stock', 'purchasable'}:
        if (context.run is None or receipt.run != context.run
                or receipt.observed_at < context.run_started_at):
            return result('historical', 'Cached price/stock is discovery evidence only')
        return result('fresh', 'Exact scope checked during the current run')
    age = context.now - receipt.observed_at
    if receipt.field_path.startswith('variant.'):
        limit = policy.specifications
    elif receipt.field_path.startswith('policy.') and receipt.scope == 'source':
        limit = policy.policies
    else:
        return result('unknown', 'Field requires explicit validity or a new check')
    if age >= limit:
        return result('historical', 'Reuse age limit reached')
    return result('fresh', 'Within the field reuse window')


def assess_observation(observation: FieldObservation, context: FreshnessContext, **options) -> Freshness:
    results = [assess_evidence(item, context, fact_state=observation.fact.state, **options)
               for item in observation.evidence]
    decision = next((item for item in results if not item.reusable), results[0])
    return Freshness(decision.state, decision.reason, tuple(observation.fact.evidence_ids))
