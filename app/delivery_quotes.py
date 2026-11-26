"""Retailer pre-order delivery quotes for buyer chats (#186).

A finished search can leave an offer's delivery fee open: the seller publishes no rate that
covers the buyer's destination, so its payable total is unknown. Where a reviewed quote route
exists (app.quote_capabilities), TechScout asks the seller's own pre-order step for the
delivery fee:

- one item, variant and quantity;
- one destination;
- no order, payment, reservation, account or message.

Two kinds of destination:

- city: a route whose rate depends at most on the city is quoted for the search's own city
  as soon as the search finishes. The buyer is not asked for anything.
- street: a route that needs a street address waits for the buyer. The chat names those sellers
  once (waiting_sellers). The buyer picks a saved location and the sellers that may receive it
  (app.buyer_accounts). Only those sellers' quotes are requested, and each one receives the
  address through BuyerAccounts.release_address, never from storage here.

The quote worker (QuoteService, run inside app.run_worker --quotes) plans each finished chat,
sends pending quotes through SourceGate.quote under the shared ledger, and records the result.
The chat then shows the quotes and a revised comparison without a new search.

Rules kept throughout:

- Unknown stays unknown: no usable quote, a conflicting quote and a declined address all leave
  the affected total unknown, and every other offer and product link stays.
- A request (seller, variant, quantity, destination) reaches a seller at most once per chat, and
  a failure after sending is never retried. A request that was never sent (no route, another city,
  the allowance, a busy workload limit) is reconsidered when the chat changes, and a busy limit
  is tried again a few times before it is given up.
- A destination change makes earlier quotes stop applying.
- A quote never sets an arrival time, so it can never satisfy a guaranteed deadline.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import logging
import re
import threading
import uuid
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator
from sqlalchemy import and_, delete, insert, or_, select, update

from app import contracts_v2 as v
from app.assessment import _doubts, assess_full_product
from app.contracts import HttpsUrl, Model, Money, PositiveInt, Text, UtcTime
from app.accounting import BudgetDenied
from app.costs import _sum, calculate_domestic_cost
from app.quote_capabilities import QuoteCapability, active_quote_routes, quote_url, storefront_body
from app.source_capabilities import CapabilityRef
from app.storage import buyer_destinations, buyer_locations, buyer_quote_plans, buyer_quotes, buyer_runs, buyer_sessions

log = logging.getLogger('techscout.quotes')

QuoteStatus = Literal['pending', 'requested', 'quoted', 'unavailable', 'conflicting']
SETTLED = ('quoted', 'unavailable', 'conflicting')
# A chat's destination summary (buyer_destinations.quote_state); see docs/delivery-quotes.md.
QuoteState = Literal['pending', 'requested', 'quoted', 'partial', 'unavailable', 'not_needed', 'none']
QUANTITY = 1  # ShoppingRequest has no quantity; every comparison is for one unit.
MAX_QUOTES_PER_SEARCH = 6  # retailer quote requests one finished search may start, in all
LEASE = timedelta(seconds=60)  # one claimed quote: far above one gated call and its waits
DEFER = timedelta(seconds=30)  # a workload refusal before sending waits this long, then retries
MAX_DEFERRALS = 4
DELIVERY_METHODS = ('SHIPPING', 'LOCAL')  # delivered options; pickup options are never a delivery fee
ADAPTER_VERSION = 'delivery-quote/1'
CORPUS_VERSION = 'delivery-quote-1'
Sha256 = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9a-f]{64}$')]

# Fixed buyer-facing reasons. Retailer text never appears in a reason.
NO_ROUTE = 'TechScout has no active, reviewed delivery quote route for this seller, so it was not asked.'
OTHER_CITY = 'The selected address is in a different city from this search, so it was not sent.'
OVER_LIMIT = 'This search already used its delivery quote allowance, so this seller was not asked.'
WITHDRAWN = 'The address is no longer selected for this seller, so it was not sent.'
GONE = 'The listing is no longer in this comparison, so it was not quoted.'
STALE = 'This quote no longer applies to the chat\'s latest search or destination, so it was not sent.'
INTERRUPTED = 'The quote was interrupted. TechScout does not repeat a quote automatically.'
NOT_SENT = 'TechScout could not prepare this quote within the reviewed route, so nothing was sent.'
ROUTE_OFF = 'The quote route for this seller is not active, so nothing was sent.'
WORKLOAD = 'TechScout\'s request allowance for this seller stayed busy, so the quote was not sent.'
FAILED = 'The seller gave no usable quote. TechScout does not repeat a quote automatically.'
REPRICED = 'The listing price changed after the delivery quote, so its total stays unknown.'
# A failure after the request was sent; it is never repeated.
_SENT = {
    'RATE_LIMIT': 'The seller asked TechScout to slow down. The quote is not repeated automatically.',
    'BLOCKED': 'The seller\'s reply was outside the reviewed quote route. The quote is not repeated automatically.',
}
# Refused by TechScout's own workload limits before sending: deferred, then given up.
_BUSY = ('RATE_LIMIT', 'BUDGET_EXHAUSTED')
_USER_ERRORS = {
    'MERCHANDISE_NOT_ENOUGH_STOCK': 'The seller reports this item is not available in that quantity.',
    'MERCHANDISE_OUT_OF_STOCK': 'The seller reports this item is out of stock.',
    'INVALID_DELIVERY_ADDRESS': 'The seller could not deliver to this destination.',
}
_AMOUNT = re.compile(r'[0-9]{1,12}(?:\.[0-9]{1,6})?')
_LABEL = re.compile(r'[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩<>]')


class QuoteDestination(Model):
    """Where a quote is for. A street-level quote names the saved location, never its street."""
    detail: Literal['city', 'street']
    city: Text
    location_id: Text | None = None
    label: Text | None = None

    @model_validator(mode='after')
    def location_only_for_street(self):
        if (self.detail == 'street') != (self.location_id is not None):
            raise ValueError('Only a street-level quote names a saved location')
        return self

    @property
    def key(self) -> str:
        return f'location:{self.location_id}' if self.detail == 'street' else 'city:' + _words(self.city)


class QuotedFee(Model):
    label: Text
    amount: Money


class DeliveryQuote(Model):
    """One retailer pre-order delivery quote and its outcome. Contains no street address."""
    quote_id: Text
    run: v.RunReference  # the search whose offer was quoted
    offer_id: Text
    retailer_id: Text
    seller: Text
    source_id: Text
    source_variant_id: Text
    listing_url: HttpsUrl
    quantity: PositiveInt
    destination: QuoteDestination
    status: QuoteStatus
    requested_at: UtcTime
    observed_at: UtcTime | None = None
    capability: Text | None = None
    endpoint: HttpsUrl | None = None
    response_sha256: Sha256 | None = None
    item_price: Money | None = None  # what the pre-order step charged for the item(s)
    included: tuple[Text, ...] = ()  # charges the seller's terms put inside that price, e.g. tax
    fee: QuotedFee | None = None  # the added delivery fee, counted once
    # The item price plus the quoted fee, as the pre-order step showed them. It is not a payable
    # total when another mandatory charge is unknown; RevisedComparison decides that.
    quoted_total: Money | None = None
    reason: Text | None = None

    @model_validator(mode='after')
    def coherent_outcome(self):
        answered = (self.observed_at, self.capability, self.endpoint, self.response_sha256)
        if self.status == 'quoted':
            if None in answered or None in (self.item_price, self.fee, self.quoted_total) or self.reason is not None:
                raise ValueError('A quote records its source, time, price, fee and total')
            currencies = {self.item_price.currency, self.fee.amount.currency, self.quoted_total.currency}
            if (len(currencies) != 1
                    or _sum([self.item_price.amount, self.fee.amount.amount]) != self.quoted_total.amount):
                raise ValueError('A quoted total is the item price plus the fee, once, in one currency')
        elif self.status in ('unavailable', 'conflicting'):
            if self.reason is None or self.quoted_total is not None:
                raise ValueError('An unusable quote keeps its reason and no total')
        elif any(item is not None for item in (*answered, self.item_price, self.fee, self.quoted_total, self.reason)):
            raise ValueError('A quote that has not been answered has no result')
        return self


class RevisedOffer(Model):
    offer_id: Text
    retailer_id: Text
    seller: Text
    listing_url: HttpsUrl
    before: v.Cost | None = None
    after: v.Cost | None = None
    quote_id: Text | None = None
    rank: PositiveInt | None = None  # among comparable verified totals; equal ranks are ties
    doubts: tuple[Text, ...] = ()


class RevisedComparison(Model):
    """The chat's comparison after delivery quotes. The search snapshot itself is never changed."""
    destination: Text
    revised_at: UtcTime
    offers: tuple[RevisedOffer, ...]
    advice: Text


@dataclass(frozen=True)
class ChatDecision:
    """A chat's destination choice: location city and label only, never its street."""
    status: str
    location_id: str | None = None
    city: str | None = None
    label: str | None = None
    recipients: frozenset = frozenset()


@dataclass(frozen=True)
class Wanted:
    """A quote this chat's latest search implies. route None: recorded unusable, never sent."""
    offer: v.Offer
    destination: QuoteDestination
    route: QuoteCapability | None
    reason: str | None = None

    @property
    def key(self) -> str:
        return request_key(self.offer, QUANTITY, self.destination)


def _words(text) -> str:
    return ' '.join(str(text or '').split()).casefold()


def request_key(offer: v.Offer, quantity: int, destination: QuoteDestination) -> str:
    parts = [offer.retailer_id, offer.source_id, offer.source_variant_id, quantity, destination.key]
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def priced_offers(snapshot: v.RunSnapshot) -> list:
    """(offer, before cost, verified, reasons) for the verified and uncertain results, in order."""
    rows = [(item.offer, item.cost, True, ()) for group in snapshot.recommendations for item in group.offers()]
    rows += [(item.offer, item.cost, False, item.reasons) for item in snapshot.uncertain]
    return rows


def delivery_fee_open(request: v.ShoppingRequest, offer: v.Offer, now: datetime) -> bool:
    """A Lebanese seller's direct delivery whose total is open because of the delivery charge."""
    if not (offer.domestic_route() and isinstance(offer.route, v.DirectRoute)):
        return False
    cost = calculate_domestic_cost(request, offer, now=now)
    return cost.status == 'incomplete' and 'costs.order.shipping' in cost.missing


def _finished(snapshot: v.RunSnapshot) -> datetime:
    return snapshot.finished_at or snapshot.updated_at


def wanted_quotes(snapshot: v.RunSnapshot, decision: ChatDecision | None,
                  routes: dict[str, QuoteCapability]) -> list[Wanted]:
    """The quotes a finished search implies for this chat's current destination."""
    request = snapshot.request
    if request.fulfillment.mode == 'pickup' or snapshot.status not in ('complete', 'partial', 'abstained'):
        return []
    city, when, out, seen = request.fulfillment.destination.city, _finished(snapshot), [], set()
    selected = (decision is not None and decision.status == 'selected' and decision.location_id is not None
                and bool(decision.city))
    for offer, _, _, _ in priced_offers(snapshot):
        if not delivery_fee_open(request, offer, when):
            continue
        route = routes.get(offer.source_id)
        if route is not None and route.destination_detail == 'city':
            if not city:
                continue
            wanted = Wanted(offer, QuoteDestination(detail='city', city=city), route)
        elif selected and offer.retailer_id in decision.recipients:
            destination = QuoteDestination(detail='street', city=decision.city, location_id=decision.location_id,
                                           label=decision.label)
            reason = (NO_ROUTE if route is None
                      else OTHER_CITY if city and _words(city) != _words(decision.city) else None)
            wanted = Wanted(offer, destination, None if reason else route, reason)
        else:
            continue
        if wanted.key not in seen:
            seen.add(wanted.key)
            out.append(wanted)
    return out


def waiting_sellers(snapshot: v.RunSnapshot, routes: dict[str, QuoteCapability]) -> list[tuple[str, str]]:
    """(retailer ID, name) of sellers whose open delivery total a street address could settle."""
    if snapshot.request.fulfillment.mode == 'pickup' or snapshot.status not in ('complete', 'partial', 'abstained'):
        return []
    sellers = {}
    for offer, _, _, _ in priced_offers(snapshot):
        route = routes.get(offer.source_id)
        if (route is not None and route.destination_detail == 'street'
                and delivery_fee_open(snapshot.request, offer, _finished(snapshot))):
            sellers.setdefault(offer.retailer_id, offer.seller.name)
    return list(sellers.items())


def new_quote(wanted: Wanted, run: v.RunReference, now: datetime, reason: str | None = None) -> DeliveryQuote:
    """A pending request, or with a reason one that is recorded unsent."""
    offer = wanted.offer
    return DeliveryQuote(quote_id=str(uuid.uuid4()), run=run, offer_id=offer.offer_id, retailer_id=offer.retailer_id,
                         seller=offer.seller.name, source_id=offer.source_id,
                         source_variant_id=offer.source_variant_id, listing_url=offer.listing_url,
                         quantity=QUANTITY, destination=wanted.destination,
                         status='pending' if reason is None else 'unavailable', requested_at=now, reason=reason)


def _settle(quote: DeliveryQuote, status: str, reason: str, **fields) -> DeliveryQuote:
    data = quote.model_dump(mode='python') | fields | dict(status=status, reason=reason, quoted_total=None)
    return DeliveryQuote.model_validate(data)


def _amount(value) -> Decimal | None:
    return Decimal(value) if isinstance(value, str) and _AMOUNT.fullmatch(value) else None


def _money(node, currency) -> Money | None:
    if not isinstance(node, dict) or node.get('currencyCode') != currency:
        return None
    amount = _amount(node.get('amount'))
    return None if amount is None else Money(amount=amount, currency=currency)


def _label(value) -> str:
    text = ' '.join(value.split()) if isinstance(value, str) else ''
    return text if 1 <= len(text) <= 60 and not _LABEL.search(text) else 'Delivery'


def read_storefront_reply(outcome, quote: DeliveryQuote, offer: v.Offer, rule: QuoteCapability) -> DeliveryQuote:
    """Turn the gate's outcome for a sent request into a settled quote. Retailer text never becomes a
    reason, and a reply of any shape settles the quote rather than raising."""
    if outcome.errors:
        return _settle(quote, 'unavailable', _SENT.get(outcome.errors[0].code, FAILED))
    answered = dict(observed_at=outcome.observed_at, response_sha256=outcome.response_sha256)
    try:
        return _read_cart(outcome, quote, offer, rule, answered)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return _settle(quote, 'unavailable', FAILED, **answered)


def _read_cart(outcome, quote, offer, rule, answered) -> DeliveryQuote:
    try:
        data = json.loads(outcome.body)
        if data.get('errors'):
            return _settle(quote, 'unavailable', FAILED, **answered)
        payload = data['data']['cartCreate']
        errors = payload.get('userErrors') or []
        if errors:
            codes = [error.get('code') for error in errors if isinstance(error, dict)]
            reason = next((_USER_ERRORS[code] for code in codes if code in _USER_ERRORS), FAILED)
            return _settle(quote, 'unavailable', reason, **answered)
        cart = payload['cart']
        lines = cart['lines']['nodes']
        groups = cart['deliveryGroups']['nodes']
    except (ValueError, KeyError, TypeError, AttributeError):
        return _settle(quote, 'unavailable', FAILED, **answered)
    if not isinstance(lines, list) or len(lines) != 1 or not isinstance(lines[0], dict):
        return _settle(quote, 'unavailable', FAILED, **answered)
    line = lines[0]
    merchandise = line.get('merchandise') or {}
    if (merchandise.get('id') != f'gid://shopify/ProductVariant/{quote.source_variant_id}'
            or line.get('quantity') != quote.quantity):
        return _settle(quote, 'unavailable', FAILED, **answered)
    if merchandise.get('availableForSale') is not True:
        return _settle(quote, 'unavailable', _USER_ERRORS['MERCHANDISE_OUT_OF_STOCK'], **answered)
    item = _money((line.get('cost') or {}).get('totalAmount'), rule.currency)
    if not isinstance(groups, list) or len(groups) > 1 or not all(isinstance(group, dict) for group in groups):
        return _settle(quote, 'unavailable', FAILED, **answered)
    options = groups[0].get('deliveryOptions') if groups else []  # no group: nothing delivers there
    if not isinstance(options, list) or not all(isinstance(option, dict) for option in options):
        return _settle(quote, 'unavailable', FAILED, **answered)
    # Only options that deliver: a free in-store pickup is never this order's delivery fee.
    delivered = [option for option in options if option.get('deliveryMethodType') in DELIVERY_METHODS]
    if not delivered:
        return _settle(quote, 'unavailable', _USER_ERRORS['INVALID_DELIVERY_ADDRESS'], **answered)
    priced = [(_money(option.get('estimatedCost'), rule.currency), _label(option.get('title')))
              for option in delivered]
    if item is None or any(money is None for money, _ in priced):
        return _settle(quote, 'unavailable', FAILED, **answered)
    cost, label = min(priced, key=lambda pair: (pair[0].amount, pair[1]))
    fee = QuotedFee(label=label, amount=cost)
    found = dict(answered, item_price=item, fee=fee)
    listed = offer.price.value if offer.price.state == 'known' else None
    expected = None if listed is None else _sum([listed.amount] * quote.quantity)
    if listed is None or listed.currency != item.currency or expected != item.amount:
        shown = 'unknown' if listed is None else money_text(
            Money(amount=expected or listed.amount, currency=listed.currency))
        return _settle(quote, 'conflicting', f'The pre-order step charged {money_text(item)} for the item, '
                                             f'but the listing shows {shown}. Its total stays unknown.', **found)
    published = [charge for charge in offer.costs if charge.leg == 'order' and charge.kind == 'shipping'
                 and charge.state == 'additional']
    if any(charge.amount != cost for charge in published):
        return _settle(quote, 'conflicting', f'The quoted delivery fee {money_text(cost)} differs from the '
                                             'seller\'s published fee. Its total stays unknown.', **found)
    total = _sum([item.amount, cost.amount])
    if total is None:
        return _settle(quote, 'unavailable', FAILED, **answered)
    included = tuple(dict.fromkeys(charge.kind for charge in offer.costs
                                   if charge.leg == 'order' and charge.state == 'included'))
    data = quote.model_dump(mode='python') | found | dict(
        status='quoted', reason=None, included=included, quoted_total=Money(amount=total, currency=item.currency))
    return DeliveryQuote.model_validate(data)


def money_text(money: Money) -> str:
    amount = money.amount
    cents = amount.quantize(Decimal('0.01'))
    text = f'{cents:f}' if cents == amount else f'{amount:f}'
    return f'{money.currency} {text[:-3] if text.endswith(".00") else text}'


def with_quote(offer: v.Offer, quote: DeliveryQuote) -> v.Offer:
    """The offer with its delivery charge taken from the quote, bound to route-scoped evidence."""
    evidence_id = f'quote:{quote.quote_id}:costs.order.shipping'
    record = v.Evidence(
        evidence_id=evidence_id, source_id=offer.source_id, product_id=offer.source_product_id,
        variant_id=offer.source_variant_id, scope='route', route_id=offer.route.route_id,
        field_path='costs.order.shipping', origin_url=quote.endpoint, final_url=quote.endpoint,
        observed_at=quote.observed_at, response_sha256=quote.response_sha256, adapter_version=ADAPTER_VERSION,
        excerpt=f'Pre-order delivery quote for quantity {quote.quantity}: {money_text(quote.fee.amount)}',
        origin='runtime', corpus_version=CORPUS_VERSION, run=quote.run)
    data = offer.model_dump(mode='python')
    data['costs'] = [charge for charge in data['costs']
                     if not (charge['leg'] == 'order' and charge['kind'] == 'shipping')]
    data['costs'].append(dict(kind='shipping', state='additional', amount=quote.fee.amount.model_dump(),
                              evidence_ids=(evidence_id,)))
    data['evidence'] = [*data['evidence'], record.model_dump(mode='python')]
    return v.Offer.model_validate(data)


def _comparable(cost):
    if not isinstance(cost, v.VerifiedCost):
        return None
    return cost.budget_conversion.quote if cost.budget_conversion is not None else cost.total


def revise(snapshot: v.RunSnapshot, quotes: dict[str, DeliveryQuote], destination: str) -> RevisedComparison | None:
    """Recompute totals and ranking for the latest search with its settled quotes (by offer ID)."""
    settled = {offer_id: quote for offer_id, quote in quotes.items() if quote.status in SETTLED}
    if not settled:
        return None
    request, entries = snapshot.request, []
    for offer, before, verified, reasons in priced_offers(snapshot):
        quote = settled.get(offer.offer_id)
        after, rankable, doubts = before, verified, tuple(reasons)
        listed = offer.price.value if offer.price.state == 'known' else None
        if quote is not None and quote.status == 'quoted' and not verified and (
                listed is None or listed.currency != quote.item_price.currency
                or _sum([listed.amount] * quote.quantity) != quote.item_price.amount):
            # A quote from an earlier revision no longer matches a price that has since changed.
            doubts = tuple(dict.fromkeys((*doubts, REPRICED)))
        elif quote is not None and quote.status == 'quoted' and not verified:
            when = max(quote.observed_at, _finished(snapshot))
            assessment = assess_full_product(request, with_quote(offer, quote), now=when)
            after, rankable = assessment.cost, assessment.decision == 'recommendable'
            doubts = () if rankable else _doubts(assessment)
        elif quote is not None and quote.status != 'quoted':
            doubts = tuple(dict.fromkeys((*doubts, quote.reason)))
        entries.append(dict(offer_id=offer.offer_id, retailer_id=offer.retailer_id, seller=offer.seller.name,
                            listing_url=offer.listing_url, before=before, after=after, doubts=doubts,
                            quote_id=quote.quote_id if quote else None, rankable=rankable))
    totals = [(entry, _comparable(entry['after'])) for entry in entries if entry['rankable']]
    totals = [(entry, total) for entry, total in totals if total is not None]
    budget = request.budget.maximum or request.budget.target if request.budget else None
    counts = Counter(total.currency for _, total in totals)
    currency = budget.currency if budget and budget.currency in counts else min(
        counts, key=lambda code: (-counts[code], code), default=None)
    ranked = sorted(((entry, total) for entry, total in totals if total.currency == currency),
                    key=lambda pair: (pair[1].amount, pair[0]['offer_id']))
    for entry, total in totals:
        if total.currency != currency:
            entry['doubts'] = (*entry['doubts'], f'Verified total in {total.currency} is not compared with '
                                                 f'totals in {currency}.')
    rank, previous = 0, None
    for position, (entry, total) in enumerate(ranked, start=1):
        if total.amount != previous:
            rank, previous = position, total.amount
        entry['rank'] = rank
    offers = tuple(RevisedOffer(**{key: value for key, value in entry.items() if key != 'rankable'})
                   for entry in entries)
    before = min(((_comparable(entry['before']), entry['seller']) for entry in entries
                  if entry['rankable'] and entry['quote_id'] is None and _comparable(entry['before']) is not None
                  and _comparable(entry['before']).currency == currency), key=lambda pair: pair[0].amount,
                 default=None)
    return RevisedComparison(destination=destination, revised_at=max(q.observed_at or q.requested_at
                                                                    for q in settled.values()),
                             offers=offers, advice=_advice(offers, before, settled, destination))


def _advice(offers, before, settled, destination) -> str:
    best = [offer for offer in offers if offer.rank == 1]
    parts = []
    if best:
        total = money_text(_comparable(best[0].after))
        names = ' and '.join(offer.seller for offer in best)
        parts.append(f'For delivery to {destination}, {names} '
                     + (f'has the lowest verified total: {total}.' if len(best) == 1
                        else f'tie on the lowest verified total: {total}.'))
        if before is not None and before[1] not in {offer.seller for offer in best} and before[0].amount > _comparable(
                best[0].after).amount:
            parts.append(f'Before the delivery quotes, {before[1]} was the cheapest verified offer at '
                         f'{money_text(before[0])}.')
    else:
        parts.append(f'No offer has a verified total for delivery to {destination} yet.')
    for quote in settled.values():
        if quote.status == 'unavailable':
            parts.append(f'{quote.seller} gave no usable delivery quote, so its total stays unknown.')
        elif quote.status == 'conflicting':
            parts.append(f'{quote.seller}\'s quote disagreed with its listing, so its total stays unknown.')
    parts.append('Prices and stock were checked during the search. You buy on the seller\'s own site.')
    return ' '.join(dict.fromkeys(parts))


# Storage: planning, the chat view and the destination summary.

def read_decision(db, session_hash: str, request_id: str) -> tuple[ChatDecision | None, datetime | None]:
    row = db.execute(select(buyer_destinations).where(buyer_destinations.c.session_hash == session_hash,
                                                      buyer_destinations.c.request_id == request_id)).mappings().first()
    if row is None:
        return None, None
    city = label = None
    if row['location_id'] is not None:
        place = db.execute(select(buyer_locations.c.city, buyer_locations.c.label).where(
            buyer_locations.c.location_id == row['location_id'])).first()
        city, label = (place.city, place.label) if place else (None, None)
    recipients = frozenset(item['retailer_id'] for item in row['recipients'])
    return ChatDecision(row['status'], row['location_id'], city, label, recipients), row['decided_at']


def latest_snapshot(db, session_hash: str, request_id: str) -> v.RunSnapshot | None:
    data = db.execute(select(buyer_runs.c.snapshot).where(
        buyer_runs.c.session_hash == session_hash, buyer_runs.c.request_id == request_id)
        .order_by(buyer_runs.c.request_revision.desc()).limit(1)).scalar_one_or_none()
    return None if data is None else v.RunSnapshot.model_validate(data)


def _rows(db, session_hash, request_id, wanted):
    keys = [item.key for item in wanted]
    if not keys:
        return {}
    rows = db.execute(select(buyer_quotes.c.request_key, buyer_quotes.c.record).where(
        buyer_quotes.c.session_hash == session_hash, buyer_quotes.c.request_id == request_id,
        buyer_quotes.c.request_key.in_(keys))).all()
    return {row.request_key: DeliveryQuote.model_validate(row.record) for row in rows}


def chat_quotes(db, session_hash, request_id, snapshot, decision, routes):
    """The quotes that apply to the chat's latest search and destination, and its revision."""
    wanted = wanted_quotes(snapshot, decision, routes)
    found = _rows(db, session_hash, request_id, wanted)
    quotes = tuple(found[item.key] for item in wanted if item.key in found)
    selected = decision is not None and decision.status == 'selected' and decision.city
    city = snapshot.request.fulfillment.destination.city
    place = f'{decision.label} · {decision.city}' if selected and any(
        quote.destination.detail == 'street' for quote in quotes) else (city or 'Lebanon')
    revised = revise(snapshot, {quote.offer_id: quote for quote in quotes}, place)
    return quotes, revised


def summary(db, session_hash, request_id, snapshot, decision, routes) -> str:
    """quote_state for a selected destination: what happened to its street-level quotes."""
    street = [item for item in wanted_quotes(snapshot, decision, routes) if item.destination.detail == 'street']
    if not street:
        return 'not_needed'
    found = _rows(db, session_hash, request_id, street)
    states = [found[item.key].status if item.key in found else 'pending' for item in street]
    if 'requested' in states:
        return 'requested'
    if 'pending' in states:
        return 'pending'
    usable = states.count('quoted')
    return 'quoted' if usable == len(states) else 'partial' if usable else 'unavailable'


def refresh_state(db, session_hash, request_id, routes) -> None:
    decision, _ = read_decision(db, session_hash, request_id)
    snapshot = latest_snapshot(db, session_hash, request_id)
    if decision is None or decision.status != 'selected' or snapshot is None:
        return
    db.execute(update(buyer_destinations).where(
        buyer_destinations.c.session_hash == session_hash, buyer_destinations.c.request_id == request_id)
        .values(quote_state=summary(db, session_hash, request_id, snapshot, decision, routes)))


def plan_chat(db, now, session_hash, request_id, routes) -> int:
    """Record the quotes the chat's latest finished search implies. Returns new requests.

    A request that was sent, or is waiting to be, is never planned again. One recorded unsent
    (no route, another city, the allowance, a refusal before sending) is reconsidered here. A
    search may start at most MAX_QUOTES_PER_SEARCH requests across all its destination choices."""
    snapshot = latest_snapshot(db, session_hash, request_id)
    if snapshot is None:
        return 0
    decision, decided_at = read_decision(db, session_hash, request_id)
    wanted = wanted_quotes(snapshot, decision, routes)
    chat = (buyer_quotes.c.session_hash == session_hash, buyer_quotes.c.request_id == request_id)
    keys = [item.key for item in wanted]
    existing = {row.request_key: row for row in db.execute(select(
        buyer_quotes.c.request_key, buyer_quotes.c.quote_id, buyer_quotes.c.status, buyer_quotes.c.sent).where(
        *chat, buyer_quotes.c.request_key.in_(keys))).all()} if keys else {}
    used = len(db.execute(select(buyer_quotes.c.quote_id).where(
        *chat, buyer_quotes.c.run_id == snapshot.run.run_id,
        or_(buyer_quotes.c.sent.is_(True), buyer_quotes.c.status.in_(('pending', 'requested'))))).all())
    created = 0
    for item in wanted:
        row = existing.get(item.key)
        if row is not None and (row.sent or row.status in ('pending', 'requested')):
            continue
        if row is not None:
            db.execute(delete(buyer_quotes).where(buyer_quotes.c.quote_id == row.quote_id))
        reason = item.reason or (OVER_LIMIT if used >= MAX_QUOTES_PER_SEARCH else None)
        quote = new_quote(item, snapshot.run, now, reason)
        db.execute(insert(buyer_quotes).values(
            quote_id=quote.quote_id, session_hash=session_hash, request_id=request_id, run_id=snapshot.run.run_id,
            request_key=item.key, status=quote.status, record=quote.model_dump(mode='json'), sent=False,
            deferrals=0, created_at=now, updated_at=now))
        if reason is None:
            used += 1
            created += 1
    db.execute(delete(buyer_quote_plans).where(buyer_quote_plans.c.run_id == snapshot.run.run_id))
    db.execute(insert(buyer_quote_plans).values(run_id=snapshot.run.run_id, decided_at=decided_at, planned_at=now))
    refresh_state(db, session_hash, request_id, routes)
    return created


class QuoteService:
    """Plans, sends and records delivery quotes. Network calls happen outside every transaction."""

    def __init__(self, accounts, *, gate, ledger, registry, lease=LEASE):
        self.accounts, self.runs = accounts, accounts.runs
        self.gate, self.ledger, self.registry, self.lease = gate, ledger, registry, lease

    def routes(self) -> dict[str, QuoteCapability]:
        return active_quote_routes(self.registry)

    def plan(self, limit=20) -> int:
        """Plan finished latest searches that are new or whose destination decision changed."""
        routes = self.routes()
        d, p, newer = buyer_destinations, buyer_quote_plans, buyer_runs.alias('newer')
        latest = ~select(1).where(newer.c.session_hash == buyer_runs.c.session_hash,
                                  newer.c.request_id == buyer_runs.c.request_id,
                                  newer.c.request_revision > buyer_runs.c.request_revision).exists()
        stale = or_(p.c.run_id.is_(None),
                    and_(d.c.decided_at.is_(None), p.c.decided_at.is_not(None)),
                    and_(d.c.decided_at.is_not(None), or_(p.c.decided_at.is_(None), p.c.decided_at != d.c.decided_at)))
        with self.runs.guarded() as (db, now):
            chats = db.execute(select(buyer_runs.c.session_hash, buyer_runs.c.request_id).select_from(
                buyer_runs.join(buyer_sessions, buyer_sessions.c.session_hash == buyer_runs.c.session_hash)
                .outerjoin(d, and_(d.c.session_hash == buyer_runs.c.session_hash,
                                   d.c.request_id == buyer_runs.c.request_id))
                .outerjoin(p, p.c.run_id == buyer_runs.c.run_id))
                .where(buyer_sessions.c.expires_at > now, latest, stale,
                       buyer_runs.c.status.in_(('complete', 'partial', 'abstained')))
                .order_by(buyer_runs.c.run_id).limit(limit)).all()
            for chat in chats:
                plan_chat(db, now, chat.session_hash, chat.request_id, routes)
        return len(chats)

    @staticmethod
    def _settle_row(db, now, row, quote, *, sent):
        db.execute(update(buyer_quotes).where(buyer_quotes.c.quote_id == row['quote_id']).values(
            status=quote.status, record=quote.model_dump(mode='json'), sent=sent, lease_token=None,
            lease_until=None, not_before=None, updated_at=now))

    def _claim(self):
        """Claim the next pending quote that still applies, or None. One at a time, so a lease
        covers a single gated call."""
        routes = self.routes()
        with self.runs.guarded() as (db, now):
            stale = db.execute(select(buyer_quotes).where(buyer_quotes.c.status == 'requested',
                                                          buyer_quotes.c.lease_until <= now)).mappings().all()
            for row in stale:  # it may have been sent, so it is never sent again
                quote = _settle(DeliveryQuote.model_validate(row['record']), 'unavailable', INTERRUPTED)
                self._settle_row(db, now, row, quote, sent=True)
                refresh_state(db, row['session_hash'], row['request_id'], routes)
            for _ in range(20):
                row = db.execute(select(buyer_quotes).join(
                    buyer_sessions, buyer_sessions.c.session_hash == buyer_quotes.c.session_hash).where(
                    buyer_quotes.c.status == 'pending', buyer_sessions.c.expires_at > now,
                    or_(buyer_quotes.c.not_before.is_(None), buyer_quotes.c.not_before <= now))
                    .order_by(buyer_quotes.c.created_at, buyer_quotes.c.quote_id).limit(1)).mappings().first()
                if row is None:
                    return None
                quote = DeliveryQuote.model_validate(row['record'])
                snapshot = latest_snapshot(db, row['session_hash'], row['request_id'])
                decision, _ = read_decision(db, row['session_hash'], row['request_id'])
                current = {item.key: item for item in wanted_quotes(snapshot, decision, routes)}
                item = current.get(row['request_key'])
                if item is None or item.route is None:
                    reason = STALE if item is None else item.reason or NO_ROUTE
                    self._settle_row(db, now, row, _settle(quote, 'unavailable', reason), sent=False)
                    refresh_state(db, row['session_hash'], row['request_id'], routes)
                    continue
                token = uuid.uuid4().hex
                quote = quote.model_copy(update=dict(status='requested'))
                db.execute(update(buyer_quotes).where(buyer_quotes.c.quote_id == row['quote_id']).values(
                    status='requested', record=quote.model_dump(mode='json'), lease_token=token,
                    lease_until=now + self.lease, updated_at=now))
                refresh_state(db, row['session_hash'], row['request_id'], routes)
                return (row['quote_id'], token, row['session_hash'], row['request_id'], quote, item.offer)
            return None

    def _request(self, quote: DeliveryQuote, offer: v.Offer) -> tuple[DeliveryQuote, bool, bool]:
        """Send one claimed quote: (result, whether it was sent, whether to defer it). The released
        address lives only inside this call."""
        route = self.routes().get(quote.source_id)
        if route is None or route.destination_detail != quote.destination.detail:
            return _settle(quote, 'unavailable', NO_ROUTE), False, False
        place = dict(city=quote.destination.city)
        if quote.destination.detail == 'street':
            released = self.accounts.release_address(quote.run, quote.retailer_id,
                                                     location_id=quote.destination.location_id)
            if released is None:
                return _settle(quote, 'unavailable', WITHDRAWN), False, False
            place = dict(city=released.city, street=released.street, details=released.details)
            del released
        try:
            body = storefront_body(route, offer.source_variant_id, quote.quantity, **place)
        except ValueError:
            return _settle(quote, 'unavailable', NOT_SENT), False, False
        finally:
            place.clear()
        prepared = quote.model_copy(update=dict(capability=f'{route.capability_id}@{route.version}',
                                                endpoint=quote_url(route)))
        try:
            budget = self.ledger.open_job('quote:' + quote.quote_id).source_budget(self.registry)
        except BudgetDenied:
            return quote, False, True
        try:
            outcome = self.gate.quote(budget, quote.source_id,
                                      CapabilityRef(capability_id=route.capability_id, version=route.version), body)
        except Exception as exc:  # it may have been sent; the address is never logged, only the type
            log.warning('Delivery quote %s failed: %s', quote.quote_id, type(exc).__name__)
            return _settle(prepared, 'unavailable', FAILED), True, False
        finally:
            del body
        if outcome.errors and not outcome.attempts:  # refused before anything was sent
            if outcome.errors[0].code in _BUSY:
                return quote, False, True
            return _settle(prepared, 'unavailable', ROUTE_OFF), False, False
        return read_storefront_reply(outcome, prepared, offer, route), True, False

    def _record(self, claim, result: DeliveryQuote, sent: bool, defer: bool) -> None:
        quote_id, token, session_hash, request_id = claim[:4]
        routes = self.routes()
        with self.runs.guarded() as (db, now):
            row = db.execute(select(buyer_quotes).where(
                buyer_quotes.c.quote_id == quote_id, buyer_quotes.c.lease_token == token,
                buyer_quotes.c.status == 'requested')).mappings().first()
            if row is None:  # the lease expired and the quote was settled meanwhile
                return
            if defer and row['deferrals'] < MAX_DEFERRALS:
                waiting = result.model_copy(update=dict(status='pending'))
                db.execute(update(buyer_quotes).where(buyer_quotes.c.quote_id == quote_id).values(
                    status='pending', record=waiting.model_dump(mode='json'), lease_token=None,
                    lease_until=None, not_before=now + DEFER, deferrals=row['deferrals'] + 1, updated_at=now))
            else:
                final = _settle(result, 'unavailable', WORKLOAD) if defer else result
                self._settle_row(db, now, row, final, sent=sent)
            refresh_state(db, session_hash, request_id, routes)

    def execute(self, limit=4) -> int:
        """Send up to limit pending quotes, one at a time, and record each outcome. A failure in one
        quote settles that quote only."""
        done = 0
        while done < limit:
            claim = self._claim()
            if claim is None:
                break
            try:
                result, sent, defer = self._request(claim[4], claim[5])
            except Exception as exc:
                log.warning('Delivery quote %s failed: %s', claim[0], type(exc).__name__)
                result, sent, defer = _settle(claim[4], 'unavailable', FAILED), True, False
            self._record(claim, result, sent, defer)
            done += 1
        return done

    def step(self) -> bool:
        """One pass: plan finished chats, then send pending quotes. True when work was done."""
        planned = self.plan()
        return bool(planned + self.execute())


def run_forever(service: QuoteService, stop: threading.Event, idle_seconds=0.5) -> None:
    """The worker's quote loop. A failed pass is logged by type only and the loop continues."""
    while not stop.is_set():
        try:
            busy = service.step()
        except Exception as exc:
            log.warning('Delivery quote pass failed: %s', type(exc).__name__)
            busy = False
        if not busy:
            stop.wait(idle_seconds)
