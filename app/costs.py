"""Cost engine (MVP 09): an offer's exact payable total, or the reasons it cannot be verified.

Deterministic rules only. The device line is the offer's known price. VAT always applies and
delivery applies to a delivery request; neither can be settled as not_applicable, which needs no
evidence. Payment surcharges and other listed charges count when they apply. Travel is never
added: a pickup total is the store payable amount, travel excluded. Unknown charges stay unknown,
never zero; one listed anywhere on the offer keeps the total open, since a Recommendation cannot
carry it. Only a fully known total gets a budget check. Reasons and gaps use fixed wording plus
contract values, never store text.

A kind's charge comes from its components that apply now: every condition holds and the quote
has not expired. Components that agree count once; disagreeing ones are a conflict. Components
whose conditions fail are set aside; if nothing else applies, VAT or delivery has no published
charge for this order, while an optional surcharge simply does not apply. A set-aside waiver (a
term that adds nothing: included, not applicable or an additional zero) is the exception: outside
its condition the ordinary charge applies but is not published, so the charge stays unresolved.
A condition that cannot be checked leaves the charge unresolved, and so does an expired quote
unless a current one exists.

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
    basis = (f'delivery to {route.city}' if route.mode == 'delivery'
             else f'pickup at {route.branch_id or route.city}; travel excluded')
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
        device = CostComponent(kind='device', state='additional', amount=price,
                               evidence_ids=list(offer.price.evidence_ids))
        lines = [_line('device', [device, *listed['device']], required=True, **order)]
    lines.append(_line('tax', listed['tax'], required=True, **order))
    if route.mode == 'delivery':  # for a pickup, delivery terms do not apply and are ignored
        lines.append(_line('delivery', listed['delivery'], required=True, **order))
    lines += [_line(kind, listed[kind], required=False, **order) for kind in _OPTIONAL]

    reasons = [] if _same_route(route, offer.fulfillment) else [
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
    if current:
        return _Line(kind, amount=added[0], component=current[0])
    if expired:
        return _Line(kind, reason=f'{kind} quote has expired', gap=field)
    if not required:
        # A term that adds nothing under a condition waives an ordinary charge; set aside, it leaves
        # that charge unpublished, so it stays unknown rather than becoming zero.
        if any(c.state in ('included', 'not_applicable')
               or (c.state == 'additional' and c.amount.amount == 0) for c in set_aside):
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
    """The offer's costs were read for the buyer's route: same mode, city and pickup branch."""
    return ((wanted.mode, _words(wanted.city), wanted.branch_id)
            == (read.mode, _words(read.city), read.branch_id))


def _words(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    return now.astimezone(timezone.utc)
