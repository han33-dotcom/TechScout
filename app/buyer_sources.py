"""One-source offer reading for the first live buyer flow (#75, T39): Class Cellulars phones.

The existing v1 reader (app.adapters.class_cellulars) reads listings and the FAQ through the
source gate and the shared ledger. This module converts its offers to v2 offers for the buyer
workspace. Conversion only moves facts and the evidence behind them. A fact the v2 contract
cannot bind to the selected variant or delivery route becomes unknown with a reason, never a
guess: a store-wide delivery banner is not route evidence, and the store publishes no
per-item delivery eligibility or condition. Nothing here decides eligibility, cost or
verification; app.buyer_flow does that with the shared rules.

Each evidence record carries this run's reference only when the gate returned its reply in
this run. Listings stored by an earlier run can be reused for discovery ("stored" mode), but
their records keep their original times and no run reference, so they can never verify a
finalist: the final re-read always goes to the store.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Literal

from pydantic import ValidationError

from app import contracts as v1
from app import contracts_v2 as v2
from app.adapters.base import CandidateQuery, PolicyDocument, Snapshot
from app.adapters.class_cellulars import ADAPTER as CLASS_ADAPTER
from app.devices import match_model

CORPUS_VERSION = 'buyer-run-v1'
POLICY_ID = 'faq'
# v1 evidence field -> v2 offer field. Facts without a v2 field (manufacturer/model text,
# device or travel cost lines) are not carried over.
FIELDS = {
    'price': 'price', 'purchasable': 'purchasable', 'online_stock': 'stock', 'condition': 'condition',
    'payment_basis': 'payment', 'arrival': 'arrival',
    'variant.storage_gb': 'variant.specs.storage_gb', 'variant.sim': 'variant.specs.sim',
    'variant.region': 'variant.specs.region', 'variant.color': 'variant.color',
    'warranty.provider': 'warranty.provider', 'warranty.months': 'warranty.months',
    'warranty.territory': 'warranty.territory',
}
CHARGES = {'tax': 'tax', 'delivery': 'shipping', 'payment': 'payment', 'other': 'other'}
NOT_ROUTE_EVIDENCE = 'The delivery fee is published store-wide, not for this delivery route'
NO_ITEM_ELIGIBILITY = 'The store does not publish per-item delivery eligibility for this route'
NO_TAX = 'Tax treatment is not published for this listing'
SEVERAL_STATEMENTS = 'Several store statements on this charge were not combined'
UNBOUND = 'Its evidence could not be bound to this field'

Reply = tuple[str, datetime]


@dataclass(frozen=True)
class DeliveryChoice:
    """The single delivery route this flow reads offers for."""
    city: str

    @property
    def route_id(self) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', self.city.casefold()).strip('-') or 'city'
        return f'delivery:LB:{slug[:60]}'


@dataclass(frozen=True)
class SourceRead:
    """One read of the source: converted offers plus what the controller needs to judge them."""
    mode: Literal['live', 'stored', 'refresh']
    offers: tuple[v2.Offer, ...] = ()
    originals: dict = field(default_factory=dict)  # offer_id -> v1 offer, as read (policies applied)
    policies: tuple[PolicyDocument, ...] = ()
    replies: frozenset = frozenset()  # (response_sha256, observed_at) received in this read
    fetches: tuple[dict, ...] = ()  # sanitized gate entries for the trace
    errors: tuple[str, ...] = ()  # fixed-wording tool errors
    skipped: tuple[str, ...] = ()  # offers that could not be converted, with the reason


class _Recorder:
    """Wraps one gate fetch: records every reply this run received and a sanitized entry."""
    def __init__(self, fetch):
        self.fetch, self.replies, self.entries = fetch, set(), []

    def __call__(self, source_id, url, *, expect, capability=None):
        kwargs = {} if capability is None else {'capability': capability}
        outcome = self.fetch(source_id, url, expect=expect, **kwargs)
        if outcome.response_sha256 is not None and outcome.observed_at is not None:
            self.replies.add((outcome.response_sha256, outcome.observed_at))
        self.entries.append(dict(url=str(url)[:300], expect=expect,
                                 status='ok' if not outcome.errors else 'error',
                                 error_code=outcome.errors[0].code if outcome.errors else None,
                                 attempts=outcome.attempts, duration_ms=outcome.duration_ms,
                                 response_sha256=outcome.response_sha256,
                                 observed_at=None if outcome.observed_at is None else outcome.observed_at.isoformat()))
        return outcome


def _evidence(record: v1.Evidence, path: str, run: v2.RunReference | None) -> v2.Evidence:
    data = record.model_dump()
    data.update(schema_version=2, field_path=path, origin='runtime', corpus_version=CORPUS_VERSION,
                index_version=None, run=run)
    return v2.Evidence.model_validate(data)


def _listing_url(offer: v1.Offer) -> str:
    """The product page the buyer opens; the Store API record is not a listing page."""
    for record in offer.evidence:
        url = str(record.final_url)
        if '/wp-json/' not in url and '/product/' in url:
            return url
    raise ValueError('No product page evidence for this listing')


class _Binder:
    """Builds v2 facts whose evidence records are carried over under their v2 field path."""
    def __init__(self, offer: v1.Offer, run, current: set):
        self.records = {r.evidence_id: r for r in offer.evidence}
        self.run, self.current, self.kept = run, current, {}

    def _run_for(self, record):
        return self.run if (record.response_sha256, record.observed_at) in self.current else None

    def ids(self, evidence_ids: Sequence[str], path: str, *, scopes=None) -> list[str] | None:
        """Carry every record over under ``path``, or none when any cannot support it there."""
        bound = {}
        for evidence_id in evidence_ids:
            record = self.records.get(evidence_id)
            if record is None or FIELDS.get(record.field_path, _charge_path(record.field_path)) != path:
                return None
            if scopes and record.scope not in scopes:
                return None
            bound[evidence_id] = _evidence(record, path, self._run_for(record))
        self.kept.update(bound)
        return list(bound)

    def fact(self, fact: v1.Fact | None, path: str, *, scopes=None) -> dict | None:
        """The fact as contract data; unknown with a reason when its evidence cannot be bound."""
        if fact is None:
            return None
        if fact.evidence_ids and self.ids(fact.evidence_ids, path, scopes=scopes) is None:
            return dict(state='unknown', reason=UNBOUND)
        return fact.model_dump(mode='python')


def _charge_path(v1_path: str) -> str | None:
    if v1_path.startswith('costs.'):
        kind = CHARGES.get(v1_path.split('.', 1)[1])
        return None if kind is None else f'costs.order.{kind}'
    return None


def _charges(offer: v1.Offer, binder: _Binder) -> tuple[v2.Charge, ...]:
    grouped = {}
    for component in offer.costs:
        kind = CHARGES.get(component.kind)
        if kind is not None:
            grouped.setdefault(kind, []).append(component)
    charges = []
    for kind in ('tax', 'shipping', 'payment', 'other'):
        components = grouped.get(kind, [])
        if not components:
            if kind in ('tax', 'shipping'):  # mandatory for a direct-delivery total
                reason = NO_TAX if kind == 'tax' else 'The delivery fee is not published for this route'
                charges.append(v2.Charge(kind=kind, state='unknown', reason=reason))
            continue
        if len(components) > 1:
            charges.append(v2.Charge(kind=kind, state='unknown', reason=SEVERAL_STATEMENTS))
            continue
        component = components[0]
        path = f'costs.order.{kind}'
        if component.state == 'unknown':
            charges.append(v2.Charge(kind=kind, state='unknown', reason=component.reason or UNBOUND,
                                     conditions=tuple(component.conditions)))
            continue
        scopes = {'route'} if kind == 'shipping' else None
        ids = binder.ids(component.evidence_ids, path, scopes=scopes)
        if ids is None:
            reason = NOT_ROUTE_EVIDENCE if kind == 'shipping' else UNBOUND
            charges.append(v2.Charge(kind=kind, state='unknown', reason=reason))
            continue
        charges.append(v2.Charge(kind=kind, state=component.state, amount=component.amount,
                                 reason=component.reason, conditions=tuple(component.conditions),
                                 evidence_ids=tuple(ids), valid_until=component.valid_until))
    return tuple(charges)


def convert_offer(offer: v1.Offer, *, run: v2.RunReference, current: set, route: DeliveryChoice,
                  retailer_id: str, seller_name: str) -> v2.Offer:
    """A v1 delivery offer as a v2 direct-delivery offer; raises ValueError when it cannot be one."""
    if offer.fulfillment.mode != 'delivery':
        raise ValueError('Only delivery offers are converted in this flow')
    variant = offer.variant
    if variant.manufacturer.state != 'known' or variant.model.state != 'known':
        raise ValueError('The listing does not identify its manufacturer and model')
    binder = _Binder(offer, run, current)
    variant_scope = {'variant'}
    specs = dict(category='phone',
                 storage_gb=binder.fact(variant.storage_gb, 'variant.specs.storage_gb', scopes=variant_scope),
                 sim=binder.fact(variant.sim, 'variant.specs.sim', scopes=variant_scope),
                 region=binder.fact(variant.region, 'variant.specs.region', scopes=variant_scope))
    warranty = dict(provider=binder.fact(offer.warranty.provider, 'warranty.provider'),
                    months=binder.fact(offer.warranty.months, 'warranty.months'),
                    territory=binder.fact(offer.warranty.territory, 'warranty.territory'))
    data = dict(
        offer_id=offer.offer_id, retailer_id=retailer_id, source_id=offer.source_id,
        source_product_id=variant.product_id, source_variant_id=variant.variant_id,
        seller=dict(seller_id=offer.source_id, name=seller_name, market='LB'),
        variant=dict(variant_id=variant.variant_id,
                     product=dict(product_id=f'{offer.source_id}:{variant.product_id}', category='phone',
                                  manufacturer=variant.manufacturer.value, model=variant.model.value),
                     specs=specs, color=binder.fact(variant.color, 'variant.color', scopes=variant_scope)),
        listing_url=_listing_url(offer),
        route=dict(kind='direct', route_id=route.route_id, origin_country='LB',
                   destination=dict(country='LB', city=route.city),
                   item_eligible=dict(state='unknown', reason=NO_ITEM_ELIGIBILITY)),
        price=binder.fact(offer.price, 'price', scopes=variant_scope),
        stock=binder.fact(offer.online_stock, 'stock', scopes=variant_scope),
        purchasable=binder.fact(offer.purchasable, 'purchasable', scopes=variant_scope),
        condition=binder.fact(offer.condition, 'condition', scopes=variant_scope),
        payment=binder.fact(offer.payment_basis, 'payment'),
        warranty=warranty,
        arrival=binder.fact(offer.arrival, 'arrival'),
        costs=_charges(offer, binder),
    )
    data['evidence'] = tuple(binder.kept.values())
    return v2.Offer.model_validate(data)


def query_for(request: v2.ShoppingRequest, route: DeliveryChoice) -> CandidateQuery:
    intent = request.intent
    storage = getattr(request.requirements, 'storage_gb', None) or 0  # the reader lists every variant
    return CandidateQuery(manufacturer=intent.manufacturer or '', model=intent.model, storage_gb=storage,
                          fulfillment=v1.Fulfillment(mode='delivery', country='LB', city=route.city))


class ClassCellularsSource:
    """The one source of this flow. ``fetcher(job)`` returns the gate fetch bound to the run's
    ledger job (fake source HTTP in tests). ``storage`` keeps raw replies and listings so a
    later run can reuse them for discovery; ``reuse_for`` bounds how old a reused listing may be."""
    source_id = 'class_cellulars'
    retailer_id = 'class_cellulars'
    name = 'Class Cellulars'
    supported_categories = ('phone',)

    def __init__(self, fetcher: Callable, *, storage=None, adapter=CLASS_ADAPTER,
                 reuse_for: timedelta = timedelta(days=7), clock=lambda: datetime.now(timezone.utc)):
        self.fetcher, self.storage, self.adapter = fetcher, storage, adapter
        self.reuse_for, self.clock = reuse_for, clock

    def adapter_version(self) -> str:
        return self.adapter.adapter_version

    def _keep(self, result) -> None:
        if self.storage is None:
            return
        for snapshot in result.snapshots:
            self.storage.save_snapshot(snapshot.body, snapshot.media_type)

    def _stored(self, query: CandidateQuery) -> list[v1.Offer]:
        if self.storage is None:
            return []
        wanted = match_model(query.model)
        if wanted is None:
            return []
        found = []
        for offer in self.storage.recent_offers(self.source_id, self.clock() - self.reuse_for):
            route = offer.fulfillment
            if (offer.variant.model.state == 'known' and match_model(offer.variant.model.value) == wanted
                    and route.mode == 'delivery' and route.city.casefold() == query.fulfillment.city.casefold()):
                found.append(offer)
        return found

    def _convert(self, offers, run, current, route, mode, recorder, policies, errors) -> SourceRead:
        converted, originals, skipped = [], {}, []
        for offer in offers:
            for policy in policies:
                offer = self.adapter.apply_policy(offer, policy)
            try:
                converted.append(convert_offer(offer, run=run, current=current, route=route,
                                               retailer_id=self.retailer_id, seller_name=self.name))
                originals[offer.offer_id] = offer
            except (ValueError, ValidationError) as exc:
                skipped.append(f'{offer.offer_id}: {_reason(exc)}')
        return SourceRead(mode=mode, offers=tuple(converted), originals=originals, policies=tuple(policies),
                          replies=frozenset(recorder.replies), fetches=tuple(recorder.entries),
                          errors=tuple(errors), skipped=tuple(skipped))

    def discover(self, request, route: DeliveryChoice, job, run, *, reuse=True) -> SourceRead:
        """Listings for the requested model: reused from storage when recent ones exist,
        otherwise read live. The FAQ policy page is always read in this run."""
        query = query_for(request, route)
        recorder = _Recorder(self.fetcher(job))
        errors = []
        stored = self._stored(query) if reuse else []
        if stored:
            mode, offers = 'stored', stored
        else:
            mode = 'live'
            result = self.adapter.search_candidates(recorder, query)
            self._keep(result)
            errors += [f'{e.code}: {e.message}' for e in result.tool_result.errors]
            offers = list(result.offers)
            if self.storage is not None:
                for offer in offers:  # raw reader output, before this run's policy pages
                    self.storage.save_offer(offer)
        policies = []
        if offers:
            policy = self.adapter.fetch_policy(recorder, POLICY_ID)
            self._keep(policy)
            errors += [f'{e.code}: {e.message}' for e in policy.tool_result.errors]
            policies = list(policy.policies)
        return self._convert(offers, run, set(recorder.replies), route, mode, recorder, policies, errors)

    def refresh(self, request, route: DeliveryChoice, job, run, offer_ids: Sequence[str], *,
                earlier: SourceRead) -> SourceRead:
        """The final re-read of the finalists' listings in this run, with this run's policy pages."""
        query = query_for(request, route)
        recorder = _Recorder(self.fetcher(job))
        result = self.adapter.fetch_offers(recorder, list(offer_ids), query)
        self._keep(result)
        wanted = set(offer_ids)
        offers = [o for o in result.offers if o.offer_id in wanted]
        current = set(recorder.replies) | set(earlier.replies)
        errors = [f'{e.code}: {e.message}' for e in result.tool_result.errors]
        return self._convert(offers, run, current, route, 'refresh', recorder, earlier.policies, errors)


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        return str(first.get('msg', 'invalid offer'))[:200]
    return str(exc)[:200]
