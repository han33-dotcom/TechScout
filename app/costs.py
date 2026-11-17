"""Cost engine (MVP 09): an offer's exact payable total, or the reasons it cannot be verified.

Deterministic rules only. The device line is the offer's known price, which lapses with a device
quote that repeats it and applies or might apply, unless the price was observed after that quote
ended (a price fact has no expiry of its own). A lapsed price is never replaced by a term for
another amount, which leaves the device line open. VAT always applies and delivery applies to a
delivery request; neither can be settled as not_applicable, which needs no evidence. Payment
surcharges and other listed charges count when they apply. Travel is never added: a pickup total
is the store payable amount, travel excluded. Buyer-supplied travel is not supported, because
ShoppingRequest v1 has no field for it, so a store-listed travel charge is never added either.
Unknown charges stay unknown, never zero; one listed anywhere on the offer keeps the total open,
since a Recommendation cannot carry it. Only a fully known total gets a budget check. Reasons
and gaps use fixed wording plus contract values, never store text.

A kind's charge comes from its components that apply now: every condition holds and the quote
has not expired. Components that agree count once; disagreeing ones are a conflict. The
exception is 'other': the contract has no fee identity, so several nonzero 'other' components
may be separate fees and are never merged; a reader lists distinct fees as one component
carrying their sum and every fee's evidence, and separately listed ones keep the total open.
Components whose conditions fail are set aside; if nothing else applies, VAT or delivery has no
published charge for this order, while an optional surcharge simply does not apply. A set-aside
waiver (a term that adds nothing: included, not applicable or an additional zero) is the
exception: outside its condition the ordinary charge applies but is not published, so the charge
stays unresolved, unless the waiver's conditions include all of a set-aside surcharge's, where
it can only waive that published surcharge. A condition that cannot be checked leaves the charge
unresolved, and so does an expired quote unless a current one exists.

Condition grammar (every condition on a component must hold):
  min_subtotal:<CUR>:<amount>  the device price is in CUR and at least amount (min_subtotal:USD:50)
  city:<name>                  the buyer's city, compared casefold: the delivery city, or the pickup
                               city for a pickup
  payment:<cash|card>          the buyer pays that way (the request's payment basis)
Any other condition, or one that cannot be evaluated (a threshold in another currency, or no known
price), leaves the charge unresolved. Imports and international charges are not supported: the
contract has no kind for them, so they can only arrive as foreign-currency amounts or conditions
outside this grammar, and both leave the total unresolved.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import MAX_EMAX, MIN_EMIN, ROUND_CEILING, Context, Decimal, Inexact, InvalidOperation
import re
from typing import Annotated

from pydantic import Field, model_validator

from app.assessment import CostResult, EvidenceGap
from app.contracts import (
    Amount, CostComponent, Currency, Fulfillment, Model, Money, Offer, ShoppingRequest, Text, UtcTime,
)

FX_MAX_AGE = timedelta(hours=24)
# Significant digits kept in sums: far beyond any real amount, so real totals are always exact.
DIGITS = 200
# Kinds that count toward the total, in reason order. Travel never does: the basis excludes it.
_LABELS = {'device': 'device price', 'tax': 'VAT treatment', 'delivery': 'delivery charge',
           'payment': 'payment surcharge', 'other': 'other charge'}
_OPTIONAL = ('payment', 'other')  # absent means none is charged; VAT and delivery must be stated
_THRESHOLD = re.compile(r'([A-Z]{3}):([0-9]+(?:\.[0-9]+)?)')


class FxRate(Model):
    """A timestamped exchange rate: one unit of base buys rate units of quote."""
    base: Currency
    quote: Currency
    rate: Annotated[Amount, Field(gt=0)]
    observed_at: UtcTime
    evidence_id: Text

    @model_validator(mode='after')
    def two_currencies(self):
        if self.base == self.quote:
            raise ValueError('An exchange rate converts between two different currencies')
        return self


@dataclass(frozen=True)
class _Line:
    """One kind's outcome: the amount it adds and the component that shows it, or why it is open."""
    kind: str
    amount: Decimal | None = None
    component: CostComponent | None = None
    reason: str | None = None
    gap: str | None = None  # the offer field that more evidence might settle


def calculate_cost(request: ShoppingRequest, offer: Offer, *, fx: Sequence[FxRate] = (),
                   now: datetime | None = None) -> CostResult:
    """Total one offer for the buyer's route: verified with a budget check, unresolved, or excluded.

    verified_total is the amount payable to the store, in the price currency. A budget in another
    currency is compared through the least favourable rate in fx observed in the 24 hours up to now.
    """
    now = _aware(now)
    rates = [FxRate.model_validate(r) for r in fx]
    route = request.fulfillment
    same_route = _same_route(route, offer.fulfillment)
    pickup_point = route.branch_id or (offer.fulfillment.branch_id if same_route else None) or route.city
    basis = (f'delivery to {route.city}' if route.mode == 'delivery'
             else f'pickup at {pickup_point}; travel excluded')
    excluded = _exclusions(offer)
    if excluded:
        return CostResult(offer_id=offer.offer_id, status='excluded', basis=basis, reasons=excluded)

    price = offer.price.value if offer.price.state == 'known' else None
    listed = {kind: [c for c in offer.costs if c.kind == kind] for kind in _LABELS}
    order = dict(request=request, price=price, now=now)
    if price is None:
        reason = 'price is unknown' if offer.price.state == 'unknown' else 'prices conflict'
        lines = [_Line('device', reason=reason, gap='price')]
    else:
        # Fact[Money] has no expiry: the price lapses with the earliest device quote that repeats
        # it and applies or might apply (only a failing condition rules it out), unless the price
        # was observed again after that quote ended.
        seen = max((e.observed_at for e in offer.evidence if e.evidence_id in offer.price.evidence_ids),
                   default=None)
        ends = [c.valid_until for c in listed['device']
                if c.state == 'additional' and c.amount == price and c.valid_until is not None
                and (seen is None or c.valid_until >= seen) and _applies(c, request, price) is not False]
        device = CostComponent(kind='device', state='additional', amount=price,
                               evidence_ids=list(offer.price.evidence_ids),
                               valid_until=min(ends, default=None))
        line = _line('device', [device, *listed['device']], required=True, **order)
        if line.reason is None and line.amount != price.amount:
            # Only possible once the price has lapsed: a current term for another amount would
            # conflict with a live price, and it cannot stand in for one that has lapsed.
            line = _Line('device', reason='device quote has expired', gap='costs.device')
        lines = [line]
    lines.append(_line('tax', listed['tax'], required=True, **order))
    if route.mode == 'delivery':  # for a pickup, delivery terms do not apply and are ignored
        lines.append(_line('delivery', listed['delivery'], required=True, **order))
    lines += [_line(kind, listed[kind], required=False, **order) for kind in _OPTIONAL]

    reasons = [] if same_route else [
        'offer was read for a different fulfillment route']
    reasons += [line.reason for line in lines if line.reason]
    # A Recommendation can never carry an unknown cost (contracts.Recommendation), so an unknown
    # charge listed anywhere keeps the total open, even one that would not apply to this order.
    open_kinds = {line.kind for line in lines if line.reason}
    reasons += [f'an unknown {c.kind} charge is listed' for c in offer.costs
                if c.state == 'unknown' and c.kind not in open_kinds]
    rate = None
    if price is not None and price.currency != request.budget.currency:
        rate = _rate(rates, price.currency, request.budget.currency, now)
        if rate is None:
            reasons.append('price currency differs from the budget; no timestamped exchange rate')
    gaps = [EvidenceGap(offer_id=offer.offer_id, source_id=offer.source_id, field_path=line.gap,
                        reason=line.reason) for line in lines if line.gap]
    # Without the device price nothing can be summed, so nothing is listed as used either.
    settled = [line for line in lines if line.reason is None] if lines[0].reason is None else []
    components = [line.component for line in settled if line.component is not None]
    total = _sum(line.amount for line in settled) if settled else None
    if settled and total is None:
        reasons.append('amounts cannot be totalled exactly')
    if reasons:
        subtotal = None if total is None else Money(amount=total, currency=price.currency)
        return CostResult(offer_id=offer.offer_id, status='unresolved', basis=basis, components=components,
                          known_subtotal=subtotal, reasons=list(dict.fromkeys(reasons)), gaps=gaps)
    compared = total
    if rate is not None:
        # Exact for any real amount and rate; an absurd product is rounded up, so no conversion can
        # bring an over-budget total under the budget.
        compared = _context().multiply(total, rate.rate)
        basis += (f'; compared with the budget at 1 {rate.base} = {rate.rate} {rate.quote}'
                  f' (rate evidence {rate.evidence_id})')
    return CostResult(offer_id=offer.offer_id, status='verified', basis=basis, components=components,
                      verified_total=Money(amount=total, currency=price.currency),
                      within_budget=compared <= request.budget.amount)


def _exclusions(offer: Offer) -> list[str]:
    """Reasons a known price cannot be a real price for this offer."""
    if offer.price.state != 'known':
        return []
    reasons = []
    amount = offer.price.value.amount
    if amount <= 0:
        reasons.append('price is zero')
    raw, digits = offer.raw_price, offer.price_fractional_digits
    if raw is not None and digits is not None and raw.isascii() and raw.isdigit():
        # Built from the string, so the check is exact at any length; Decimal.scaleb would round
        # to the context's 28 significant digits.
        try:
            matches = Decimal(f'{raw}E-{digits}') == amount
        except InvalidOperation:  # an exponent beyond Decimal's range describes no real price
            matches = False
        if not matches:
            reasons.append('raw price does not match the declared amount')
    return reasons


def _line(kind: str, components: list[CostComponent], *, required: bool, request: ShoppingRequest,
          price: Money | None, now: datetime) -> _Line:
    """Settle one kind from its components that apply now (see the module docstring)."""
    field, label = f'costs.{kind}', _LABELS[kind]
    current, uncheckable, expired, set_aside = [], [], [], []
    for component in components:
        applies = _applies(component, request, price)
        if applies is False:
            set_aside.append(component)
        elif component.valid_until is not None and component.valid_until < now:
            expired.append(component)  # stale whether or not it would apply
        elif applies is None:
            uncheckable.append(component)
        else:
            current.append(component)
    states = {c.state for c in current}
    if 'unknown' in states:
        return _Line(kind, reason=f'{label} is not published', gap=field)
    if required and 'not_applicable' in states:
        # not_applicable needs no evidence, so it cannot settle a charge this order must have;
        # a store that charges nothing states it as included or as an additional zero.
        return _Line(kind, reason=f'{kind} applies to this order but is marked not applicable', gap=field)
    if price is not None and any(c.state == 'additional' and c.amount.currency != price.currency
                                 for c in current):
        return _Line(kind, reason=f'{kind} charge is not in the price currency')
    if uncheckable:  # it might apply, and then the charge could differ
        return _Line(kind, reason=f'{kind} promotion condition cannot be checked')
    # Included and not-applicable terms add nothing, so an included tax is never added twice.
    added = [c.amount.amount if c.state == 'additional' else Decimal(0) for c in current]
    if len(set(added)) > 1:
        return _Line(kind, reason=f'conflicting {kind} terms', gap=field)
    if kind == 'other' and len(current) > 1 and added[0]:
        # Equal 'other' charges may be one fee restated or several fees; they are never merged.
        # Re-reading the store cannot settle how a reader listed them, so this is no evidence gap.
        return _Line(kind, reason='several other charges are listed separately')
    if current:
        return _Line(kind, amount=added[0], component=current[0])
    if expired:
        return _Line(kind, reason=f'{kind} quote has expired', gap=field)
    if not required:
        # A term that adds nothing under a condition waives an ordinary charge; set aside, it leaves
        # that charge unpublished, so it stays unknown rather than becoming zero. A waiver whose
        # conditions include all of a set-aside surcharge's can only waive that published surcharge.
        surcharges = [set(c.conditions) for c in set_aside if c.state == 'additional' and c.amount.amount > 0]
        waivers = [set(c.conditions) for c in set_aside if c.state in ('included', 'not_applicable')
                   or (c.state == 'additional' and c.amount.amount == 0)]
        if any(not any(surcharge <= waiver for surcharge in surcharges) for waiver in waivers):
            return _Line(kind, reason=f'published {kind} terms do not cover this order', gap=field)
        return _Line(kind, amount=Decimal(0))  # no published surcharge applies to this order
    if set_aside:  # we do not know the charge without the terms that were set aside
        return _Line(kind, reason=f'published {kind} terms do not cover this order', gap=field)
    return _Line(kind, reason=f'{label} is not published', gap=field)


def _applies(component: CostComponent, request: ShoppingRequest, price: Money | None) -> bool | None:
    """Every condition must hold: False if one fails, None if one cannot be evaluated."""
    results = {_holds(condition, request, price) for condition in component.conditions}
    return False if False in results else None if None in results else True


def _holds(condition: str, request: ShoppingRequest, price: Money | None) -> bool | None:
    """One condition of the grammar; None when it is outside the grammar or cannot be evaluated."""
    name, _, argument = condition.partition(':')
    if name == 'min_subtotal':
        threshold = _THRESHOLD.fullmatch(argument)
        if threshold is None or price is None or price.currency != threshold[1]:
            return None
        return price.amount >= Decimal(threshold[2])
    if name == 'city':
        city = _words(argument)
        return city == _words(request.fulfillment.city) if city else None
    if name == 'payment':
        return request.payment_basis == argument if argument in ('cash', 'card') else None
    return None


def _rate(rates: list[FxRate], base: str, quote: str, now: datetime) -> FxRate | None:
    fresh = [r for r in rates
             if (r.base, r.quote) == (base, quote) and now - FX_MAX_AGE <= r.observed_at <= now]
    # The least favourable fresh rate, so a choice between rates cannot admit an over-budget offer.
    return max(fresh, key=lambda r: r.rate, default=None)


def _context() -> Context:
    # Bounded precision with the widest exponents: an absurd exponent stays cheap (unbounded
    # precision would write out every digit), and anything inexact shows in the flags. Fresh per
    # call, so flags never leak between calls or threads.
    return Context(prec=DIGITS, rounding=ROUND_CEILING, Emax=MAX_EMAX, Emin=MIN_EMIN,
                   traps=[InvalidOperation])


def _sum(amounts) -> Decimal | None:
    """The exact sum, or None when it cannot be written in DIGITS significant digits."""
    context, total = _context(), Decimal(0)
    for amount in amounts:
        total = context.add(total, amount)
    return None if context.flags[Inexact] else total


def _same_route(wanted: Fulfillment, read: Fulfillment) -> bool:
    """The offer must cover the requested location and any explicit branch."""
    return (wanted.mode == read.mode and wanted.country == read.country
            and _words(wanted.city) == _words(read.city)
            and wanted.branch_id in (None, read.branch_id))


def _words(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    return now.astimezone(timezone.utc)


def calculate_domestic_cost(request, offer, *, fx: Sequence[FxRate] = (), now=None, estimates=None):
    """V2 cost union; explicit fee estimates never verify an offer."""
    from types import SimpleNamespace
    from app import contracts_v2 as v2
    now, estimates = _aware(now), estimates or {}
    if (offer.seller.market != 'LB' or isinstance(offer.route,v2.ForwardedRoute)
            or isinstance(offer.route,v2.DirectRoute) and offer.route.origin_country != 'LB'):
        return v2.IncompleteCost(status='incomplete',missing=('international cost evaluation required',))
    price = offer.price.value if offer.price.state == 'known' else None
    if price is None or price.amount <= 0:
        return v2.IncompleteCost(status='incomplete',missing=('positive, unambiguous price',))
    if any(e.observed_at > now for e in offer.evidence if e.evidence_id in offer.price.evidence_ids):
        return v2.IncompleteCost(status='incomplete',missing=('price observation is in the future',))
    amounts, missing = [price.amount], []
    currency = price.currency
    required = {'tax'} | ({'shipping'} if isinstance(offer.route,v2.DirectRoute) else set())
    listed = {c.kind for c in offer.costs}
    missing.extend('costs.order.'+k for k in sorted(required-listed))
    payment = request.payment or (offer.payment.value if offer.payment and offer.payment.state=='known' else None)
    city = request.fulfillment.destination.city
    order = SimpleNamespace(payment_basis=payment,fulfillment=SimpleNamespace(city=city or ''))
    for charge in offer.costs:
        path = f'costs.{charge.leg}.{charge.kind}'
        if charge.state=='unknown' or charge.valid_until is not None and charge.valid_until < now:
            missing.append(path)
            continue
        receipts = [e for e in offer.evidence if e.evidence_id in charge.evidence_ids]
        if any(e.observed_at > now for e in receipts):
            missing.append(path)
            continue
        applicable = set()
        for condition in charge.conditions:
            name = condition.partition(':')[0]
            applicable.add(None if (name=='payment' and payment is None or name=='city' and city is None)
                           else _holds(condition,order,price))
        if None in applicable:
            missing.append(path)
            continue
        if False in applicable:
            # A failed waiver doesn't establish the normal charge. Conditional
            # required fees don't establish the fee outside their condition.
            if charge.kind in required or charge.state != 'additional' or charge.amount.amount == 0:
                missing.append(path)
            continue
        if charge.state=='additional':
            if charge.amount.currency != currency:
                missing.append(path)
            else:
                amounts.append(charge.amount.amount)
    subtotal = _sum(amounts)
    if subtotal is None or not subtotal.is_finite():
        return v2.IncompleteCost(status='incomplete',missing=('exact payable arithmetic',))
    money = Money(amount=subtotal,currency=currency)
    if missing:
        # No invented fee or range: every missing component needs an explicit
        # same-currency estimate with assumptions before a range can be shown.
        ranges = [estimates.get(path) for path in missing]
        if all(isinstance(r,v2.EstimatedCost) and r.lower.currency==currency for r in ranges):
            lower = _sum([subtotal,*(r.lower.amount for r in ranges)])
            upper = _sum([subtotal,*(r.upper.amount for r in ranges)])
            if lower is not None and upper is not None and lower.is_finite() and upper.is_finite():
                return v2.EstimatedCost(status='estimated',lower=Money(amount=lower,currency=currency),
                    upper=Money(amount=upper,currency=currency),
                    assumptions=tuple(a for r in ranges for a in r.assumptions))
        return v2.IncompleteCost(status='incomplete',known_subtotal=money,missing=tuple(dict.fromkeys(missing)))
    conversion = None
    maximum = request.budget.maximum if request.budget else None
    if maximum and maximum.currency != currency:
        rate = _rate([FxRate.model_validate(r) for r in fx],currency,maximum.currency,now)
        if rate is None:
            return v2.IncompleteCost(status='incomplete',known_subtotal=money,missing=('current budget FX quote',))
        converted = _context().multiply(subtotal,rate.rate)
        if not converted.is_finite():
            return v2.IncompleteCost(status='incomplete',known_subtotal=money,missing=('finite budget conversion',))
        conversion = v2.FxConversion(base=money,quote=Money(amount=converted,currency=maximum.currency),
            rate=rate.rate,evidence_id=rate.evidence_id,observed_at=rate.observed_at,
            valid_until=rate.observed_at+FX_MAX_AGE)
    return v2.VerifiedCost(status='verified',total=money,budget_conversion=conversion)
