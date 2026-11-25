"""Shared domestic search and page-read operations for research and collection jobs."""
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import os
import time
from typing import Literal, get_args
from urllib.parse import urlsplit

import httpx2
from sqlalchemy import func, or_, select

from app.accounting import BudgetDenied, Charge
from app.contracts import SourceLimits
from app.contracts_v2 import DirectRoute, VerifiedOffer
from app.directory import DirectoryRecord
from app.firecrawl import Firecrawl
from app.identity import conflicting_configurations
from app.page_extraction import read_page
from app.search_connectors import (
    BraveSearch, DirectoryPage, FirecrawlSearch, ManufacturerDirectories, SearchFailure,
    SerpApiSearch, TavilySearch,
)
from app.storage import directory_entries

Channel = Literal['tavily', 'serpapi', 'brave', 'manufacturer', 'firecrawl']
CHANNELS = get_args(Channel)
CONNECTOR_CONFIG = (
    ('tavily', TavilySearch, 'TAVILY_API_KEY', 'TAVILY_USD_PER_CREDIT'),
    ('serpapi', SerpApiSearch, 'SERPAPI_API_KEY', 'SERPAPI_USD_PER_SEARCH'),
    ('brave', BraveSearch, 'BRAVE_SEARCH_API_KEY', 'BRAVE_USD_PER_SEARCH'),
    ('firecrawl', FirecrawlSearch, 'FIRECRAWL_API_KEY', 'FIRECRAWL_SEARCH_USD_PER_CREDIT'),
)
DISCOVERY_ENV = tuple(key for _, _, credential, rate in CONNECTOR_CONFIG for key in (credential, rate)) + (
    'FIRECRAWL_SCRAPE_USD_PER_CREDIT', 'TECHSCOUT_MANUFACTURER_DIRECTORIES',
)
CHANNEL_LIMITS = SourceLimits(requests_per_minute=6, concurrency=1, max_requests_per_run=6)


@dataclass(frozen=True)
class SearchObjective:
    query: str
    objective: str
    channel: Channel = 'tavily'
    language: Literal['en', 'ar'] = 'en'
    limit: int = 10

    def __post_init__(self):
        if (not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 400
                or not isinstance(self.objective, str) or not self.objective.strip() or len(self.objective) > 500):
            raise ValueError('Supply a bounded search query and decision objective')
        if self.channel not in CHANNELS or self.language not in ('en', 'ar'):
            raise ValueError('Unknown discovery channel or language')
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise ValueError('Request 1–20 search results')


@dataclass(frozen=True)
class Attribution:
    channel: Channel
    request_id: str | None
    title: str
    snippet: str
    score: float | None
    source_url: str | None


@dataclass(frozen=True)
class SearchLead:
    url: str
    seller_key: str
    source_id: str | None
    attribution: tuple[Attribution, ...]


@dataclass(frozen=True)
class ChannelAttempt:
    channel: Channel
    status: Literal['ok', 'unavailable', 'timeout', 'rate_limited', 'budget_denied',
                    'refused', 'invalid_response', 'provider_error', 'cancelled']
    latency_ms: int = 0
    reserved_usd: Decimal = Decimal(0)
    actual_usd: Decimal | None = None
    usage_units: int | None = None
    request_id: str | None = None


def dispatch_receipts(offer, run, now):
    """Listing dispatch or a reviewed collection's exact listing, product and variant membership."""
    receipts = [e for e in offer.evidence if e.field_path == 'route.origin_country' and e.scope == 'route'
                and e.source_id == offer.source_id and e.route_id == offer.route.route_id
                and e.product_id == offer.source_product_id and e.variant_id == offer.source_variant_id
                and e.run == run and e.observed_at <= now]
    if any(e.conflicts for e in receipts):
        return ()
    listing = tuple(e for e in receipts if e.origin_url == offer.listing_url)
    if listing:
        return listing
    pages = defaultdict(list)
    listing_origin = urlsplit(str(offer.listing_url)).netloc
    for receipt in receipts:
        origin = urlsplit(str(receipt.origin_url))
        if origin.scheme == 'https' and origin.netloc == listing_origin:
            pages[(str(receipt.origin_url), receipt.response_sha256, receipt.observed_at)].append(receipt)
    required = {f'/collection/dispatch/{part}' for part in ('statement', 'listing', 'variant', 'product')}
    return next((group for group in pages.values() if required <= {e.json_pointer for e in group}), ())


@dataclass(frozen=True)
class SearchReport:
    run_id: str
    attempts: tuple[ChannelAttempt, ...]
    leads: tuple[SearchLead, ...]

    def _domestic_offers(self, offers, collisions):
        for item in offers:
            verified = VerifiedOffer.model_validate(item.model_dump(mode='json'))
            offer, run = verified.offer, verified.verification.run
            if (run.run_id != self.run_id or offer.seller.market != 'LB'
                    or not isinstance(offer.route, DirectRoute)):
                continue
            merchant = offer.domestic_merchant_receipts()
            if (merchant and all(e.run == run and e.observed_at <= verified.verification.checked_at for e in merchant)
                    or offer.route.origin_country == 'LB' and dispatch_receipts(offer, run, verified.verification.checked_at)):
                variant = (offer.variant.product.product_id, offer.variant.variant_id)
                if variant in collisions:
                    variant = (*variant, offer.offer_id)
                yield 'seller:' + offer.seller.seller_id, offer.source_id, str(offer.listing_url), variant

    def coverage(self, verified_offers, *, baseline=()):
        """Count incremental sellers and pairs against current-run verified baseline offers."""
        verified_offers, baseline = tuple(verified_offers), tuple(baseline)
        collisions = conflicting_configurations(v.offer for v in (*verified_offers, *baseline))
        sellers, variants = set(), defaultdict(set)
        for seller, _, _, variant in self._domestic_offers(baseline, collisions):
            sellers.add(seller)
            variants[variant].add(seller)
        eligible = defaultdict(set)
        for seller, source, url, variant in self._domestic_offers(verified_offers, collisions):
            eligible[(seller, source)].add((url, variant))
        output = {}
        for attempt in self.attempts:
            channel = attempt.channel
            new_sellers = new_comparisons = 0
            for lead in self.leads:
                if not any(a.channel == channel for a in lead.attribution):
                    continue
                for url, variant in eligible[(lead.seller_key, lead.source_id)]:
                    if lead.seller_key not in sellers:
                        sellers.add(lead.seller_key)
                        new_sellers += 1
                    # A directory can find the shop. A comparison additionally needs the listing.
                    if url == lead.url and lead.seller_key not in variants[variant]:
                        new_comparisons += len(variants[variant])
                        variants[variant].add(lead.seller_key)
            output[channel] = dict(additional_usable_sellers=new_sellers,
                                   new_same_variant_comparisons=new_comparisons)
        return output


def _failure(exc):
    if isinstance(exc, SearchFailure):
        return exc.status
    if isinstance(exc, BudgetDenied):
        return 'rate_limited' if str(exc) == 'Shared source workload limit' else 'budget_denied'
    if isinstance(exc, (TimeoutError, httpx2.TimeoutException)):
        return 'timeout'
    if isinstance(exc, httpx2.HTTPStatusError):
        if exc.response.status_code == 429:
            return 'rate_limited'
        if exc.response.status_code in (401, 403):
            return 'refused'
        return 'provider_error'
    if isinstance(exc, httpx2.HTTPError):
        return 'provider_error'
    return 'invalid_response'


class DomesticDiscovery:
    def __init__(self, gate, directory, connectors, *, fallback=None):
        self.gate, self.directory = gate, directory
        self.connectors, self.fallback = dict(connectors), fallback

    @classmethod
    def from_env(cls, gate, directory, *, environ=None, directory_defaults=()):
        env = os.environ if environ is None else environ
        connectors = {}
        for name, factory, key, rate in CONNECTOR_CONFIG:
            if env.get(key) and env.get(rate):
                try:
                    connectors[name] = factory.from_config(env[key], env[rate])
                except (ValueError, InvalidOperation):
                    continue  # This connector is unavailable; the other channels remain independent.
        pages = env.get('TECHSCOUT_MANUFACTURER_DIRECTORIES', json.dumps(directory_defaults) if directory_defaults else None)
        if pages:
            try:
                connectors['manufacturer'] = ManufacturerDirectories(
                    gate, directory, [DirectoryPage(**p) for p in json.loads(pages)])
            except (ValueError, TypeError, AttributeError):
                pass
        fallback = None
        if env.get('FIRECRAWL_API_KEY') and env.get('FIRECRAWL_SCRAPE_USD_PER_CREDIT'):
            try:
                fallback = Firecrawl(env['FIRECRAWL_API_KEY'], gate,
                                     usd_per_credit=Decimal(env['FIRECRAWL_SCRAPE_USD_PER_CREDIT']))
            except (ValueError, InvalidOperation):
                pass
        return cls(gate, directory, connectors, fallback=fallback)

    def read(self, job, key, profile, processing, **kwargs):
        """Read a known scoped page even when all discovery providers are offline."""
        return read_page(self.gate, job, key, profile, processing, fallback=self.fallback, **kwargs)

    def _attempt(self, search, job, cursor):
        channel = search.channel
        connector = self.connectors.get(channel)
        report = ChannelAttempt(channel, 'unavailable')
        if job.cancelled:
            return replace(report, status='cancelled'), ()
        if connector is None:
            return report, ()
        started = time.monotonic()
        lease = None
        sent = False
        try:
            if channel == 'manufacturer':
                # Each HTTP request/redirect is individually reserved by SourceGate.
                response = connector.fetch(search, job, cursor)
                report = replace(report, actual_usd=Decimal(0))
            else:
                quote = connector.quote(search)
                lease = job.reserve('search', Charge(quote), 20, source='discovery:' + channel, limits=CHANNEL_LIMITS)
                lease.dispatch()
                sent = True
                report = replace(report, reserved_usd=quote)
                response = connector.fetch(search)
                units = response.units
                if units is not None and (type(units) is not int or units < 0):
                    raise ValueError('Invalid provider usage')
                rid = response.request_id
                if isinstance(rid, str) and len(rid) <= 256:
                    report = replace(report, request_id=rid)
                lease.record(dict(channel=channel, request_id=report.request_id, usage_units=units,
                                  latency_ms=round((time.monotonic() - started) * 1000)))
                if units is None:
                    lease.uncertain()
                else:
                    actual = connector.rate * units
                    report = replace(report, actual_usd=actual, usage_units=units)
                    lease.complete(actual, time.monotonic() - started)
                lease = None
            rid = response.request_id
            if rid is not None and (not isinstance(rid, str) or len(rid) > 256):
                raise ValueError('Invalid provider request ID')
            if not response.valid_results:
                raise ValueError('Malformed provider result list')
            if job.cancelled:
                raise SearchFailure('cancelled')
            return replace(report, status='ok', request_id=rid,
                           latency_ms=round((time.monotonic() - started) * 1000)), response.hits
        except (SearchFailure, BudgetDenied, httpx2.HTTPError, TimeoutError, ValueError, KeyError,
                TypeError, AttributeError, RecursionError) as exc:
            if lease is not None:
                try:
                    if sent:
                        lease.uncertain()
                    else:
                        lease.release()
                except BudgetDenied:
                    job.ledger.recover()
            return replace(report, status=_failure(exc), latency_ms=round((time.monotonic() - started) * 1000)), ()

    def _identities(self, hosts):
        """Match exact reviewed origins, including aliases, without granting fetch scope."""
        if not hosts:
            return {}
        matching = defaultdict(set)
        roots = {f'https://{host}{slash}' for host in hosts for slash in ('', '/')}
        entry = directory_entries.c.payload['entry']
        related = entry['related_origins']
        with self.directory.storage.engine.connect() as db:
            if db.dialect.name == 'postgresql':
                aliases = func.json_array_elements_text(related).table_valued('value').render_derived()
            else:
                aliases = func.json_each(related).table_valued('value')
            has_alias = select(1).select_from(aliases).where(aliases.c.value.in_(roots)).exists()
            query = select(directory_entries.c.payload).where(or_(entry['origin'].as_string().in_(roots), has_alias))
            for raw in db.execute(query).scalars():
                record = DirectoryRecord.model_validate(raw)
                if (record.seller_identity == 'retailer' and record.source_country == 'LB'
                        and record.entry.discovery_status != 'retired' and record.source_id):
                    for origin in (record.entry.origin, *record.entry.related_origins):
                        if str(origin) in roots:
                            matching[origin.host].add(('seller:' + record.seller_id, record.source_id))
        return {host: next(iter(ids)) for host, ids in matching.items() if len(ids) == 1}

    def _run(self, search, job, channels, *, rotate=False, cursor=0):
        attempts, observations = [], defaultdict(list)
        sent_channels = 0
        for channel in channels:
            attempt, hits = self._attempt(replace(search, channel=channel), job, cursor)
            attempts.append(attempt)
            for hit in hits:
                origin = Attribution(channel, attempt.request_id, hit.title, hit.snippet, hit.score, hit.source_url)
                if origin not in observations[hit.url]:
                    observations[hit.url].append(origin)
            if attempt.status in ('cancelled', 'budget_denied'):
                break
            if not rotate:
                sent_channels += attempt.status != 'unavailable'
                if attempt.status == 'ok' or sent_channels >= 3:
                    break
        identities = self._identities({urlsplit(url).hostname for url in observations})
        leads = []
        for url, origins in observations.items():
            host = urlsplit(url).hostname
            key, source = identities.get(host, ('host:' + host, None))
            leads.append(SearchLead(url, key, source, tuple(origins)))
        return SearchReport(job.run_id, tuple(attempts), tuple(leads))

    def search(self, objective, job, *, alternatives=()):
        channels = tuple(dict.fromkeys((objective.channel, *alternatives)))
        if any(c not in CHANNELS for c in channels):
            raise ValueError('Unknown alternative discovery channel')
        return self._run(objective, job, channels)

    def rotate(self, objective, job, *, cursor=0, count=2):
        if type(cursor) is not int or cursor < 0 or type(count) is not int or not 1 <= count <= len(CHANNELS):
            raise ValueError('Rotation requires a nonnegative cursor and 1–5 channels')
        channels = [CHANNELS[(cursor + i) % len(CHANNELS)] for i in range(count)]
        return self._run(objective, job, channels, rotate=True, cursor=cursor // len(CHANNELS))

    def survey(self, objective, job, channels):
        """Measure each requested connector once, preserving attribution across the whole objective."""
        channels = tuple(channels)
        if not channels or len(set(channels)) != len(channels) or any(c not in CHANNELS for c in channels):
            raise ValueError('Select distinct discovery channels')
        return self._run(objective, job, channels, rotate=True)
