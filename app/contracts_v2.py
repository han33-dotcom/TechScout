"""Shared full-product shopping contracts. The live v1 API and storage use app.contracts."""
from collections.abc import Mapping
from decimal import Context, Inexact, MAX_EMAX, MIN_EMIN, ROUND_CEILING
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StringConstraints, model_validator

from app import contracts as v1
from app.contracts import (
    Amount, ArrivalWindow, Count, Deadline, Fact, HttpsUrl, Model, Money, PositiveInt, Sim,
    Text, UtcTime, Warranty, WarrantyRequirement,
)

Category = Literal['phone', 'laptop', 'tablet', 'monitor', 'audio']
Country = Annotated[str, StringConstraints(strict=True, pattern=r'^[A-Z]{2}$')]
Condition = Literal['new', 'used', 'refurbished']
Payment = Literal['cash', 'card']
Origin = Literal['bootstrap', 'maintenance', 'runtime']
_MONEY_PRECISION = 200
ChoicePriority = Literal['price', 'performance', 'battery', 'portability', 'display', 'audio',
                         'delivery', 'warranty']
TargetKind = Literal['product', 'variant', 'offer', 'route']


class Contract(Model):
    schema_version: Literal[2] = 2


class PhoneRequirements(Model):
    category: Literal['phone']
    storage_gb: PositiveInt | None = None
    sim: Sim | None = None
    regions: tuple[Text, ...] = ()


class LaptopRequirements(Model):
    category: Literal['laptop']
    min_ram_gb: PositiveInt | None = None
    min_storage_gb: PositiveInt | None = None
    cpu: Text | None = None
    gpu: Text | None = None
    operating_system: Text | None = None


class TabletRequirements(Model):
    category: Literal['tablet']
    min_storage_gb: PositiveInt | None = None
    connectivity: Literal['wifi', 'cellular'] | None = None
    stylus_support: StrictBool | None = None


class MonitorRequirements(Model):
    category: Literal['monitor']
    min_width_px: PositiveInt | None = None
    min_height_px: PositiveInt | None = None
    min_refresh_hz: PositiveInt | None = None
    min_diagonal_inches: Annotated[Amount, Field(gt=0)] | None = None


class AudioRequirements(Model):
    category: Literal['audio']
    form_factor: Literal['headphones', 'earbuds'] | None = None
    connectivity: Literal['wired', 'wireless', 'both'] | None = None
    active_noise_cancellation: StrictBool | None = None


Requirements = Annotated[
    PhoneRequirements | LaptopRequirements | TabletRequirements | MonitorRequirements | AudioRequirements,
    Field(discriminator='category'),
]


class ModelIntent(Model):
    kind: Literal['exact_model']
    manufacturer: Text | None = None
    model: Text


class LinkIntent(Model):
    kind: Literal['product_link']
    url: HttpsUrl  # A lead; source authorization occurs at dispatch.


class NeedsIntent(Model):
    kind: Literal['needs']
    description: Text
    required_software: tuple[Text, ...] = ()


Intent = Annotated[ModelIntent | LinkIntent | NeedsIntent, Field(discriminator='kind')]


class Budget(Model):
    target: Money | None = None
    maximum: Money | None = None

    @model_validator(mode='after')
    def coherent_amounts(self):
        if self.target is None and self.maximum is None:
            raise ValueError('A budget needs a target, a maximum, or both')
        if any(m.amount <= 0 for m in (self.target, self.maximum) if m is not None):
            raise ValueError('Budget amounts must be positive')
        if self.target and self.maximum and (
            self.target.currency != self.maximum.currency or self.target.amount > self.maximum.amount
        ):
            raise ValueError('Budget target must fit the maximum in the same currency')
        return self


class MarketScope(Model):
    include_international: StrictBool = False
    international_markets: tuple[Country, ...] = ()

    @model_validator(mode='after')
    def explicit_international_scope(self):
        markets = self.international_markets
        if 'LB' in markets or len(set(markets)) != len(markets):
            raise ValueError('International markets must be distinct and overseas')
        if markets and not self.include_international:
            raise ValueError('Selecting overseas markets requires explicit opt-in')
        return self

    def allows(self, market: str) -> bool:
        return market == 'LB' or (
            self.include_international
            and (not self.international_markets or market in self.international_markets)
        )


class Location(Model):
    country: Country
    city: Text | None = None
    postal_code: Text | None = None


class DeliveryDestination(Location):
    country: Literal['LB'] = 'LB'


class FulfillmentRequest(Model):
    destination: DeliveryDestination = Field(default_factory=DeliveryDestination)
    mode: Literal['any', 'pickup', 'delivery'] = 'any'
    branch_id: Text | None = None
    deadline: Deadline | None = None

    @model_validator(mode='after')
    def branch_requires_pickup(self):
        if self.branch_id is not None and self.mode != 'pickup':
            raise ValueError('A branch constraint requires pickup')
        return self


class ShoppingRequest(Contract):
    request_id: Text
    revision: PositiveInt = 1
    intent: Intent
    refinement_note: Text | None = None  # Additional buyer wording; never replaces existing constraints.
    requirements: Requirements
    budget: Budget | None = None
    preferences: tuple[ChoicePriority, ...] = ()
    allowed_colors: tuple[Text, ...] = ()
    payment: Payment | None = None
    allowed_conditions: Annotated[tuple[Condition, ...], Field(min_length=1)] = ('new',)
    market_scope: MarketScope = Field(default_factory=MarketScope)
    fulfillment: FulfillmentRequest = Field(default_factory=FulfillmentRequest)
    warranty_requirement: WarrantyRequirement | None = None

    @model_validator(mode='after')
    def unique_choices(self):
        if len(set(self.allowed_conditions)) != len(self.allowed_conditions):
            raise ValueError('Allowed conditions must be distinct')
        if len(set(self.preferences)) != len(self.preferences):
            raise ValueError('Preferences must be distinct')
        return self

    def refine(self, changes: Mapping[str, object]) -> 'ShoppingRequest':
        """Apply explicit whole-field edits. A budget edit may change either bound independently."""
        if {'schema_version', 'request_id', 'revision'} & changes.keys():
            raise ValueError('Refinement retains the request ID and advances the revision')
        data = self.model_dump(mode='python')
        data.update(changes)
        if self.budget is not None and isinstance(changes.get('budget'), Mapping):
            data['budget'] = self.budget.model_dump() | dict(changes['budget'])
        data['revision'] = self.revision + 1
        return ShoppingRequest.model_validate(data)


class Product(Model):
    product_id: Text
    category: Category
    manufacturer: Text
    model: Text


class PhoneSpecs(Model):
    category: Literal['phone']
    storage_gb: Fact[PositiveInt]
    sim: Fact[Sim]
    region: Fact[Text]


class LaptopSpecs(Model):
    category: Literal['laptop']
    cpu: Fact[Text]
    ram_gb: Fact[PositiveInt]
    storage_gb: Fact[PositiveInt]
    gpu: Fact[Text] | None = None
    operating_system: Fact[Text] | None = None
    display_inches: Fact[Annotated[Amount, Field(gt=0)]] | None = None
    display_width_px: Fact[PositiveInt] | None = None
    display_height_px: Fact[PositiveInt] | None = None
    display_refresh_hz: Fact[PositiveInt] | None = None
    display_panel: Fact[Text] | None = None
    display_touchscreen: Fact[StrictBool] | None = None


class TabletSpecs(Model):
    category: Literal['tablet']
    storage_gb: Fact[PositiveInt]
    connectivity: Fact[Literal['wifi', 'cellular']]
    stylus_support: Fact[StrictBool] | None = None


class MonitorSpecs(Model):
    category: Literal['monitor']
    width_px: Fact[PositiveInt]
    height_px: Fact[PositiveInt]
    refresh_hz: Fact[PositiveInt]
    diagonal_inches: Fact[Annotated[Amount, Field(gt=0)]]
    panel: Fact[Text] | None = None


class AudioSpecs(Model):
    category: Literal['audio']
    form_factor: Fact[Literal['headphones', 'earbuds']]
    connectivity: Fact[Literal['wired', 'wireless', 'both']]
    active_noise_cancellation: Fact[StrictBool] | None = None


Specs = Annotated[PhoneSpecs | LaptopSpecs | TabletSpecs | MonitorSpecs | AudioSpecs,
                  Field(discriminator='category')]


class Variant(Contract):
    variant_id: Text
    product: Product
    specs: Specs
    color: Fact[Text] | None = None

    @model_validator(mode='after')
    def category_matches(self):
        if self.product.category != self.specs.category:
            raise ValueError('Variant specifications must match the product category')
        return self


class Seller(Model):
    seller_id: Text
    name: Text
    market: Country


class PickupRoute(Model):
    kind: Literal['pickup']
    route_id: Text
    destination: DeliveryDestination
    branch_id: Text


class DirectRoute(Model):
    kind: Literal['direct']
    route_id: Text
    origin_country: Country
    destination: DeliveryDestination
    item_eligible: Fact[StrictBool]


class DeliveryLeg(Model):
    origin: Location
    destination: Location
    item_eligible: Fact[StrictBool]


class Package(Model):
    weight_g: PositiveInt | None = None
    length_mm: PositiveInt | None = None
    width_mm: PositiveInt | None = None
    height_mm: PositiveInt | None = None


class ForwardedRoute(Model):
    kind: Literal['forwarded']
    route_id: Text
    retailer_leg: DeliveryLeg
    forwarding_leg: DeliveryLeg
    forwarder_id: Text
    package: Package | None = None

    @model_validator(mode='after')
    def connected_legs(self):
        hub = self.retailer_leg.destination
        if (hub != self.forwarding_leg.origin or hub.country == 'LB'
                or self.forwarding_leg.destination.country != 'LB'):
            raise ValueError('Forwarding must connect through an overseas hub to Lebanon')
        return self

    @property
    def destination(self) -> Location:
        return self.forwarding_leg.destination


Route = Annotated[PickupRoute | DirectRoute | ForwardedRoute, Field(discriminator='kind')]


class RunReference(Model):
    run_id: Text
    run_revision: PositiveInt
    request_id: Text
    request_revision: PositiveInt


class Evidence(v1.Evidence):
    schema_version: Literal[2] = 2
    origin: Origin
    corpus_version: Text
    index_version: Text | None = None
    run: RunReference | None = None

    @model_validator(mode='after')
    def buyer_run_is_runtime(self):
        if self.run is not None and self.origin != 'runtime':
            raise ValueError('Buyer-run evidence must come from runtime')
        return self


class Charge(Contract):
    kind: Literal['tax', 'shipping', 'duty', 'customs_handling', 'payment', 'other']
    leg: Literal['order', 'retailer', 'forwarding'] = 'order'
    state: Literal['additional', 'included', 'unknown', 'not_applicable']
    amount: Money | None = None
    reason: Text | None = None
    conditions: tuple[Text, ...] = ()
    evidence_ids: tuple[Text, ...] = ()
    valid_until: UtcTime | None = None

    @model_validator(mode='after')
    def coherent_charge(self):
        if (self.amount is not None) != (self.state == 'additional'):
            raise ValueError('Only an additional charge carries an amount')
        if self.state in {'unknown', 'not_applicable'} and self.reason is None:
            raise ValueError('Unpublished or inapplicable costs require a reason')
        if self.state != 'unknown' and not self.evidence_ids:
            raise ValueError('Resolved costs require evidence')
        return self


def _references(value, path=''):
    """Offer evidence addresses model fields, with charges keyed by leg and kind."""
    if isinstance(value, (Fact, Charge)):
        for evidence_id in value.evidence_ids:
            yield path, evidence_id
    elif isinstance(value, Model):
        for field in type(value).model_fields:
            if field != 'evidence':
                child = f'{path}.{field}' if path else field
                yield from _references(getattr(value, field), child)
    elif isinstance(value, tuple):
        for item in value:
            if isinstance(item, Charge):
                yield from _references(item, f'{path}.{item.leg}.{item.kind}')


class Offer(Contract):
    offer_id: Text
    retailer_id: Text
    source_id: Text
    source_product_id: Text
    source_variant_id: Text
    seller: Seller
    fulfiller_id: Text | None = None
    variant: Variant
    listing_url: HttpsUrl
    route: Route
    price: Fact[Money]
    stock: Fact[StrictBool]
    purchasable: Fact[StrictBool]
    condition: Fact[Condition]
    activation: Fact[Literal['unactivated', 'activated']] | None = None
    payment: Fact[Payment] | None = None
    warranty: Warranty | None = None
    arrival: Fact[ArrivalWindow] | None = None
    costs: tuple[Charge, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode='after')
    def bind_offer_evidence(self):
        if isinstance(self.route, PickupRoute) and self.seller.market != 'LB':
            raise ValueError('Pickup requires a domestic seller')
        if isinstance(self.route, ForwardedRoute) and self.seller.market == 'LB':
            raise ValueError('Forwarding requires an international seller')
        if not isinstance(self.route, ForwardedRoute) and any(c.leg != 'order' for c in self.costs):
            raise ValueError('Forwarding legs require a forwarded route')
        if len({(c.kind, c.leg) for c in self.costs}) != len(self.costs):
            raise ValueError('Each cost kind and leg has one resolved or unresolved component')
        records = {item.evidence_id: item for item in self.evidence}
        if len(records) != len(self.evidence):
            raise ValueError('Evidence IDs must be unique')
        for record in self.evidence:
            self._check_evidence_scope(record)
        for path, evidence_id in _references(self):
            record = records.get(evidence_id)
            if record is None or record.field_path != path:
                raise ValueError('Evidence must support its referring field')
            if path == 'stock' and isinstance(self.route, PickupRoute):
                if (record.scope != 'branch' or record.variant_id != self.source_variant_id
                        or record.product_id != self.source_product_id):
                    raise ValueError('Pickup stock requires selected variant and branch evidence')
            elif path in {'price', 'stock', 'purchasable', 'condition', 'activation', 'variant.color'} or (
                path.startswith('variant.specs.')
            ):
                if record.scope != 'variant':
                    raise ValueError('Selected-variant facts require scoped variant evidence')
            elif path.startswith('route.') and record.scope != 'route':
                raise ValueError('Route facts require route-scoped evidence')
            elif path.endswith(('.shipping', '.duty', '.customs_handling')) and path.startswith('costs.'):
                if record.scope != 'route':
                    raise ValueError('Route-specific charges require evidence for that route')
        return self

    def _check_evidence_scope(self, record: Evidence) -> None:
        if record.source_id != self.source_id:
            raise ValueError('Offer evidence must belong to its source')
        if (record.product_id not in (None, self.source_product_id)
                or record.variant_id not in (None, self.source_variant_id)):
            raise ValueError('Offer evidence must belong to its source listing and variant')
        if record.route_id is not None and record.route_id != self.route.route_id:
            raise ValueError('Route evidence must match the selected route')
        branch_id = self.route.branch_id if isinstance(self.route, PickupRoute) else None
        if record.branch_id is not None and record.branch_id != branch_id:
            raise ValueError('Branch evidence must match the selected pickup branch')


def _money_context() -> Context:
    # Match the v1 calculator's bounded precision and conservative rounding.
    return Context(prec=_MONEY_PRECISION, rounding=ROUND_CEILING, Emax=MAX_EMAX, Emin=MIN_EMIN,
                   traps=[])


class FxConversion(Model):
    base: Money
    quote: Money
    rate: Annotated[Amount, Field(gt=0)]
    evidence_id: Text
    observed_at: UtcTime
    valid_until: UtcTime | None = None

    @model_validator(mode='after')
    def coherent_exchange(self):
        if self.base.currency == self.quote.currency:
            raise ValueError('FX requires different currencies')
        if self.valid_until is not None and self.valid_until < self.observed_at:
            raise ValueError('FX cannot expire before it was observed')
        converted = _money_context().multiply(self.base.amount, self.rate)
        if not converted.is_finite() or self.quote.amount < converted:
            raise ValueError('Budget conversion cannot understate a payable total')
        return self


class VerifiedCost(Model):
    status: Literal['verified']
    total: Money
    budget_conversion: FxConversion | None = None

    @model_validator(mode='after')
    def conversion_uses_total(self):
        if self.budget_conversion is not None and self.budget_conversion.base != self.total:
            raise ValueError('Budget conversion must start with this payable total')
        return self


class EstimatedCost(Model):
    status: Literal['estimated']
    lower: Money
    upper: Money
    assumptions: Annotated[tuple[Text, ...], Field(min_length=1)]

    @model_validator(mode='after')
    def valid_range(self):
        if self.lower.currency != self.upper.currency or self.lower.amount > self.upper.amount:
            raise ValueError('A cost range requires one currency and ordered endpoints')
        return self


class IncompleteCost(Model):
    status: Literal['incomplete']
    known_subtotal: Money | None = None
    missing: Annotated[tuple[Text, ...], Field(min_length=1)]


Cost = Annotated[VerifiedCost | EstimatedCost | IncompleteCost, Field(discriminator='status')]


class Verification(Model):
    checked_at: UtcTime
    run: RunReference


class VerifiedOffer(Model):
    offer: Offer
    cost: VerifiedCost
    verification: Verification

    @model_validator(mode='after')
    def currently_checked(self):
        offer = self.offer
        if (offer.price.state != 'known' or offer.price.value.amount <= 0
                or offer.stock.value is not True or offer.purchasable.value is not True
                or offer.condition.state != 'known' or any(c.state == 'unknown' for c in offer.costs)):
            raise ValueError('A verified offer needs a positive price, purchasable stock, condition and resolved costs')
        if self.cost.total.currency != offer.price.value.currency or (
            self.cost.total.amount < offer.price.value.amount
        ):
            raise ValueError('A payable total must cover the device price in its currency')
        self._check_costs()
        if isinstance(offer.route, DirectRoute) and offer.route.item_eligible.value is not True:
            raise ValueError('Direct delivery requires item eligibility')
        if isinstance(offer.route, ForwardedRoute) and not (
            offer.route.retailer_leg.item_eligible.value is True
            and offer.route.forwarding_leg.item_eligible.value is True
        ):
            raise ValueError('Forwarding requires item eligibility on both legs')
        records = {item.evidence_id: item for item in offer.evidence}
        for evidence_id in (*offer.price.evidence_ids, *offer.stock.evidence_ids):
            record = records[evidence_id]
            if record.run != self.verification.run or record.observed_at > self.verification.checked_at:
                raise ValueError('Finalist price and stock need evidence checked in the current run')
        return self

    def _check_costs(self) -> None:
        offer = self.offer
        checked_at = self.verification.checked_at
        kinds = {(charge.leg, charge.kind) for charge in offer.costs}
        required = {('order', 'tax')}
        if isinstance(offer.route, DirectRoute):
            required.add(('order', 'shipping'))
        elif isinstance(offer.route, ForwardedRoute):
            required.update({('retailer', 'shipping'), ('forwarding', 'shipping')})
        if offer.seller.market != 'LB':
            required.update({('order', 'duty'), ('order', 'customs_handling')})
        if not required <= kinds:
            raise ValueError('A payable total needs evidence for mandatory route charges')
        if any(c.valid_until and c.valid_until < checked_at for c in offer.costs):
            raise ValueError('Decisive cost quotes must be current')
        conversion = self.cost.budget_conversion
        if conversion and (conversion.observed_at > checked_at
                           or (conversion.valid_until and conversion.valid_until < checked_at)):
            raise ValueError('Budget conversion requires a current FX quote')
        # The cost engine resolves conditional and foreign-currency components. For an
        # unconditional single-currency offer, the wire contract can check the sum exactly.
        additional = [c.amount for c in offer.costs if c.amount is not None]
        price = offer.price.value
        if any(c.conditions for c in offer.costs) or any(m.currency != price.currency for m in additional):
            return
        context = _money_context()
        total = price.amount
        for charge in additional:
            total = context.add(total, charge.amount)
        if context.flags[Inexact] or total != self.cost.total.amount:
            raise ValueError('A payable total must include all applicable additional charges')


class Recommendation(Model):
    primary: VerifiedOffer
    additional_sellers: tuple[VerifiedOffer, ...] = ()

    @model_validator(mode='after')
    def same_configuration(self):
        offer = self.primary.offer
        sellers = {offer.seller.seller_id}
        for alternative in self.additional_sellers:
            variant = alternative.offer.variant
            if (variant.variant_id != offer.variant.variant_id
                    or variant.product.product_id != offer.variant.product.product_id):
                raise ValueError('Grouped sellers must offer the same configuration')
            if alternative.offer.seller.seller_id in sellers:
                raise ValueError('A seller can appear once in a recommendation group')
            sellers.add(alternative.offer.seller.seller_id)
        return self

    def offers(self) -> tuple[VerifiedOffer, ...]:
        return (self.primary, *self.additional_sellers)


class ProvisionalCandidate(Model):
    status: Literal['provisional'] = 'provisional'
    offer: Offer
    observed_at: UtcTime

    @model_validator(mode='after')
    def original_timestamp(self):
        if not any(item.observed_at == self.observed_at for item in self.offer.evidence):
            raise ValueError('Provisional observation time must come from original evidence')
        return self


class UncertainAlternative(Model):
    status: Literal['uncertain'] = 'uncertain'
    offer: Offer
    reasons: Annotated[tuple[Text, ...], Field(min_length=1)]
    cost: Cost | None = None


class CoverageCounts(Model):
    discovered: Count = 0
    attempted: Count = 0
    retrieved: Count = 0
    qualifying: Count = 0

    @model_validator(mode='after')
    def ordered_counts(self):
        if not self.discovered >= self.attempted >= self.retrieved >= self.qualifying:
            raise ValueError('Retailer coverage must decrease from discovered to qualifying')
        return self


class Clarification(Model):
    clarification_id: Text
    question: Text
    material_reason: Text


class SourceOutcome(Model):
    source_id: Text
    name: Text
    status: Literal['checked', 'failed', 'unchecked']
    reason: Text | None = None

    @model_validator(mode='after')
    def failed_reason(self):
        if self.status == 'failed' and self.reason is None:
            raise ValueError('A failed source requires a reason')
        return self


class RunSnapshot(Contract):
    run: RunReference
    request: ShoppingRequest
    status: Literal['queued', 'running', 'clarification', 'complete', 'partial',
                    'abstained', 'cancelled', 'error']
    phase: Literal['interpret', 'discover', 'research', 'evaluate', 'verify', 'write']
    started_at: UtcTime
    updated_at: UtcTime
    finished_at: UtcTime | None = None
    stop_reason: Text | None = None
    clarification: Clarification | None = None
    coverage: CoverageCounts = Field(default_factory=CoverageCounts)
    source_outcomes: tuple[SourceOutcome, ...] = ()
    provisional: tuple[ProvisionalCandidate, ...] = ()
    recommendations: Annotated[tuple[Recommendation, ...], Field(max_length=3)] = ()
    uncertain: tuple[UncertainAlternative, ...] = ()

    @model_validator(mode='after')
    def coherent_results(self):
        if len({source.source_id for source in self.source_outcomes}) != len(self.source_outcomes):
            raise ValueError("Source outcomes must be unique")
        if (self.run.request_id, self.run.request_revision) != (self.request.request_id, self.request.revision):
            raise ValueError('Run must refer to the displayed request revision')
        terminal = self.status in {'complete', 'partial', 'abstained', 'cancelled', 'error'}
        if (self.updated_at < self.started_at or terminal != (self.finished_at is not None)
                or (self.finished_at is not None and not self.started_at <= self.finished_at <= self.updated_at)):
            raise ValueError('Run timestamps must match its lifecycle')
        if terminal and self.stop_reason is None:
            raise ValueError('Terminal runs require a stop reason')
        if (self.clarification is not None) != (self.status == 'clarification'):
            raise ValueError('Clarifications require a waiting run')
        if ((self.status == 'complete' and not self.recommendations)
                or (self.status not in {'complete', 'partial'} and self.recommendations)):
            raise ValueError('Recommendations require a complete or partial outcome')
        verified = [offer for group in self.recommendations for offer in group.offers()]
        if len({item.offer.retailer_id for item in verified}) > self.coverage.qualifying:
            raise ValueError('Recommended retailers must be counted as qualifying')
        if self.request.intent.kind == 'needs':
            products = [item.primary.offer.variant.product.product_id for item in self.recommendations]
            if len(products) != len(set(products)):
                raise ValueError('Needs-based recommendations must cover distinct models')
        for item in verified:
            self._check_verified_offer(item)
        visible = [item.offer for item in (*verified, *self.provisional, *self.uncertain)]
        if any(not self._in_request_scope(offer) for offer in visible):
            raise ValueError('Result offers must match the requested category, market and known condition')
        ids = [offer.offer_id for offer in visible]
        if len(ids) != len(set(ids)):
            raise ValueError('An offer can belong to only one result group')
        if any(item.observed_at > self.updated_at for item in self.provisional):
            raise ValueError('Provisional timestamps cannot be newer than the run snapshot')
        return self

    def _in_request_scope(self, offer: Offer) -> bool:
        request = self.request
        return (offer.variant.product.category == request.requirements.category
                and request.market_scope.allows(offer.seller.market)
                and (offer.condition.state != 'known' or offer.condition.value in request.allowed_conditions))

    def _check_verified_offer(self, item: VerifiedOffer) -> None:
        if (self.finished_at is None or item.verification.run != self.run
                or not self.started_at <= item.verification.checked_at <= self.finished_at):
            raise ValueError('Verification must belong to this run and occur before it finishes')
        decisive_ids = {*item.offer.price.evidence_ids, *item.offer.stock.evidence_ids}
        if any(record.observed_at < self.started_at
               for record in item.offer.evidence if record.evidence_id in decisive_ids):
            raise ValueError('Finalist price or stock evidence predates this run')
        offer, request = item.offer, self.request
        fulfillment = request.fulfillment
        mode = 'pickup' if isinstance(offer.route, PickupRoute) else 'delivery'
        if fulfillment.mode not in ('any', mode) or (
            fulfillment.branch_id is not None and (
                not isinstance(offer.route, PickupRoute) or fulfillment.branch_id != offer.route.branch_id
            )
        ):
            raise ValueError('A recommendation must obey the requested fulfillment')
        for field in ('city', 'postal_code'):
            wanted = getattr(fulfillment.destination, field)
            actual = getattr(offer.route.destination, field)
            if wanted and (actual is None or wanted.casefold() != actual.casefold()):
                raise ValueError('A recommendation must match the requested destination')
        if request.budget and request.budget.maximum:
            maximum = request.budget.maximum
            conversion = item.cost.budget_conversion
            cost = item.cost.total
            if cost.currency != maximum.currency and conversion is not None:
                cost = conversion.quote
            if cost.currency != maximum.currency or cost.amount > maximum.amount:
                raise ValueError('A recommendation must meet the hard budget in its currency')


class ComparisonWorkspace(Contract):
    current: RunSnapshot
    previous: RunSnapshot | None = None

    @model_validator(mode='after')
    def older_revision(self):
        if self.previous and (
            self.previous.request.request_id != self.current.request.request_id
            or self.previous.request.revision >= self.current.request.revision
            or self.previous.run.run_id == self.current.run.run_id
        ):
            raise ValueError('Previous results must belong to an older run of the same request')
        return self


class MatchAssessment(Contract):
    left_offer_id: Text
    right_offer_id: Text
    left_variant_id: Text
    right_variant_id: Text
    outcome: Literal['confirmed_equivalent', 'different_configuration', 'unresolved']
    reasons: Annotated[tuple[Text, ...], Field(min_length=1)]
    evidence_ids: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def evidence_for_decisive_match(self):
        if self.left_offer_id == self.right_offer_id:
            raise ValueError('A match compares two distinct seller observations')
        if self.outcome == 'different_configuration' and self.left_variant_id == self.right_variant_id:
            raise ValueError('Distinct configurations need distinct variant IDs')
        if self.outcome != 'unresolved' and not self.evidence_ids:
            raise ValueError('Decisive identity decisions require evidence')
        return self


class FindingClaim(Model):
    target: TargetKind
    target_id: Text
    field_path: Text
    fact: Fact[Money | ArrivalWindow | StrictBool | Amount | Text]


class AgentFinding(Contract):
    finding_id: Text
    task_id: Text
    run: RunReference
    role: Literal['orchestrator', 'researcher', 'evaluator']
    claims: tuple[FindingClaim, ...] = ()
    gaps: tuple[Text, ...] = ()


def read_request(payload: Mapping[str, object]) -> v1.ShoppingRequest | ShoppingRequest:
    return _read_versioned(payload, v1.ShoppingRequest, ShoppingRequest)


def read_offer(payload: Mapping[str, object]) -> v1.Offer | Offer:
    return _read_versioned(payload, v1.Offer, Offer)


def read_run(payload: Mapping[str, object]) -> v1.RunResult | RunSnapshot:
    return _read_versioned(payload, v1.RunResult, RunSnapshot)


def _read_versioned(payload, legacy, current):
    version = payload.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported contract schema version')
    return (legacy if version == 1 else current).model_validate(payload)
