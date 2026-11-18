"""International landed costs (#63): what an overseas offer costs once it reaches the buyer in Lebanon.

Deterministic rules only. The input is the v2 offer plus RouteInputs: the shipped item's evidenced
battery, weight and size, and route quotes. A route quote is one provider's dated statement for the
offer's route: a retailer's checkout figure, a forwarder's calculator result, a customs estimate or
a courier's price. Every quote line names one charge slot, a leg plus a component (see _SLOTS), so the
same fee stated by two providers lands in one slot. The offer's own listed charges (Offer.costs) are
read as the retailer's statements through a fixed mapping (_listing_slot); nothing listed is dropped.

A slot is settled from the lines that cover the actual item now:
- A line counts only when its quote is current and covers the item. Current: before its stated
  valid_until, or, with no stated validity, read in this run (observed at or after run_started_at);
  a quote that ended is never current. A receipt from the offer's own source must belong to its listing.
- Covering: retailer and order charges need a quote that names the offer's seller and listing variant.
  Only the retailer's own receipt for that listing (provider = retailer_id, receipt from the offer's
  source for that listing) speaks for the item itself. Every other quote that carries the item
  (forwarding leg or final delivery) states the package it priced, the item's evidenced weight, size
  and battery must fit it, and it must confirm a battery it carries; its customs lines need a declared
  value of at least the device price. On a forwarded route the retailer side (retailer or seller, and
  the offer's listed charges) ships only to the hub, so it settles only the retailer leg and the order.
- A provider that refuses the item (its service does not take the item's battery, or the item is over
  its weight or size limit) cannot price it. A current, matching quote that refuses makes the route
  unsupported (the offer is rejected, not merely uncertain) unless another current quote that covers
  the item addresses that leg's carriage, whether priced, estimated or stated without an amount; the
  offer's listed charges never count as such a quote, and they cannot settle carriage on a refused leg.
  Quotes from one service (provider and service name) that both refuse and accept the item contradict
  each other, so neither settles anything. A refusal from any other quote is only a doubt.
- Lines that agree count once, however many providers list them. Lines that disagree (other amounts,
  or included against additional) leave the slot open. Several separately listed 'other' charges are
  never merged, as in the domestic engine, and two listed charges of the offer that fall on one slot
  leave it open. A verified line outranks an estimate. A listing that does not publish a charge gives
  way to any current line; a current quote that states a charge without an amount gives way only to a
  stated amount, never to another provider's included or not applicable.
- Required slots must settle for every route: retailer shipping, customs duty, import VAT, customs
  clearance, final delivery in Lebanon and sales tax on the order; a forwarded route adds forwarder
  freight, and a dangerous-goods charge unless the item is evidenced to carry no battery. Any other
  slot counts only when a provider states it, but once stated it must settle. An open slot is
  missing, never zero.

Amounts in another currency convert to the price currency only through a sourced rate: an FxRate
whose evidence record (field path 'fx.<base>.<quote>', same observation time, cited by no rate with
another value) is supplied and whose observation is within FX_MAX_AGE before now. As in the domestic
budget check, the rate must be quoted in the needed direction, the least favourable fresh rate is used
and rounding is upward, so conversion never lowers a total. The result is the shared v2 cost union:
verified only when every slot settled from current quotes; estimated when some slot rests on an
explicit estimate; otherwise incomplete with its known subtotal and the missing charges by name. A
verified total is also converted into the budget currency; a hard maximum in another currency needs
that conversion. The total excludes any fee the buyer's own card issuer adds for paying in a foreign
currency, as the domestic total excludes travel; a provider's stated currency or payment fee is a slot
like any other. Reasons use fixed wording plus contract values (IDs, times); only an estimate's
assumption carries the producer's own note.
"""
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from app import contracts_v2 as v2
from app.contracts import Fact, Model, Money, PositiveInt, Text, UtcTime
from app.costs import FX_MAX_AGE, FxRate, _aware, _context, _rate, _sum, supports_domestic_cost

Leg = Literal['retailer', 'forwarding', 'import', 'final', 'order']
Component = Literal['shipping', 'consolidation', 'handling', 'storage', 'insurance', 'dangerous_goods',
                    'duty', 'import_vat', 'clearance', 'delivery', 'sales_tax', 'payment_fee', 'fx_fee',
                    'other']
Battery = Literal['none', 'installed', 'packed_with', 'loose']
Support = Literal['supported', 'unsupported', 'unresolved']

# Every charge slot in display order, with the name shown to buyers. 'retailer' is whatever the
# retailer charges to ship its order: to Lebanon on a direct route, to the hub on a forwarded one.
_NAMES = {
    ('retailer', 'shipping'): 'retailer shipping',
    ('retailer', 'handling'): 'retailer handling fee',
    ('retailer', 'insurance'): 'retailer shipping insurance',
    ('retailer', 'dangerous_goods'): 'retailer battery shipping fee',
    ('retailer', 'other'): 'other retailer charge',
    ('forwarding', 'shipping'): 'forwarder freight to Lebanon',
    ('forwarding', 'consolidation'): 'forwarder consolidation fee',
    ('forwarding', 'handling'): 'forwarder handling fee',
    ('forwarding', 'storage'): 'forwarder storage fee',
    ('forwarding', 'insurance'): 'forwarding insurance',
    ('forwarding', 'dangerous_goods'): 'forwarder battery (dangerous goods) fee',
    ('forwarding', 'other'): 'other forwarder charge',
    ('import', 'duty'): 'customs duty',
    ('import', 'import_vat'): 'import VAT',
    ('import', 'clearance'): 'customs clearance',
    ('final', 'delivery'): 'final delivery in Lebanon',
    ('order', 'sales_tax'): 'sales tax on the order',
    ('order', 'payment_fee'): 'payment fee',
    ('order', 'fx_fee'): 'currency conversion fee',
    ('order', 'other'): 'other order charge',
}
_SLOTS = tuple(_NAMES)
# The slot that carries the item on each transport leg; a refusal matters only while it is open.
_CARRIES = {'retailer': ('retailer', 'shipping'), 'forwarding': ('forwarding', 'shipping'),
            'final': ('final', 'delivery')}
_ALWAYS = (('retailer', 'shipping'), ('import', 'duty'), ('import', 'import_vat'), ('import', 'clearance'),
           ('final', 'delivery'), ('order', 'sales_tax'))
_NO_PRICE = 'the device price is not established'
# On a forwarded route the retailer ships only to the hub: it cannot settle freight, import or final delivery.
_RETAILER_LEGS = {'retailer', 'order'}


def _label(slot, forwarded: bool) -> str:
    if slot == ('retailer', 'shipping'):
        return 'retailer shipping to the forwarding address' if forwarded else 'retailer shipping to Lebanon'
    return _NAMES[slot]


class Dimensions(Model):
    length_mm: PositiveInt
    width_mm: PositiveInt
    height_mm: PositiveInt

    def sides(self) -> tuple[int, int, int]:
        return tuple(sorted((self.length_mm, self.width_mm, self.height_mm)))

    def fits_in(self, other: 'Dimensions') -> bool:
        return all(mine <= theirs for mine, theirs in zip(self.sides(), other.sides()))


class ItemFacts(Model):
    """The shipped item as evidenced. Evidence field paths: package.battery, package.weight_g and
    package.dimensions. A known fact needs every receipt supplied; otherwise it counts as unknown."""
    battery: Fact[Battery]
    weight_g: Fact[PositiveInt]
    dimensions: Fact[Dimensions]


def _not_supplied() -> ItemFacts:
    unknown = {'state': 'unknown', 'reason': 'not supplied'}
    return ItemFacts.model_validate({'battery': unknown, 'weight_g': unknown, 'dimensions': unknown})


class QuotedPackage(Model):
    """The package a provider priced, with the battery configuration declared to it."""
    weight_g: PositiveInt
    battery: Battery
    dimensions: Dimensions | None = None


class ServiceLimits(Model):
    max_weight_g: PositiveInt | None = None
    max_side_mm: PositiveInt | None = None
    max_dimension_sum_mm: PositiveInt | None = None  # length + width + height


class QuoteLine(Model):
    """One charge as the provider states it. An estimate is a range with its assumption as the note."""
    leg: Leg
    component: Component
    state: Literal['additional', 'included', 'not_applicable', 'unknown', 'estimated']
    amount: Money | None = None
    lower: Money | None = None
    upper: Money | None = None
    note: Text | None = None

    @model_validator(mode='after')
    def coherent_line(self):
        if (self.leg, self.component) not in _NAMES:
            raise ValueError('This charge component does not belong to that leg')
        if (self.amount is not None) != (self.state == 'additional'):
            raise ValueError('Only an additional charge carries an amount')
        estimated = self.state == 'estimated'
        if estimated != (self.lower is not None) or estimated != (self.upper is not None):
            raise ValueError('Only an estimate carries a range, and it needs both ends')
        if estimated and (self.lower.currency != self.upper.currency or self.lower.amount > self.upper.amount):
            raise ValueError('An estimate needs one currency and ordered ends')
        if self.state in ('unknown', 'not_applicable', 'estimated') and self.note is None:
            raise ValueError('Unknown, inapplicable and estimated charges need a note')
        return self


class RouteQuote(Model):
    """One provider's statement for one route, backed by one route-scoped evidence record.

    seller_id and source_variant_id name the listing the quote was issued for; retailer and order
    charges need them. package states what was priced, battery_accepted whether the service takes the
    declared battery, and declared_value what customs charges were computed on. Only the retailer's own
    receipt for the listing may leave those out.
    """
    quote_id: Text
    provider_id: Text
    service: Text | None = None  # the provider's service, e.g. one carrier option; None is one service
    route_id: Text
    seller_id: Text | None = None
    source_variant_id: Text | None = None
    package: QuotedPackage | None = None
    battery_accepted: StrictBool | None = None
    limits: ServiceLimits = Field(default_factory=ServiceLimits)
    declared_value: Money | None = None
    lines: Annotated[tuple[QuoteLine, ...], Field(min_length=1)]
    valid_until: UtcTime | None = None
    evidence: v2.Evidence

    @model_validator(mode='after')
    def bound_quote(self):
        if (self.seller_id is None) != (self.source_variant_id is None):
            raise ValueError('A quote names both the seller and the listing variant, or neither')
        slots = [(line.leg, line.component) for line in self.lines]
        if len(set(slots)) != len(slots):
            raise ValueError('A quote states each charge once')
        if self.evidence.scope != 'route' or self.evidence.route_id != self.route_id:
            raise ValueError('A route quote needs evidence for its route')
        if self.valid_until is not None and self.valid_until < self.evidence.observed_at:
            raise ValueError('A quote cannot expire before it was observed')
        return self

    @property
    def names_listing(self) -> bool:
        return self.seller_id is not None


class RouteInputs(Model):
    """What route producers (#61 direct offers, #62 forwarding quotes) supply beside the offer.

    evidence holds the item-fact receipts and the FX receipts; each quote carries its own record.
    """
    item: ItemFacts = Field(default_factory=_not_supplied)
    quotes: tuple[RouteQuote, ...] = ()
    evidence: tuple[v2.Evidence, ...] = ()

    @model_validator(mode='after')
    def unique_quotes(self):
        if len({quote.quote_id for quote in self.quotes}) != len(self.quotes):
            raise ValueError('Quote IDs must be unique')
        return self


class LandedComponent(Model):
    """One settled, estimated or missing charge slot. Amounts are in the total's currency except
    quoted, which keeps the provider's own amount."""
    leg: Leg
    component: Component
    label: Text
    status: Literal['verified', 'estimated', 'missing']
    quoted: Money | None = None
    added: Money | None = None
    lower: Money | None = None
    upper: Money | None = None
    sources: tuple[Text, ...] = ()  # quote IDs, or 'listing' for the offer's own charges
    evidence_ids: tuple[Text, ...] = ()  # the quotes' or listing's receipts and any FX receipt used
    reasons: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def coherent_status(self):
        if (self.added is not None) != (self.status == 'verified'):
            raise ValueError('Only a verified charge adds an amount')
        if (self.lower is not None and self.upper is not None) != (self.status == 'estimated'):
            raise ValueError('Only an estimated charge carries a range')
        if self.status == 'missing' and not self.reasons:
            raise ValueError('A missing charge needs a reason')
        return self


class LandedCost(Model):
    offer_id: Text
    route: Literal['direct', 'forwarded']
    support: Support
    basis: Text
    cost: v2.Cost
    components: tuple[LandedComponent, ...] = ()
    reasons: tuple[Text, ...] = ()  # why the route is unsupported or not yet supported

    @model_validator(mode='after')
    def truthful_total(self):
        if self.cost.status == 'verified' and any(c.status != 'verified' for c in self.components):
            raise ValueError('A verified landed cost needs every charge verified')
        if self.support == 'supported' and self.cost.status != 'verified':
            raise ValueError('A supported route needs a verified landed cost')
        if self.support == 'unsupported' and not self.reasons:
            raise ValueError('An unsupported route needs a reason')
        return self


@dataclass(frozen=True)
class _Line:
    slot: tuple[str, str]
    state: str
    amount: Money | None
    lower: Money | None
    upper: Money | None
    note: str | None
    source: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Item:
    battery: str | None
    weight_g: int | None
    dimensions: Dimensions | None


def requires_landed_cost(offer: v2.Offer) -> bool:
    """True for an overseas seller or origin: the exact complement of the domestic cost engine."""
    return not supports_domestic_cost(offer)


def calculate_landed_cost(request: v2.ShoppingRequest, offer: v2.Offer, inputs: RouteInputs | None = None, *,
                          fx: Sequence[FxRate] = (), now: datetime | None = None,
                          run_started_at: datetime | None = None) -> LandedCost:
    """Settle every charge slot of an overseas offer's route (see the module docstring)."""
    if not requires_landed_cost(offer):
        raise ValueError('Landed costs apply to an overseas seller or origin')
    now = _aware(now)
    started = None if run_started_at is None else _aware(run_started_at)
    inputs = RouteInputs() if inputs is None else inputs
    route = offer.route
    forwarded = isinstance(route, v2.ForwardedRoute)
    for quote in inputs.quotes:
        if quote.route_id != route.route_id:
            raise ValueError('Every quote must be issued for the offer route')
        if not forwarded and any(line.leg == 'forwarding' for line in quote.lines):
            raise ValueError('Forwarding charges require a forwarded route')
    pool = _pool(offer, inputs)
    item = _read_item(inputs.item, offer, pool, now)
    rates = _sourced_rates([FxRate.model_validate(rate) for rate in fx], pool)
    # The total is in the price currency. A price that is zero or observed after now is not a total's
    # base, but its currency still places the other charges.
    currency = offer.price.value.currency if offer.price.state == 'known' else None
    price = offer.price.value if currency is not None and offer.price.value.amount > 0 else None
    if price is not None and any(record.observed_at > now for record in _records(offer.price.evidence_ids, pool)):
        price = None

    checked = [(quote, *_check_quote(quote, offer, item, price, now, started)) for quote in inputs.quotes]
    # One service (provider and service name) that both refuses and carries the item on one leg
    # contradicts itself: neither statement settles that carriage or rules the route out.
    def carried(quote, lines=None):
        return {(quote.provider_id, quote.service, line.leg) for line in (quote.lines if lines is None else lines)
                if line.leg in _CARRIES}
    refusing = set().union(*(carried(q) for q, refused, _, _ in checked if refused))
    # Only an acceptance that could itself settle a charge contradicts a refusal: an undated quote not
    # read in this run can supply estimates only.
    carrying = set().union(*(carried(q, [line for line in q.lines if undated is None or line.state == 'estimated'])
                             for q, refused, problems, undated in checked if not refused and not problems))
    found, doubts, unpriced = defaultdict(list), defaultdict(list), defaultdict(list)
    refusals, refused_legs = [], set()
    for quote, refused, problems, undated in checked:
        legs = {line.leg for line in quote.lines}
        if carried(quote) & refusing & carrying:
            problems = problems + refused + [f'quotes for one service of {quote.provider_id} disagree on '
                                             'carrying the item']
            refused = []
        if refused:
            refusals.append((legs, refused))
            refused_legs |= legs & set(_CARRIES)
        for line in quote.lines:
            slot = (line.leg, line.component)
            if refused or problems:
                doubts[slot] += refused + problems
            elif undated and line.state != 'estimated':
                doubts[slot].append(undated)
            elif line.state == 'unknown':
                # A current provider says the charge applies without pricing it: only an amount
                # can settle it, never another provider's included or not applicable.
                doubts[slot].append(f'quote {quote.quote_id} does not state this charge')
                unpriced[slot].append(quote.quote_id)
            else:
                found[slot].append(_Line(slot, line.state, line.amount, line.lower, line.upper, line.note,
                                         quote.quote_id, (quote.evidence.evidence_id,)))
    listed, blocked = defaultdict(list), {}
    for slot, line, problem in _listing_lines(offer, pool, now, started, forwarded):
        if problem:
            doubts[slot].append(problem)
        elif slot in {_CARRIES[leg] for leg in refused_legs}:
            # A listing says nothing about the battery or size a provider refused.
            doubts[slot].append('the listing does not show that the refused item is carried')
        else:
            listed[slot].append(line)
    for slot, lines in listed.items():
        if len(lines) > 1:  # separate charges on the offer; one quote cannot settle both
            blocked[slot] = f'several listed charges fall on the {_label(slot, forwarded)}'
        else:
            found[slot] += lines

    required = set(_ALWAYS) | ({('forwarding', 'shipping')} if forwarded else set())
    if forwarded and item.battery != 'none':
        required.add(('forwarding', 'dangerous_goods'))
    components = []
    for slot in _SLOTS:
        if slot in required or found[slot] or doubts[slot] or slot in blocked:
            label = _label(slot, forwarded)
            components.append(
                LandedComponent(leg=slot[0], component=slot[1], label=label, status='missing',
                                reasons=(blocked[slot],)) if slot in blocked else
                _settle(slot, label, found[slot], doubts[slot], unpriced[slot], currency, rates, now))

    exclusions = []
    for legs, refused in refusals:
        # Another current quote that covers the item may still carry the leg, priced or not.
        if any(not ({line.source for line in found[_CARRIES[leg]]} - {'listing'}) and not unpriced[_CARRIES[leg]]
               for leg in legs & set(_CARRIES)):
            exclusions += refused
    eligible =((route.retailer_leg.item_eligible, route.forwarding_leg.item_eligible) if forwarded
                else (route.item_eligible,))
    if any(fact.state == 'known' and fact.value is False for fact in eligible):
        exclusions.append('route evidence says the item cannot take this route')
    reasons = list(dict.fromkeys(exclusions))
    if not all(fact.state == 'known' and fact.value is True for fact in eligible) and not exclusions:
        reasons.append('route eligibility for this item is not established')

    cost = _total(request, price, components, rates, now)
    support = ('unsupported' if exclusions else
               'supported' if cost.status == 'verified' and not reasons else 'unresolved')
    destination = route.destination.city or 'Lebanon'
    way = f'forwarded through {route.forwarder_id}' if forwarded else f'direct from {route.origin_country}'
    basis = (f'{way} to {destination}; total in {currency or "the price currency"}; excludes any fee '
             "the buyer's own card issuer adds for paying in a foreign currency")
    return LandedCost(offer_id=offer.offer_id, route='forwarded' if forwarded else 'direct', support=support,
                      basis=basis, cost=cost, components=tuple(components), reasons=tuple(reasons))


def _pool(offer: v2.Offer, inputs: RouteInputs) -> dict[str, v2.Evidence]:
    pool = {}
    for record in (*offer.evidence, *inputs.evidence, *(quote.evidence for quote in inputs.quotes)):
        if pool.setdefault(record.evidence_id, record) != record:
            raise ValueError('Evidence IDs must be unique')
    return pool


def _records(evidence_ids, pool) -> list[v2.Evidence]:
    return [pool[evidence_id] for evidence_id in evidence_ids if evidence_id in pool]


def _read_item(facts: ItemFacts, offer: v2.Offer, pool, now: datetime) -> _Item:
    def known(fact, path):
        if fact.state != 'known':
            return None
        for evidence_id in fact.evidence_ids:
            record = pool.get(evidence_id)
            if (record is None or record.field_path != path or record.observed_at > now
                    or (record.source_id == offer.source_id and (
                        record.product_id not in (None, offer.source_product_id)
                        or record.variant_id not in (None, offer.source_variant_id)))):
                return None
        return fact.value
    return _Item(known(facts.battery, 'package.battery'), known(facts.weight_g, 'package.weight_g'),
                 known(facts.dimensions, 'package.dimensions'))


def _sourced_rates(rates: list[FxRate], pool) -> list[FxRate]:
    """Rates whose receipt is supplied for that currency pair at that time and backs no other value."""
    cited = defaultdict(set)
    for rate in rates:
        cited[rate.evidence_id].add((rate.base, rate.quote, rate.rate, rate.observed_at))
    usable = []
    for rate in rates:
        record = pool.get(rate.evidence_id)
        if (record is not None and record.field_path == f'fx.{rate.base}.{rate.quote}'
                and record.observed_at == rate.observed_at and len(cited[rate.evidence_id]) == 1):
            usable.append(rate)
    return usable


def _bound_to_listing(record: v2.Evidence, offer: v2.Offer) -> bool:
    return record.source_id != offer.source_id or (record.product_id in (None, offer.source_product_id)
                                                   and record.variant_id in (None, offer.source_variant_id))


def _check_quote(quote: RouteQuote, offer: v2.Offer, item: _Item, price: Money | None, now: datetime,
                 started: datetime | None) -> tuple[list[str], list[str], str | None]:
    """(refusals, problems, validity doubt): a refusal or problem bars every line of the quote; the
    validity doubt bars only its non-estimated lines."""
    name = f'quote {quote.quote_id}'
    legs = {line.leg for line in quote.lines}
    carries = legs & set(_CARRIES)
    record = quote.evidence
    refusals, problems = [], []
    if record.observed_at > now:
        problems.append(f'{name} was observed after this check')
    if quote.valid_until is not None and quote.valid_until <= now:
        problems.append(f'{name} expired at {quote.valid_until.isoformat()}')
    undated = None
    if quote.valid_until is None and (started is None or record.observed_at < started):
        undated = f'{name} states no validity period and was not read in this run'
    if not _bound_to_listing(record, offer):
        problems.append(f'the receipt of {name} belongs to another listing')
    if quote.names_listing:
        if (quote.seller_id, quote.source_variant_id) != (offer.seller.seller_id, offer.source_variant_id):
            problems.append(f'{name} was issued for another seller or listing')
    elif legs & _RETAILER_LEGS:
        problems.append(f'{name} does not name the actual seller and listing')
    # Only the retailer's own receipt for this listing speaks for the item without a package.
    receipt = (quote.names_listing and quote.provider_id == offer.retailer_id
               and record.source_id == offer.source_id and record.product_id == offer.source_product_id
               and record.variant_id == offer.source_variant_id)
    if quote.provider_id == offer.retailer_id and quote.names_listing and not receipt:
        problems.append(f"{name} is not backed by the retailer's receipt for this listing")
    if 'forwarding' in legs and quote.provider_id != offer.route.forwarder_id:
        problems.append(f'{name} is not from the route forwarder')
    if (isinstance(offer.route, v2.ForwardedRoute) and legs - _RETAILER_LEGS
            and quote.provider_id in (offer.retailer_id, offer.seller.seller_id)):
        problems.append(f'{name} is from the retailer, which ships only to the forwarding address')
    # Only a current quote for this seller and route can rule the route out; any other refusal is a doubt.
    refuse = refusals.append if not problems and undated is None else problems.append

    package = quote.package
    if package is None and not receipt and carries:
        problems.append(f'{name} does not state the package it priced')
    if package is not None:
        if item.weight_g is None:
            problems.append('item weight is not established')
        elif item.weight_g > package.weight_g:
            problems.append(f'{name} priced a lighter package than the item')
        if package.dimensions is not None:
            if item.dimensions is None:
                problems.append('item dimensions are not established')
            elif not item.dimensions.fits_in(package.dimensions):
                problems.append(f'{name} priced a smaller package than the item')
        if item.battery is None:
            problems.append('item battery configuration is not established')
        elif package.battery != item.battery:
            problems.append(f'{name} declared a different battery configuration')

    if carries:
        # The retailer's receipt without a package speaks for the listed item itself.
        declared = package.battery if package is not None else item.battery if receipt else None
        if item.battery not in (None, 'none') and declared == item.battery:
            if quote.battery_accepted is False:
                refuse(f'{name} service does not accept the item battery')
            elif quote.battery_accepted is None and not receipt:
                problems.append(f'{name} does not confirm that the item battery is accepted')
        limits = quote.limits
        if limits.max_weight_g is not None:
            if item.weight_g is None:
                problems.append('item weight is not established')
            elif item.weight_g > limits.max_weight_g:
                refuse(f'item weight is over the limit of {name}')
        if limits.max_side_mm is not None or limits.max_dimension_sum_mm is not None:
            if item.dimensions is None:
                problems.append('item dimensions are not established')
            else:
                sides = item.dimensions.sides()
                if ((limits.max_side_mm is not None and sides[-1] > limits.max_side_mm)
                        or (limits.max_dimension_sum_mm is not None and sum(sides) > limits.max_dimension_sum_mm)):
                    refuse(f'item size is over the limit of {name}')

    if 'import' in legs and not receipt:
        value = quote.declared_value
        if value is None:
            problems.append(f'{name} does not state the declared value')
        elif price is None:
            problems.append(f'the declared value of {name} cannot be compared with the device price')
        elif value.currency != price.currency:
            problems.append(f'{name} declares a value in another currency than the price')
        elif value.amount < price.amount:
            problems.append(f'{name} declares less than the device price')
    return list(dict.fromkeys(refusals)), list(dict.fromkeys(problems)), undated


def _listing_slot(charge: v2.Charge) -> tuple[str, str]:
    """Where a charge listed on the offer lands. A kind whose meaning on that leg is open is that
    leg's 'other' charge, so it is still counted and never stands in for a required slot."""
    if charge.kind == 'shipping':
        return ('forwarding' if charge.leg == 'forwarding' else 'retailer', 'shipping')
    if charge.kind == 'duty':
        return ('import', 'duty')
    if charge.kind == 'customs_handling':
        return ('import', 'clearance')
    if charge.kind == 'payment':
        return ('order', 'payment_fee')
    if charge.kind == 'tax' and charge.leg == 'order':
        return ('order', 'sales_tax')
    return (charge.leg, 'other')


def _listing_lines(offer: v2.Offer, pool, now: datetime, started: datetime | None, forwarded: bool):
    """(slot, line, problem) for each charge listed on the offer, read as the retailer's statement."""
    for charge in offer.costs:
        slot = _listing_slot(charge)
        name = f'listed {_label(slot, forwarded)}'
        records = _records(charge.evidence_ids, pool)
        problem = None
        if charge.state == 'unknown':
            problem = f'{name} is not published'
        elif forwarded and slot[0] not in _RETAILER_LEGS:
            problem = f'{name} comes from the retailer, which ships only to the forwarding address'
        elif charge.conditions:
            problem = f'{name} depends on a condition'
        elif charge.valid_until is not None and charge.valid_until <= now:
            problem = f'{name} expired at {charge.valid_until.isoformat()}'
        elif any(record.observed_at > now for record in records):
            problem = f'{name} was observed after this check'
        elif charge.valid_until is None and (started is None or any(r.observed_at < started for r in records)):
            problem = f'{name} states no validity period and was not read in this run'
        line = _Line(slot, charge.state, charge.amount, None, None, None, 'listing', tuple(charge.evidence_ids))
        yield slot, line, problem


def _convert(money: Money, currency: str, rates, now) -> tuple[Decimal, tuple[str, ...]] | None:
    """The amount in currency and the FX receipt used, or None without a fresh sourced rate."""
    if money.currency == currency:
        return money.amount, ()
    rate = _rate(rates, money.currency, currency, now)
    if rate is None:
        return None
    value = _context().multiply(money.amount, rate.rate)
    return (value, (rate.evidence_id,)) if value.is_finite() else None


def _settle(slot, label: str, lines: list[_Line], doubts: list[str], unpriced: list[str], currency: str | None,
            rates, now: datetime) -> LandedComponent:
    leg, component = slot
    base = dict(leg=leg, component=component, label=label)

    def missing(*reasons):
        return LandedComponent(status='missing', reasons=tuple(dict.fromkeys(reasons)), **base)

    # A verified line outranks an estimate; doubts about other lines no longer matter once one settles.
    verified = [line for line in lines if line.state != 'estimated']
    chosen = verified or [line for line in lines if line.state == 'estimated']
    if not chosen:
        return missing(*(doubts or [f'no current quote states the {label}']))
    if currency is None:
        return missing(_NO_PRICE)
    sources = tuple(dict.fromkeys(line.source for line in chosen))
    evidence = tuple(dict.fromkeys(e for line in chosen for e in line.evidence_ids))
    if verified:
        # Included, not applicable and an additional zero all add nothing, so they agree.
        values = {(line.amount.amount, line.amount.currency) if line.state == 'additional' and line.amount.amount
                  else (Decimal(0), None) for line in verified}
        if component == 'other' and len(verified) > 1 and values != {(Decimal(0), None)}:
            # Equal 'other' amounts may be one fee restated or several fees; they are never merged.
            return missing('several other charges are listed separately')
        if len(values) > 1:
            return missing(f'providers disagree on the {label}')
        amount, quoted_currency = next(iter(values))
        if quoted_currency is None and unpriced:
            return missing(f'quote {unpriced[0]} states the {label} without an amount')
        quoted = None if quoted_currency is None else Money(amount=amount, currency=quoted_currency)
        converted = (amount, ()) if quoted is None else _convert(quoted, currency, rates, now)
        if converted is None:
            return missing(f'no current sourced exchange rate for the {label}')
        return LandedComponent(status='verified', quoted=quoted, added=Money(amount=converted[0], currency=currency),
                               sources=sources, evidence_ids=evidence + converted[1], **base)
    ends = [(_convert(line.lower, currency, rates, now), _convert(line.upper, currency, rates, now))
            for line in chosen]
    if any(lower is None or upper is None for lower, upper in ends):
        return missing(f'no current sourced exchange rate for the {label}')
    fx_ids = tuple(dict.fromkeys(e for pair in ends for end in pair for e in end[1]))
    return LandedComponent(status='estimated', lower=Money(amount=min(lower[0] for lower, _ in ends), currency=currency),
                           upper=Money(amount=max(upper[0] for _, upper in ends), currency=currency),
                           sources=sources, evidence_ids=evidence + fx_ids,
                           reasons=tuple(f'{label}: {line.note}' for line in chosen), **base)


def _total(request: v2.ShoppingRequest, price: Money | None, components, rates, now: datetime) -> v2.Cost:
    missing = [c.label for c in components if c.status == 'missing' and c.reasons != (_NO_PRICE,)]
    if price is None:
        return v2.IncompleteCost(status='incomplete', missing=tuple(dict.fromkeys(
            ['positive, unambiguous price', *missing])))
    currency = price.currency
    subtotal = _sum([price.amount, *(c.added.amount for c in components if c.status == 'verified')])
    if subtotal is None or not subtotal.is_finite():
        return v2.IncompleteCost(status='incomplete', missing=('exact payable arithmetic',))
    known = Money(amount=subtotal, currency=currency)
    if missing:
        return v2.IncompleteCost(status='incomplete', known_subtotal=known, missing=tuple(dict.fromkeys(missing)))
    estimated = [c for c in components if c.status == 'estimated']
    if estimated:
        lower = _sum([subtotal, *(c.lower.amount for c in estimated)])
        upper = _sum([subtotal, *(c.upper.amount for c in estimated)])
        if lower is None or upper is None or not (lower.is_finite() and upper.is_finite()):
            return v2.IncompleteCost(status='incomplete', known_subtotal=known, missing=('exact payable arithmetic',))
        return v2.EstimatedCost(status='estimated', lower=Money(amount=lower, currency=currency),
                                upper=Money(amount=upper, currency=currency),
                                assumptions=tuple(note for c in estimated for note in c.reasons))
    budget = request.budget
    limit = (budget.maximum or budget.target) if budget else None
    conversion = None
    if limit is not None and limit.currency != currency:
        # A hard maximum needs the conversion; a target is a preference, so without a rate the
        # verified total simply stays in its own currency.
        rate = _rate(rates, currency, limit.currency, now)
        converted = None if rate is None else _context().multiply(subtotal, rate.rate)
        if converted is not None and converted.is_finite():
            conversion = v2.FxConversion(base=known, quote=Money(amount=converted, currency=limit.currency),
                                         rate=rate.rate, evidence_id=rate.evidence_id,
                                         observed_at=rate.observed_at, valid_until=rate.observed_at + FX_MAX_AGE)
        elif budget.maximum is not None:
            missing = 'current budget FX quote' if rate is None else 'finite budget conversion'
            return v2.IncompleteCost(status='incomplete', known_subtotal=known, missing=(missing,))
    return v2.VerifiedCost(status='verified', total=known, budget_conversion=conversion)
