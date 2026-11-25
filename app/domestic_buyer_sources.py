"""Project seeded listing evidence into buyer offers and re-read reviewed product URLs.

A catalog observation can select the listing and SKU for a recheck. Only the newly
returned page supplies the fields and receipts of the resulting runtime offer.
"""
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import time
from typing import Literal
from urllib.parse import parse_qs, urldefrag, urlencode, urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import contracts_v2 as v2
from app.accounting import BudgetDenied, Charge
from app.adapters.woocommerce import read_page as read_woo_page
from app.adapters import beytech, itworks_audio, mojitech, technodel_audio
from app.domestic_search_plan import product_terms, retailer_query
from app.buyer_sources import DeadlineBudget
from app.corpus_contracts import DocumentCapture, ProcessingVersion, ResourceKey
from app.corpus import Corpus
from app.embeddings import EmbeddingAllowanceExceeded, EmbeddingService, EmbeddingUnavailable
from app.freshness import FreshnessContext
from app.retrieval import EvidenceQuery, EvidenceRetrieval, ROUTED_MODEL
from app.directory import DirectoryRecord
from app.domestic_catalog import DomesticCatalog, exact_variant_key, extract_listing
from app.domestic_retailer_profiles import HOSTS as SHOPIFY_VARIANT_STORES, extract_dispatch_membership
from app.domestic_pickup import select_route
from app.extraction import _HTML, _objects, _unique_pairs
from app.firecrawl import retrieve_page
from app.identity import changed_listing_configuration
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
    estimates: dict[str, dict[str, v2.EstimatedCost]] = field(default_factory=dict)


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
    bound('costs.order.shipping_range')
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


def _product_nodes(outcome):
    parser = _HTML(None)
    parser.feed(outcome.body.decode('utf-8'))
    if len(parser.scripts) > 50:
        return
    for script in parser.scripts:
        try:
            block = json.loads(script, parse_float=Decimal, object_pairs_hook=_unique_pairs)
            yield from (p for _, p in _objects(block) if p.get('@type') in ('Product', 'ProductGroup'))
        except (ValueError, RecursionError):
            continue


def _linked_skus(outcome, products):
    """Derive all current selections for the supplied link, independently of any seeded SKU."""
    urls = {outcome.origin_url, outcome.final_url}
    bare = not urlsplit(outcome.origin_url).query
    found = set()

    def identifies_page(ref):
        if not isinstance(ref, str):
            return False
        target = urldefrag(ref).url
        return target in urls or (bare and target.partition('?')[0] in urls)

    def collect(product):
        if not isinstance(product, dict):
            found.add(None)
            return
        offers = product.get('offers', ())
        offers = offers if isinstance(offers, list) else [offers]
        if outcome.source_id in SHOPIFY_VARIANT_STORES:
            matched = False
            for offer in offers:
                url = offer.get('url') if isinstance(offer, dict) else None
                if identifies_page(url):
                    key, _, value = urlsplit(url).query.partition('=')
                    found.add(value if key == 'variant' and value.isdecimal() else None)
                    matched = True
            if bare and not matched:
                found.add(None)
        else:
            sku = product.get('sku')
            found.add(str(sku).strip() if type(sku) in (str, int) and str(sku).strip() else None)
            found.update(str(offer['sku']) for offer in offers if isinstance(offer, dict)
                         and offer.get('sku') is not None and (bare or identifies_page(offer.get('url'))))

    for product in products:
        offers = product.get('offers', ())
        offers = offers if isinstance(offers, list) else [offers]
        refs = [product.get('url'), product.get('@id'),
                *(offer.get('url') for offer in offers if isinstance(offer, dict))]
        if not any(identifies_page(ref) for ref in refs):
            continue
        if product.get('@type') == 'ProductGroup':
            members = product.get('hasVariant')
            for member in members if isinstance(members, list) else [members]:
                collect(member)
        else:
            collect(product)
    return found


def _page_listings(outcome, record, category, products):
    """Select the product described by this URL, never a recommendation embedded alongside it."""
    found = {}
    for product in products:
        if product.get('@type') != 'Product':
            continue
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
    def __init__(self, storage, gate, directory, *, fallback=None, embedding=None):
        self.storage, self.gate, self.directory, self.fallback = storage, gate, directory, fallback
        self.catalog = DomesticCatalog(storage, registry_path=directory.registry_path)
        self.embedding = embedding

    def needs_lookup(self, request, job):
        """Rank catalog candidates by reusable scoped specification passages before current checks."""
        items = self.catalog.lookup(category=request.requirements.category, limit=20)
        corpus = Corpus(self.storage)
        retrieval = EvidenceRetrieval(corpus)
        route = self.embedding
        service = EmbeddingService(corpus, route.provider, usd_per_million=route.rate, job=job) if route and route.provider else None
        vector, ranked, semantic_hits = None, [], 0
        text = request.intent.description[:1000]
        for item in items:
            if not item.manufacturer or not item.model:
                continue
            fields = tuple(k for k in item.observations if k.startswith('variant.specs.'))
            hits = ()
            if fields and not job.cancelled:
                context = FreshnessContext(now=datetime.now(timezone.utc), product_id=item.product_id, variant_id=item.sku)
                query = EvidenceQuery(text=text, source_ids=(item.source_id,), scope='variant', field_paths=fields[:20],
                    context=context, corpus_version=item.processing.corpus_version, index_version=item.processing.index_version,
                    chunk_version=item.processing.chunk_version, limit=3)
                compatible = service and retrieval.has_routed_vectors(query)
                if compatible and vector is None:
                    try:
                        vector, = service.embed([text], owner_id=job.run_id, purpose='query',
                                                index_version=query.index_version, max_spend_usd=Decimal('.01'))
                    except (EmbeddingUnavailable, EmbeddingAllowanceExceeded, BudgetDenied):
                        service = None
                hits = tuple(hit for hit in retrieval.retrieve(query, vector=vector if compatible else None,
                             vector_model=ROUTED_MODEL) if hit.freshness.reusable)
                semantic_hits += sum('vector' in hit.channels for hit in hits)
            ranked.append((sum(hit.score for hit in hits), _offer(item, item.observations, request.fulfillment.destination)))
        return tuple(o for _, o in sorted(ranked, key=lambda entry: -entry[0])), semantic_hits

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

    def search_listings(self, request, job, *, room):
        """Search category-capable shops. Search results supply URLs only."""
        category = request.requirements.category
        term = retailer_query(request)
        if not term:
            return
        bounded = _ReadJob(job, room)
        routes = []
        for reader in (beytech.CATALOG, mojitech.CATALOG):
            if category in reader.categories:
                routes.append((reader.source_id, reader.search_url(term), 'json',
                               lambda body, r=reader: r.product_links(body, category)))
        if category == 'audio' and request.fulfillment.mode in ('any', 'delivery'):
            for reader in (itworks_audio,):
                url = reader.ORIGIN + '/search?' + urlencode({'q': term})
                routes.append((reader.SOURCE, url, 'html', lambda body, r=reader: r.product_links(body, term)))
        if category in technodel_audio.CATEGORIES.values() and request.fulfillment.mode != 'pickup':
            # The store searches its titles. Use a discriminating catalogue token for needs,
            # then rank the returned product slugs by all the original requirements.
            terms = product_terms(request)
            query = term if request.intent.kind == 'exact_model' else next(
                (t.split()[0] for t in terms if any(c.isdigit() for c in t)), term)
            rank = ' '.join([*terms, *request.allowed_colors])
            url = technodel_audio.ORIGIN + '/search?' + urlencode({'q': query})
            routes.insert(0, (technodel_audio.SOURCE, url, 'html',
                              lambda body: technodel_audio.listing_links(body, query, rank, category)))
        for source_id, url, expect, extract in routes:
            owner = self.directory.get(source_id)
            if (not owner or owner.source_id != source_id or owner.seller_identity != 'retailer'
                    or owner.source_country != 'LB' or owner.role != 'domestic'
                    or category not in owner.entry.categories or owner.entry.discovery_status == 'retired'):
                continue
            if job.cancelled or room() < CALL_TIMEOUT_S:
                break
            ref = CapabilityRef(capability_id=source_id + '-search', version='1')
            links, reason, attempts = (), None, 0
            try:
                result = self.gate.fetch(bounded.source_budget(self.directory.registry_path),
                                         source_id, url, expect=expect, capability=ref)
                attempts = result.attempts
                if result.status == 'ok' and result.final_url == url:
                    links = extract(result.body)
                    if not links:
                        reason = 'No matching product links in the bounded retailer search.'
                else:
                    reason = 'Retailer search unavailable: ' + ', '.join(e.code for e in result.errors)
            except (ValueError, TypeError, PermissionError, BudgetDenied, TimeoutError, httpx2.HTTPError):
                reason = 'The retailer search could not be read within its approved scope.'
            params = parse_qs(urlsplit(url).query)
            yield dict(source_id=source_id, query=(params.get('q') or params['search'])[0],
                       attempts=attempts, links=links, reason=reason)

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
            audio_reader = {itworks_audio.SOURCE: itworks_audio, technodel_audio.SOURCE: technodel_audio}.get(source_id)
            if audio_reader:
                if (audio_reader is itworks_audio and category != 'audio') or request.fulfillment.mode == 'pickup':
                    return ReadResult(source_id, status='out_of_scope', reason='No reviewed reader for the requested category or pickup route')
                policy = retrieve_page(self.gate, bounded, source_id, audio_reader.FAQ)
                if policy.status == 'ok':
                    replies |= {(policy.response_sha256, policy.observed_at)}
                try:
                    offer = audio_reader.read_offer(outcome, policy, request, owner, run,
                                                     _route_id(request.fulfillment.destination))
                except (KeyError, IndexError, ValueError, TypeError, AttributeError, InvalidOperation, RecursionError):
                    offer = None
                if offer is None:
                    return ReadResult(source_id, replies=replies, status='failed',
                                      reason='The current page did not establish an unambiguous product selection')
                return ReadResult(source_id, (offer,), replies)
            work = bounded.reserve('work', Charge(Decimal(0)), 5)
            started = time.monotonic()
            work.dispatch()
            try:
                is_link = isinstance(request.intent, v2.LinkIntent)
                products = tuple(_product_nodes(outcome)) if is_link or not selected else ()
                linked = _linked_skus(outcome, products) if is_link else None
                listings = selected or _page_listings(outcome, owner, category, products)
                offers, estimates = [], {}
                for item in listings:
                    if (is_link and not urlsplit(url).query and linked == {item.sku}
                            and b'variations_form' in outcome.body):
                        page, problem = read_woo_page(outcome, item.product_id)
                        choices = page.items or {}
                        if (problem or len(choices) != 1 or not all(
                                item.sku in (variant_id, entry.data.get('sku'))
                                for variant_id, entries in choices.items() for entry in entries)):
                            linked.add(None)
                    if linked is not None and linked != {item.sku}:
                        continue
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
                    offer = select_route(offer, capture, item, request.fulfillment)
                    if offer is None:
                        continue
                    if earlier and changed_listing_configuration(earlier, offer):
                        continue
                    if all(e.run == run and (e.response_sha256, e.observed_at) in replies for e in offer.evidence):
                        offers.append(offer)
                        quote = fields.get('costs.order.shipping_range')
                        if (quote and quote.fact.state == 'known' and isinstance(offer.route, v2.DirectRoute)
                                and all(e.scope == 'route' and e.route_id == offer.route.route_id
                                        and e in offer.evidence for e in quote.evidence)):
                            estimates[offer.offer_id] = {'costs.order.shipping': v2.EstimatedCost.model_validate(quote.fact.value)}
            finally:
                work.complete(Decimal(0), time.monotonic() - started)
        except BudgetDenied:
            job.ledger.recover()
            return ReadResult(source_id, replies=replies, status='refused', reason='Listing read exceeded the available budget')
        except (ValueError, ArithmeticError, RecursionError, httpx2.HTTPError, TimeoutError):
            return ReadResult(source_id, replies=replies, status='failed',
                              reason='The listing could not be read for the selected product')
        if is_link and (len(linked) != 1 or None in linked):
            return ReadResult(source_id, replies=replies, status='failed',
                              reason='The current product link does not identify one SKU. Select a variant on the retailer page.')
        if not offers:
            if request.fulfillment.mode == 'pickup':
                return ReadResult(source_id, replies=replies, status='failed',
                                  reason='No current stock evidence for this SKU at the requested pickup branch')
            return ReadResult(source_id, replies=replies, status='failed', reason='No matching SKU on the current listing')
        offers, dispatch_replies = self._dispatch(selected, offers, bounded, run, room)
        return ReadResult(source_id, tuple(offers), replies | dispatch_replies, dispatch_replies=dispatch_replies,
                          estimates=estimates)

    def _dispatch(self, listings, offers, bounded, run, room):
        """One additional exact, approved collection read can establish dispatch for its individual members."""
        targets = {}
        pages = set()
        for item in listings:
            observation = item.observations.get('route.origin_country')
            page = _membership_url(observation, item)
            for index, offer in enumerate(offers):
                if (page and isinstance(offer.route, v2.DirectRoute) and offer.route.origin_country == 'ZZ'
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
