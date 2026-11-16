"""Version 1 data contracts. Retrieval, eligibility and ranking are separate tasks."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator, AnyHttpUrl, BaseModel, BeforeValidator, ConfigDict, Field,
    JsonValue, StrictBool, StrictInt, StringConstraints, model_validator,
)


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]
Currency = Annotated[str, StringConstraints(pattern=r'^[A-Z]{3}$', strict=True)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
Count = Annotated[StrictInt, Field(ge=0)]
Sim = Literal['physical+esim', 'esim-only', 'dual-physical']
T = TypeVar('T')


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('Money must be a decimal string, integer or Decimal; not a float')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal amount') from exc
    if not result.is_finite():
        raise ValueError('Money must be finite')
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('A timezone offset is required')
    return value.astimezone(timezone.utc)


def _https(value: AnyHttpUrl) -> AnyHttpUrl:
    if value.scheme != 'https' or value.username is not None or value.password is not None:
        raise ValueError('Use HTTPS without URL credentials')
    return value


Amount = Annotated[Decimal, BeforeValidator(_decimal), Field(ge=0)]
UtcTime = Annotated[datetime, AfterValidator(_utc)]
HttpsUrl = Annotated[AnyHttpUrl, AfterValidator(_https)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Contract(Model):
    schema_version: Literal[1] = 1


class Money(Model):
    amount: Amount
    currency: Currency


class Fact(Model, Generic[T]):
    state: Literal['known', 'unknown', 'conflict']
    value: T | None = None
    alternatives: list[T] = Field(default_factory=list)
    reason: Text | None = None
    evidence_ids: list[Text] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent_state(self):
        if self.state == 'known':
            if self.value is None or self.alternatives or not self.evidence_ids:
                raise ValueError('Known facts need one value and evidence references')
        elif self.state == 'unknown':
            if self.value is not None or self.alternatives or not self.reason:
                raise ValueError('Unknown facts need a reason, without invented values')
        elif (self.value is not None or not self.reason or not self.evidence_ids
              or len(self.alternatives) < 2
              or any(v == earlier for i, v in enumerate(self.alternatives)
                     for earlier in self.alternatives[:i])):
            raise ValueError('Conflicts need distinct alternatives, evidence and a reason')
        return self


class Fulfillment(Model):
    mode: Literal['pickup', 'delivery']
    country: Literal['LB']
    city: Text
    branch_id: Text | None = None

    @model_validator(mode='after')
    def branch_is_for_pickup(self):
        if self.mode == 'delivery' and self.branch_id is not None:
            raise ValueError('A delivery location cannot contain a pickup branch')
        return self


class WarrantyRequirement(Model):
    provider: Text | None = None
    min_months: Count
    territory: Text


class Deadline(Model):
    arrive_by: UtcTime
    basis: Literal['estimate_ok', 'guaranteed']


class ShoppingRequest(Contract):
    request_id: Text
    manufacturer: Text
    model: Text
    storage_gb: PositiveInt
    allowed_colors: list[Text] = Field(min_length=1)
    sim: Literal['physical+esim', 'esim-only', 'dual-physical', 'any']
    allowed_regions: list[Text] = Field(min_length=1)
    condition: Literal['new'] = 'new'
    warranty_requirement: WarrantyRequirement | None
    budget: Money
    payment_basis: Literal['cash', 'card']
    fulfillment: Fulfillment
    allowed_markets: list[Literal['LB']] = Field(min_length=1)
    deadline: Deadline | None
    show_unresolved_imports: StrictBool = False

    @model_validator(mode='after')
    def unambiguous_request(self):
        if self.manufacturer.casefold() in {'any', '*', 'unknown', 'unspecified'}:
            raise ValueError('Specify a manufacturer')
        model = self.model.casefold()
        if (model in {'any', '*', 'iphone', 'samsung', 'smartphone', 'phone', 'mobile phone'}
                or model.split()[0] in {'any', 'best', 'latest', 'cheapest'}):
            raise ValueError('Specify an exact model; clarify broad device requests')
        if self.budget.amount <= 0:
            raise ValueError('Budget must be positive')
        for choices in (self.allowed_colors, self.allowed_regions):
            if 'any' in [v.casefold() for v in choices] and len(choices) != 1:
                raise ValueError('Use any alone, or specify allowed choices')
        return self


class SourceReview(Model):
    basis: Text
    evidence: list[Text] = Field(min_length=1)
    reviewed_at: UtcTime
    scope: Text


class SourceLimits(Model):
    requests_per_minute: Count
    concurrency: Count
    max_requests_per_run: Count


class SourcePolicy(Contract):
    # Registry observations stay in JSON; this consumes only operational fields.
    model_config = ConfigDict(extra='ignore', frozen=True)
    id: Text
    name: Text
    origin: HttpsUrl
    status: Literal['pending', 'active', 'paused', 'disabled', 'revoked', 'rejected']
    reviewed_at: date
    admission: SourceReview | None
    allowed_endpoints: list[HttpsUrl]
    limits: SourceLimits
    allowed_methods: tuple[Literal['GET'], ...] = ('GET',)
    adapter_version: Text | None = None

    @model_validator(mode='after')
    def scoped_configuration(self):
        if self.origin.path not in (None, '/') or self.origin.query or self.origin.fragment:
            raise ValueError('Origin must not contain a path, query or fragment')
        for endpoint in self.allowed_endpoints:
            if ((endpoint.scheme, endpoint.host, endpoint.port)
                    != (self.origin.scheme, self.origin.host, self.origin.port)
                    or endpoint.fragment):
                raise ValueError('Endpoint must belong to the configured origin')
        if self.status == 'active' and (
            self.admission is None or not self.allowed_endpoints or not self.allowed_methods
            or any(v <= 0 for v in self.limits.model_dump().values())
        ):
            raise ValueError('Active sources need technical review, endpoints and positive limits')
        return self


class Variant(Contract):
    source_id: Text
    product_id: Text
    variant_id: Text
    manufacturer: Fact[Text]
    model: Fact[Text]
    storage_gb: Fact[PositiveInt]
    color: Fact[Text]
    sim: Fact[Sim]
    region: Fact[Text]
    raw_attributes: dict[str, JsonValue] = Field(default_factory=dict)


class Evidence(Contract):
    evidence_id: Text
    source_id: Text
    product_id: Text | None = None
    variant_id: Text | None = None
    scope: Literal['source', 'product', 'variant', 'branch', 'route']
    branch_id: Text | None = None
    route_id: Text | None = None
    field_path: Text
    origin_url: HttpsUrl
    final_url: HttpsUrl
    observed_at: UtcTime
    response_sha256: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
    adapter_version: Text
    json_pointer: Text | None = None
    excerpt: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    conflicts: list[Text] = Field(default_factory=list)

    @model_validator(mode='after')
    def evidence_binding(self):
        if not self.json_pointer and not self.excerpt:
            raise ValueError('Evidence needs a JSON pointer or a bounded excerpt')
        if self.scope in ('product', 'variant') and self.product_id is None:
            raise ValueError('Product evidence needs a product ID')
        if self.scope == 'variant' and self.variant_id is None:
            raise ValueError('Variant evidence needs an actual variant ID')
        if self.scope == 'branch' and self.branch_id is None:
            raise ValueError('Branch evidence needs a branch ID')
        if self.scope == 'route' and self.route_id is None:
            raise ValueError('Route evidence needs a route ID')
        return self


class CostComponent(Contract):
    kind: Literal['device', 'tax', 'delivery', 'payment', 'travel', 'other']
    state: Literal['additional', 'included', 'unknown', 'not_applicable']
    amount: Money | None = None
    reason: Text | None = None
    conditions: list[Text] = Field(default_factory=list)
    evidence_ids: list[Text] = Field(default_factory=list)
    valid_until: UtcTime | None = None

    @model_validator(mode='after')
    def cost_state(self):
        if self.state in ('unknown', 'not_applicable'):
            if self.amount is not None or not self.reason:
                raise ValueError('Unknown/not-applicable components need a reason and no amount')
        elif not self.evidence_ids or (self.state == 'additional' and self.amount is None):
            raise ValueError('Resolved costs need evidence; additional costs need an amount')
        return self


class BranchStock(Model):
    branch_id: Text
    city: Text
    stock: Fact[StrictBool]


class Warranty(Model):
    provider: Fact[Text]
    months: Fact[Count]
    territory: Fact[Text]


class ArrivalWindow(Model):
    earliest: UtcTime
    latest: UtcTime
    basis: Literal['estimate', 'guaranteed']

    @model_validator(mode='after')
    def ordered_window(self):
        if self.latest < self.earliest:
            raise ValueError('Arrival window must be chronological')
        return self


def _references(model: Model, path='', branch_id=None):
    if isinstance(model, (Fact, CostComponent)):
        for evidence_id in model.evidence_ids:
            yield path, evidence_id, branch_id
        return
    for name in type(model).model_fields:
        value = getattr(model, name)
        child_path = f'{path}.{name}' if path else name
        if isinstance(value, Model):
            yield from _references(value, child_path, branch_id)
        elif isinstance(value, list):
            for i, entry in enumerate(value):
                if isinstance(entry, Model):
                    key = (entry.branch_id if isinstance(entry, BranchStock)
                           else entry.kind if isinstance(entry, CostComponent) else str(i))
                    branch = entry.branch_id if isinstance(entry, BranchStock) else branch_id
                    yield from _references(entry, f'{child_path}.{key}', branch)


class Offer(Contract):
    offer_id: Text
    source_id: Text
    seller_id: Text
    variant: Variant
    price: Fact[Money]
    payment_basis: Fact[Literal['cash', 'card']]
    raw_price: Text | None = None
    price_fractional_digits: Count | None = None
    condition: Fact[Literal['new', 'used', 'refurbished']]
    purchasable: Fact[StrictBool]
    online_stock: Fact[StrictBool]
    pickup_stock: list[BranchStock] = Field(default_factory=list)
    warranty: Warranty
    fulfillment: Fulfillment
    arrival: Fact[ArrivalWindow]
    costs: list[CostComponent] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode='after')
    def references_bind_to_offer(self):
        if self.variant.source_id != self.source_id:
            raise ValueError('Variant must belong to the offer source')
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError('Evidence IDs must be unique')
        for path, evidence_id, branch in _references(self):
            e = evidence.get(evidence_id)
            if e is None or e.field_path != path:
                raise ValueError('Evidence must support the referring fact/cost field')
            if branch is not None and (e.scope != 'branch' or e.branch_id != branch
                                       or e.product_id != self.variant.product_id
                                       or e.variant_id != self.variant.variant_id):
                raise ValueError('Pickup stock needs evidence for its actual branch and phone')
            if (path in {'variant.manufacturer', 'variant.model'}
                    and e.scope not in {'product', 'variant'}):
                raise ValueError('Device identity needs product or variant evidence')
            if (path in {'price', 'condition', 'purchasable', 'online_stock',
                         'variant.storage_gb', 'variant.color', 'variant.sim', 'variant.region'}
                    and e.scope != 'variant'):
                raise ValueError('Variant-specific facts need selected-variant evidence')
        for e in self.evidence:
            if (e.source_id != self.source_id
                    or (e.product_id is not None and e.product_id != self.variant.product_id)
                    or (e.variant_id is not None and e.variant_id != self.variant.variant_id)):
                raise ValueError('Evidence must belong to this source and actual variant')
        return self


# Gate refusals and permanent HTTP statuses: a documented prefix, then a space or the end.
_REFUSAL_PREFIXES = {
    'BLOCKED': re.compile(r'blocked:(url|method|dns|redirect|size|encoding)( |\Z)'),
    'HTTP_ERROR': re.compile(r'http:[0-9]{3}( |\Z)'),
}


class ToolError(Model):
    code: Literal['TIMEOUT', 'RATE_LIMIT', 'SOURCE_DISABLED', 'INVALID_SCHEMA',
                  'FIELD_CONFLICT', 'NO_PRICE', 'NETWORK_ERROR', 'BUDGET_EXHAUSTED',
                  'BLOCKED', 'HTTP_ERROR']
    message: Text
    retryable: StrictBool

    @model_validator(mode='after')
    def documented_refusal(self):
        prefix = _REFUSAL_PREFIXES.get(self.code)
        if prefix is not None and (self.retryable or not prefix.match(self.message)):
            raise ValueError(f'{self.code} errors need their documented message prefix '
                             'and cannot be retryable')
        return self


class ToolResult(Contract):
    status: Literal['ok', 'partial', 'error']
    data: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: list[Text] = Field(default_factory=list)
    errors: list[ToolError] = Field(default_factory=list)
    duration_ms: Count

    @model_validator(mode='after')
    def truthful_outcome(self):
        if (self.status == 'ok' and self.errors) or (self.status != 'ok' and not self.errors):
            raise ValueError('Tool status and errors must agree')
        return self


class OfferIssue(Model):
    offer_id: Text
    reasons: list[Text] = Field(min_length=1)


class SourceCoverage(Model):
    source_id: Text
    status: Literal['ok', 'partial', 'error']
    candidates_seen: Count
    errors: list[ToolError] = Field(default_factory=list)

    @model_validator(mode='after')
    def truthful_outcome(self):
        if (self.status == 'ok' and self.errors) or (self.status != 'ok' and not self.errors):
            raise ValueError('Source coverage status and errors must agree')
        return self


class BudgetUsage(Model):
    elapsed_ms: Count
    external_calls: Count
    planner_turns: Count
    spend: Money


class Recommendation(Model):
    offer: Offer
    payable_total: Money
    verified_at: UtcTime

    @model_validator(mode='after')
    def resolved_costs(self):
        if any(c.state == 'unknown' for c in self.offer.costs):
            raise ValueError('Unknown mandatory costs cannot enter verified recommendations')
        return self


class RunResult(Contract):
    run_id: Text
    request_id: Text
    status: Literal['complete', 'partial', 'abstained', 'cancelled', 'error']
    stop_reason: Text
    started_at: UtcTime
    finished_at: UtcTime
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=3)
    unresolved: list[OfferIssue] = Field(default_factory=list)
    rejected: list[OfferIssue] = Field(default_factory=list)
    source_coverage: list[SourceCoverage] = Field(default_factory=list)
    usage: BudgetUsage

    @model_validator(mode='after')
    def terminal_result(self):
        if self.finished_at < self.started_at:
            raise ValueError('Run must finish after it starts')
        if ((self.status == 'complete' and not self.recommendations)
                or (self.status in ('abstained', 'cancelled', 'error') and self.recommendations)):
            raise ValueError('Run status must agree with its recommendations')
        for rec in self.recommendations:
            if not self.started_at <= rec.verified_at <= self.finished_at:
                raise ValueError('Final verification must occur inside this run')
        ids = [rec.offer.offer_id for rec in self.recommendations]
        if len(ids) != len(set(ids)):
            raise ValueError('Recommendations must have distinct offer IDs')
        return self
