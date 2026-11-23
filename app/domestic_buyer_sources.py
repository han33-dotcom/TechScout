"""Project seeded listing evidence into buyer offers and re-read reviewed product URLs.

A catalog observation can select the listing and SKU for a recheck. Only the newly
returned page supplies the fields and receipts of the resulting runtime offer.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import time
from typing import Literal
from urllib.parse import urldefrag, urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import contracts_v2 as v2
from app.accounting import BudgetDenied, Charge
from app.buyer_sources import DeadlineBudget
from app.corpus_contracts import DocumentCapture, ProcessingVersion, ResourceKey
from app.directory import DirectoryRecord
from app.domestic_catalog import DomesticCatalog, exact_variant_key, extract_listing
from app.domestic_retailer_profiles import extract_dispatch_membership
from app.extraction import _HTML, _objects, _unique_pairs
from app.firecrawl import retrieve_page
from app.onboarding import CATEGORY_WORDS, canonical
from app.source_capabilities import CapabilityRef
from app.source_registry import load_registry
from app.sources import CALL_TIMEOUT_S
from app.storage import directory_entries


@dataclass(frozen=True)
class ReadResult:
    source_id: str | None
    offers: tuple[v2.Offer, ...] = ()
    replies: frozenset[tuple[str, datetime]] = frozenset()
    status: Literal['ok', 'unconfigured', 'out_of_scope', 'failed', 'refused'] = 'ok'
    reason: str | None = None
    dispatch_replies: frozenset[tuple[str, datetime]] = frozenset()


@dataclass(frozen=True)
class _Listing:
    source_id: str
    retailer_id: str
    seller_id: str
    seller_name: str
    seller_aliases: tuple[str, ...]
    listing_url: str
    product_id: str
    sku: str
    category: str
    manufacturer: str
    model: str


_RUNTIME = ProcessingVersion(corpus_version='domestic-buyer-1', index_version='domestic-buyer-1',
                             extraction_version='domestic-listing-1', chunk_version='source-fields-1')
_SPECS = {key: model for key, model in (
    ('phone', v2.PhoneSpecs), ('tablet', v2.TabletSpecs), ('laptop', v2.LaptopSpecs),
    ('monitor', v2.MonitorSpecs), ('audio', v2.AudioSpecs))}
_UNKNOWN = dict(state='unknown', reason='The selected listing does not establish this field')
_MEMBERSHIP = {'/collection/dispatch/' + key for key in ('statement', 'listing', 'variant', 'product')}


def _route_id(destination):
    return 'delivery:' + sha256(destination.model_dump_json().encode()).hexdigest()[:24]


def _membership_url(observation, listing):
    if not observation or observation.fact.state != 'known' or observation.fact.value != 'LB':
        return None
    records = observation.evidence
    if ({r.json_pointer for r in records} != _MEMBERSHIP or any(
            r.field_path != 'route.origin_country' or r.scope != 'route' or r.conflicts or
            (r.source_id, r.product_id, r.variant_id) != (listing.source_id, listing.product_id, listing.sku)
            for r in records)):
        return None
    url = str(records[0].origin_url)
    if (url == listing.listing_url or urlsplit(url).netloc != urlsplit(listing.listing_url).netloc
            or any(str(r.origin_url) != url or str(r.final_url) != url for r in records)):
        return None
    return url


def _capture(outcome, listing, route_id):
    return DocumentCapture(key=ResourceKey(source_id=listing.source_id, url=outcome.origin_url,
        representation='product-html', product_id=listing.product_id, variant_id=listing.sku, route_id=route_id),
        final_url=outcome.final_url, visibility='public', origin='runtime', observed_at=outcome.observed_at,
        checked_at=outcome.observed_at, body=outcome.body, relevant_content=outcome.body, processing=_RUNTIME)


def _offer(listing, fields, destination, *, current=False):
    """Project only observations bound to this source, SKU, URL and applicable route."""
    route_id = _route_id(destination)
    evidence = {}
    accepted = {}

    def bound(path):
        observation = fields.get(path)
        if not observation:
            return None
        for record in observation.evidence:
            if (record.source_id != listing.source_id or record.product_id != listing.product_id
                    or record.variant_id != listing.sku or str(record.origin_url) != listing.listing_url
                    or record.scope not in ('variant', 'route') or record.route_id not in (None, route_id)):
                return None
            if (path.startswith('route.') or path == 'costs.order.shipping') and record.scope != 'route':
                return None
        evidence.update((e.evidence_id, e) for e in observation.evidence)
        accepted[path] = observation
        return observation.fact

    def fact(path):
        value = bound(path)
        return value.model_dump() if value else _UNKNOWN

    def known(path):
        value = bound(path)
        return value.value if value and value.state == 'known' else None

    def charge(kind):
        value = bound('costs.order.' + kind)
        if value and value.state == 'known':
            if kind == 'tax' and value.value == 'included':
                return dict(kind=kind, state='included', evidence_ids=value.evidence_ids)
            if kind == 'shipping' and isinstance(value.value, dict):
                return dict(kind=kind, state='additional', amount=value.value, evidence_ids=value.evidence_ids)
        return dict(kind=kind, state='unknown', reason='No unambiguous applicable charge on the listing')

    category = listing.category
    specs = {'category': category}
    for name, spec in _SPECS[category].model_fields.items():
        if name != 'category' and (spec.is_required() or 'variant.specs.' + name in fields):
            specs[name] = fact('variant.specs.' + name)
    manufacturer = known('variant.product.manufacturer')
    model = known('variant.product.model')
    if not current:
        manufacturer, model = manufacturer or listing.manufacturer, model or listing.model
    if not manufacturer or not model:
        raise ValueError('The selected listing does not establish model and manufacturer')
    source_key = f'{listing.source_id}:{listing.product_id}:{listing.sku}'
    for path in ('catalog.gtin', 'catalog.mpn', 'catalog.configuration_id', 'variant.color'):
        bound(path)
    exact_key = exact_variant_key(category, accepted)
    product_id = (f'{category}:{manufacturer.casefold()}:{model.casefold()}' if exact_key
                  else f'{listing.source_id}:{listing.product_id}')
    variant_id = exact_key or source_key
    # The direct-route contract requires a country. ZZ is the ISO user-assigned
    # unknown territory. A positive domestic dispatch still needs its own receipt.
    country = known('route.origin_country') or 'ZZ'
    return v2.Offer(
        offer_id=source_key, retailer_id=listing.retailer_id, source_id=listing.source_id,
        source_product_id=listing.product_id, source_variant_id=listing.sku,
        seller=dict(seller_id=listing.seller_id, name=listing.seller_name, market='LB'),
        listing_url=listing.listing_url,
        variant=dict(variant_id=variant_id, product=dict(product_id=product_id, category=category,
            manufacturer=manufacturer, model=model), specs=specs, color=fact('variant.color')),
        route=dict(kind='direct', route_id=route_id, origin_country=country, destination=destination,
                   item_eligible=fact('route.item_eligible')),
        price=fact('price'), stock=fact('stock'), purchasable=fact('purchasable'), condition=fact('condition'),
        costs=tuple(charge(kind) for kind in ('tax', 'shipping')), evidence=tuple(evidence.values()))


def _text(value):
    if isinstance(value, dict):
        value = value.get('name')
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 256 else None


def _page_listings(outcome, record, category):
    """Select the product described by this URL, never a recommendation embedded alongside it."""
    parser = _HTML(None)
    parser.feed(outcome.body.decode('utf-8'))
    if len(parser.scripts) > 50:
        return ()
    found = {}
    for script in parser.scripts:
        try:
            block = json.loads(script, parse_float=Decimal, object_pairs_hook=_unique_pairs)
            products = [p for _, p in _objects(block) if p.get('@type') == 'Product']
        except (ValueError, RecursionError):
            continue
        for product in products:
            urls = [value for field in ('url', '@id') if isinstance(value := product.get(field), str)]
            if not urls or any(urldefrag(u).url not in (outcome.origin_url, outcome.final_url) for u in urls):
                continue
            stated = _text(product.get('category')) or ''
            if {key for key, pattern in CATEGORY_WORDS.items() if pattern.search(stated)} != {category}:
                continue
            sku = product.get('sku')
            maker = _text(product.get('brand', product.get('manufacturer')))
            model = _text(product.get('model')) or _text(product.get('name'))
            if type(sku) not in (str, int) or not 0 < len(str(sku).strip()) <= 128 or not maker or not model:
                continue
            sku = str(sku).strip()
            product_id = product.get('productID', sku)
            if type(product_id) not in (str, int) or not 0 < len(str(product_id).strip()) <= 128:
                continue
            item = _Listing(record.source_id, record.entry.retailer_id, record.seller_id, record.entry.name,
                            tuple(record.entry.aliases), outcome.origin_url, str(product_id).strip(),
                            sku, category, maker, model)
            if sku in found and found[sku] != item:
                return ()
            found[sku] = item
            if len(found) > 20:
                return ()
    return tuple(found.values())


class _ReadJob:
    """Check the controller's remaining time before every redirect, retry and managed read."""
    def __init__(self, job, room):
        self.job, self.room = job, room
        self.ledger, self.run_id = job.ledger, job.run_id

    def source_budget(self, *args, **kwargs):
        return DeadlineBudget(self.job.source_budget(*args, **kwargs), self.room)

    def reserve(self, kind, charge, seconds, **kwargs):
        if self.room() < seconds:
            raise BudgetDenied('Insufficient time for a listing read')
        return self.job.reserve(kind, charge, seconds, **kwargs)


class DomesticBuyerSources:
    def __init__(self, storage, gate, directory, *, fallback=None):
        self.storage, self.gate, self.directory, self.fallback = storage, gate, directory, fallback
        self.catalog = DomesticCatalog(storage, registry_path=directory.registry_path)

    def lookup(self, request, *, limit=20):
        intent = request.intent
        text = ' '.join(intent.model.split()[:20])[:300] if isinstance(intent, v2.ModelIntent) else None
        items = self.catalog.lookup(category=request.requirements.category,
            text=text,
            listing_url=str(intent.url) if isinstance(intent, v2.LinkIntent) else None, limit=limit)
        return tuple(_offer(item, item.observations, request.fulfillment.destination)
                     for item in items if item.manufacturer and item.model)

    def _source(self, host):
        registered = [entry['id'] for entry in load_registry(self.directory.registry_path)['sources']
                      if urlsplit(entry['origin']).hostname == host]
        if len(registered) != 1:
            return None, None
        source_id, = registered
        with self.storage.engine.connect() as db:
            payloads = db.execute(select(directory_entries.c.payload).where(
                directory_entries.c.payload['source_id'].as_string() == source_id)).scalars().all()
        records = [DirectoryRecord.model_validate(payload) for payload in payloads]
        owners = [r for r in records if r.source_country == 'LB' and r.role == 'domestic'
                  and 'LB' in r.entry.markets and r.seller_identity == 'retailer'
                  and r.entry.discovery_status != 'retired'
                  and any(origin.host == host for origin in (r.entry.origin, *r.entry.related_origins))]
        return source_id, owners[0] if len(owners) == 1 else None

    def read(self, request, url, job, run, *, earlier=None, room):
        url = str(url)
        target = canonical(url)
        if target is None or (isinstance(request.intent, v2.LinkIntent) and str(request.intent.url) != url):
            return ReadResult(None, status='out_of_scope', reason='Invalid public listing URL')
        try:
            source_id, owner = self._source(target[1])
        except (OSError, ValueError, LookupError, SQLAlchemyError):
            return ReadResult(None, status='failed', reason='Reviewed source identity is unavailable')
        if source_id is None:
            return ReadResult(None, status='unconfigured', reason='No reviewed source for the shop')
        category = request.requirements.category
        if (owner is None or category not in owner.entry.categories or
                (earlier and (earlier.source_id != source_id or str(earlier.listing_url) != url))):
            return ReadResult(source_id, status='out_of_scope', reason='No applicable domestic retailer identity')
        if run.run_id != job.run_id or (run.request_id, run.request_revision) != (request.request_id, request.revision):
            raise ValueError('The read must belong to this buyer run and request')
        try:
            policy = self.gate.admitted_policy(source_id)
            refs = set(owner.capabilities)
            if owner.entry.capability:
                refs.add(owner.entry.capability)
            product = next((c for c in policy.endpoint_capabilities if c.expect == 'html'
                            and c.operation in ('product', 'variant') and c.matches(url)
                            and (not refs or CapabilityRef(capability_id=c.capability_id, version=c.version) in refs)), None)
            capability = CapabilityRef(capability_id=product.capability_id, version=product.version) if product else None
            self.gate.check_scope(source_id, url, expect='html', capability=capability)
        except (ValueError, PermissionError):
            return ReadResult(source_id, status='out_of_scope', reason='Listing URL is outside the active source scope')
        if room() < CALL_TIMEOUT_S or job.cancelled:
            return ReadResult(source_id, status='refused', reason='Listing read exceeded the remaining run allowance')

        selected = self.catalog.lookup(category=category, listing_url=url, limit=20)
        bounded = _ReadJob(job, room)
        replies = frozenset()
        try:
            outcome = retrieve_page(self.gate, bounded, source_id, url, capability=capability, fallback=self.fallback)
            if outcome.status != 'ok':
                refused = any(e.code in ('BUDGET_EXHAUSTED', 'RATE_LIMIT', 'BLOCKED', 'SOURCE_DISABLED')
                              for e in outcome.errors)
                return ReadResult(source_id, status='refused' if refused else 'failed', reason='Listing read unavailable')
            replies = frozenset({(outcome.response_sha256, outcome.observed_at)})
            work = bounded.reserve('work', Charge(Decimal(0)), 5)
            started = time.monotonic()
            work.dispatch()
            try:
                listings = selected or _page_listings(outcome, owner, category)
                offers = []
                for item in listings:
                    if (item.source_id, item.seller_id, item.retailer_id) != (
                            source_id, owner.seller_id, owner.entry.retailer_id):
                        continue
                    if earlier and (item.product_id, item.sku) != (earlier.source_product_id, earlier.source_variant_id):
                        continue
                    capture = _capture(outcome, item, _route_id(request.fulfillment.destination))
                    extraction = extract_listing(capture, item, run=run, destination=request.fulfillment.destination)
                    fields = {o.evidence[0].field_path: o for o in extraction.observations}
                    if any(name not in fields or fields[name].fact.state != 'known' for name in (
                            'variant.product.manufacturer', 'variant.product.model')):
                        continue
                    offer = _offer(item, fields, request.fulfillment.destination, current=True)
                    if earlier and self._changed_variant(earlier, offer):
                        continue
                    if all(e.run == run and (e.response_sha256, e.observed_at) in replies for e in offer.evidence):
                        offers.append(offer)
            finally:
                work.complete(Decimal(0), time.monotonic() - started)
        except BudgetDenied:
            job.ledger.recover()
            return ReadResult(source_id, replies=replies, status='refused', reason='Listing read exceeded the available budget')
        except (ValueError, ArithmeticError, RecursionError, httpx2.HTTPError, TimeoutError):
            return ReadResult(source_id, replies=replies, status='failed',
                              reason='The listing could not be read for the selected product')
        if not offers:
            return ReadResult(source_id, replies=replies, status='failed', reason='No matching SKU on the current listing')
        if isinstance(request.intent, v2.LinkIntent) and earlier is None and len(offers) > 1:
            return ReadResult(source_id, replies=replies, status='failed',
                              reason='The product link does not select one of the available SKUs')
        offers, dispatch_replies = self._dispatch(selected, offers, bounded, run, room)
        return ReadResult(source_id, tuple(offers), replies | dispatch_replies, dispatch_replies=dispatch_replies)

    def _dispatch(self, listings, offers, bounded, run, room):
        """One additional exact, approved collection read can establish dispatch for its individual members."""
        targets = {}
        pages = set()
        for item in listings:
            observation = item.observations.get('route.origin_country')
            page = _membership_url(observation, item)
            for index, offer in enumerate(offers):
                if (page and offer.route.origin_country == 'ZZ'
                        and not any(r.field_path == 'route.origin_country' for r in offer.evidence)
                        and (offer.source_product_id, offer.source_variant_id) == (item.product_id, item.sku)):
                    targets[index] = item
                    pages.add(page)
        if len(pages) != 1 or room() < CALL_TIMEOUT_S:
            return offers, frozenset()
        page, = pages
        source_id = next(iter(targets.values())).source_id
        try:
            # A membership page is separately reviewed as an exact endpoint. A
            # product capability never authorizes following arbitrary collection links.
            self.gate.check_scope(source_id, page, expect='html')
            outcome = retrieve_page(self.gate, bounded, source_id, page, fallback=self.fallback)
            if outcome.status != 'ok' or outcome.final_url != page:
                return offers, frozenset()
            reply = outcome.response_sha256, outcome.observed_at
            confirmed = set()
            updated = offers.copy()
            work = bounded.reserve('work', Charge(Decimal(0)), 5)
            started = time.monotonic()
            work.dispatch()
            try:
                for index, item in targets.items():
                    offer = offers[index]
                    capture = _capture(outcome, item, offer.route.route_id)
                    result = extract_dispatch_membership(capture, item, run=run)
                    for observation in result.observations:
                        if _membership_url(observation, item) != page or any(
                                r.run != run or r.route_id != offer.route.route_id
                                or (r.response_sha256, r.observed_at) != reply for r in observation.evidence):
                            continue
                        data = offer.model_dump()
                        data['route']['origin_country'] = 'LB'
                        data['evidence'] += tuple(r.model_dump() for r in observation.evidence)
                        updated[index] = v2.Offer.model_validate(data)
                        confirmed.add(reply)
            finally:
                work.complete(Decimal(0), time.monotonic() - started)
            return updated, frozenset(confirmed)
        except BudgetDenied:
            bounded.ledger.recover()
        except (PermissionError, ValueError, httpx2.HTTPError, TimeoutError):
            pass
        return offers, frozenset()

    @staticmethod
    def _changed_variant(previous, current):
        """An unchanged shop SKU cannot override a newly conflicting model or configuration."""
        first, second = previous.variant, current.variant
        if first.variant_id != second.variant_id and any(
                marker in first.variant_id and marker in second.variant_id for marker in (':gtin:', ':mpn:', ':configuration:')):
            return True
        if (first.product.manufacturer.casefold(), first.product.model.casefold()) != (
                second.product.manufacturer.casefold(), second.product.model.casefold()):
            return True
        for name in ('color', *type(first.specs).model_fields):
            if name == 'category':
                continue
            left = first.color if name == 'color' else getattr(first.specs, name)
            right = second.color if name == 'color' else getattr(second.specs, name, None)
            if left and right and left.state == right.state == 'known' and left.value != right.value:
                return True
        return False
