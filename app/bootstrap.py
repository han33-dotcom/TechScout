"""Collect a bounded, resumable public corpus before the first buyer search."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.accounting import BudgetDenied, Ledger, Charge, SourceAttempt
from app.adapters.woo_catalog import _records
from app.collection_contracts import Category, CollectionLimits, ContentKind
from app.contracts import HttpsUrl, Model, SourcePolicy, Text
from app.contracts_v2 import Evidence
from app.corpus import Corpus
from app.corpus_contracts import DocumentCapture, Extraction, FieldObservation, PassageDraft, ProcessingVersion, ResourceKey
from app.embeddings import (
    EmbeddingAllowanceExceeded, EmbeddingIndexBusy, EmbeddingRoute, EmbeddingService,
    EmbeddingUnavailable, RATE_VARIABLE,
)
from app.extraction import PageProfile, extract_page
from app.source_capabilities import CapabilityRef
from app.source_registry import get_active_source
from app.sources import MAX_RESPONSE_BYTES, SourceGate
from app.storage import Storage


class ListingSeed(Model):
    retailer_id: Text


class DispatchSeed(ListingSeed):
    listing_url: HttpsUrl


class Seed(Model):
    source_id: Text
    url: HttpsUrl
    category: Category
    content: ContentKind
    format: Literal['catalog', 'html']
    capability: CapabilityRef | None = None
    product_id: Text | None = None
    sku: Text | None = None
    policy_element_id: Text | None = None
    policy_statements: tuple[Text, ...] = ()
    policy_field: Literal['policy.shipping','policy.warranty','policy.returns','policy.payment'] = 'policy.shipping'
    # Preserve serialized digests of legacy manifests/checkpoints without a listing.
    listing: ListingSeed | None = Field(default=None, exclude_if=lambda value: value is None)
    dispatch_for: DispatchSeed | None = Field(default=None, exclude_if=lambda value: value is None)
    # Category slugs reviewed for this source. Search text alone is insufficient.
    category_slugs: tuple[Text, ...] = ()


class Manifest(Model):
    manifest_id: Text
    processing: ProcessingVersion
    limits: CollectionLimits
    # Keep the original serialized shape so domestic checkpoints retain their digest.
    international_limits: CollectionLimits | None = None
    seeds: tuple[Seed, ...] = Field(min_length=1, max_length=3000)

    @model_validator(mode='after')
    def check_targets(self):
        seen = set()
        for seed in self.seeds:
            if (seed.source_id, str(seed.url)) in seen:
                raise ValueError('Duplicate seed URL')
            seen.add((seed.source_id, str(seed.url)))
            if seed.dispatch_for and (seed.listing or seed.url==seed.dispatch_for.listing_url):
                raise ValueError('Dispatch membership needs a separate collection resource')
            if (seed.listing or seed.dispatch_for) and (seed.format != 'html' or seed.content != 'listing'
                    or not seed.product_id or not seed.sku or seed.source_id in RETIRED_IMPORT_SOURCES):
                raise ValueError('Domestic listing seeds require a domestic HTML product, SKU and retailer')
            if seed.format == 'catalog' and (not seed.category_slugs or seed.content != 'listing'):
                raise ValueError('Catalog seeds need reviewed categories')
        return self


RETIRED_IMPORT_SOURCES = {'microless', 'microless_uae', 'bh', 'bh_us', 'amazon', 'amazon_us',
                         'myus', 'stackry', 'shop_and_ship'}
DOMESTIC = {'mojitech','beytech','class_cellulars','pcandparts','ayoub_computers','abed_tahan'}


class SeedBudget:
    """Reserve every retry and redirect before dispatch, including after a restart."""
    def __init__(self, job, manifest, seed, token, registry, *, checkpoints='bootstrap_manifests'):
        self.job, self.run_id, self.registry = job, job.run_id, registry
        self.ledger = job.ledger
        self.manifest, self.seed, self.token = manifest, seed, token
        self.checkpoints = checkpoints

    @property
    def cancelled(self):
        return self.job.cancelled

    def reserve(self, kind, charge, seconds, **kwargs):
        is_fetch = kind == 'retrieval'
        with self.job.ledger._transaction() as (state, now):
            record = state[self.checkpoints][self.run_id]
            if record.get('owner') != self.token or now >= record['lease_until']:
                raise BudgetDenied('Collection lease expired')
            if 'deadline' in record and now + seconds > record['deadline']:
                raise BudgetDenied('Collection deadline reached')
            used, limits = record['all'], self.manifest.limits
            if (used['fetch'] + is_fetch > limits.max_fetch_attempts
                    or used['paid'] + int(charge.total > 0) > limits.max_paid_calls
                    or Decimal(used['usd']) + charge.total > limits.max_spend_usd
                    or used['ms'] + int(seconds * 1000) > limits.max_elapsed_ms):
                raise BudgetDenied('Manifest allowance exhausted')
            used['fetch'] += is_fetch
            used['paid'] += int(charge.total > 0)
            used['usd'] = str(Decimal(used['usd']) + charge.total)
            used['ms'] += int(seconds * 1000)
            if is_fetch:
                key = str(self.seed.url)
                tries = record['tries'].get(key,0)
                if tries >= 1 + limits.max_retries_per_resource:
                    raise BudgetDenied('Resource attempt ceiling reached')
                record['tries'][key] = tries + 1
        return self.job.reserve(kind, charge, seconds, **kwargs)

    def reserve_call(self, source_id, max_seconds):
        return self.reserve_resource(source_id, str(self.seed.url), max_seconds)

    def reserve_resource(self, source_id, url, max_seconds):
        policy = SourcePolicy.model_validate(get_active_source(source_id,self.registry))
        limits = policy.limits.model_copy(update={'concurrency':1,'requests_per_minute':min(
            policy.limits.requests_per_minute, self.manifest.limits.max_requests_per_source_minute, 6)})
        try:
            return SourceAttempt(self.reserve('retrieval',Charge(Decimal(0)),max_seconds,
                source=source_id,limits=limits,resource=url))
        except BudgetDenied:
            return None


def catalog(capture, outcome, seed, cap):
    """Index named products only after matching the source's category and product ID."""
    observations, passages, seen = [], [], set()
    for i, row in enumerate(_records(outcome, limit=min(100,cap))):
        categories = row.get('categories') or []
        if not any(isinstance(c,dict) and c.get('slug') in seed.category_slugs for c in categories):
            continue
        pid, name = row.get('id'), row.get('name')
        if type(pid) is not int or pid <= 0 or pid in seen or not isinstance(name,str) or not name.strip():
            continue
        if row.get('type') not in {'simple','variable'} or row.get('parent') != 0:
            continue
        # Preserve source spelling, including escaped Unicode and slashes.
        import re
        excerpt = next((m.group() for m in re.finditer(r'"name"\s*:\s*("(?:[^"\\]|\\.)*")',
            outcome.body.decode('utf-8')) if json.loads(m.group(1)) == name and len(m.group()) <= 500),None)
        if not excerpt:
            continue
        seen.add(pid)
        locator = f'/{i}/name'
        identity = [seed.source_id,str(seed.url),locator,outcome.response_sha256,
                    outcome.observed_at.isoformat(),capture.processing.model_dump()]
        evidence = Evidence(evidence_id=sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
            source_id=seed.source_id, product_id=str(pid), scope='product',field_path='variant.product.model',
            origin_url=seed.url,final_url=outcome.final_url,observed_at=outcome.observed_at,
            response_sha256=outcome.response_sha256,adapter_version=capture.processing.extraction_version,
            origin=capture.origin,corpus_version=capture.processing.corpus_version,json_pointer=locator,excerpt=excerpt)
        observations.append(FieldObservation(fact=dict(state='known',value=name,evidence_ids=[evidence.evidence_id]),
                                             evidence=(evidence,)))
        passages.append(PassageDraft(locator=locator,text=name,evidence=evidence))
    return Extraction(observations=tuple(observations),passages=tuple(passages))


def index_pending(corpus, document_id, embedder, rate, budget):
    service = EmbeddingService(corpus,embedder,usd_per_million=rate,job=budget)
    while service.index_document(document_id,owner_id=budget.run_id,
            max_spend_usd=budget.manifest.limits.max_spend_usd):
        pass


def seed_target(seed, corpus, registry):
    key = ResourceKey(source_id=seed.source_id, url=seed.url, representation=seed.format,
                      product_id=seed.product_id, variant_id=seed.sku)
    if not (seed.listing or seed.dispatch_for):
        return key, None
    from app.domestic_catalog import DomesticCatalog, dispatch_representation
    profile = DomesticCatalog(corpus.storage, registry_path=registry).seed_profile(seed)
    representation = dispatch_representation(profile) if seed.dispatch_for else profile.representation
    return key.model_copy(update={'representation': representation}), profile


def seed_processing(manifest, seed):
    recipe = sha256(seed.model_dump_json().encode()).hexdigest()[:16]
    return manifest.processing.model_copy(update={
        'extraction_version': f'{manifest.processing.extraction_version}/{recipe}'})


def extract_seed(capture, outcome, seed, profile, policy, rows_left):
    if seed.format == 'catalog':
        return catalog(capture, outcome, seed, rows_left)
    if seed.dispatch_for:
        from app.domestic_retailer_profiles import extract_dispatch_membership
        return extract_dispatch_membership(capture, profile)
    if profile:
        from app.domestic_catalog import extract_listing
        return extract_listing(capture, profile)
    extraction = extract_page(capture, PageProfile(seed.source_id, policy['origin'], sku=seed.sku,
                              policy_element_id=seed.policy_element_id, policy_field=seed.policy_field))
    if seed.content == 'policy':
        return Extraction(passages=tuple(p for p in extraction.passages if p.text in seed.policy_statements))
    return extraction


def collect(manifest, *, ledger, corpus, gate, embedder=None, rate=None, embedding_gap=None):
    if any(seed.source_id in RETIRED_IMPORT_SOURCES for seed in manifest.seeds):
        raise ValueError('International retailer collection is retired')
    busy_gap = None

    def index(document_id, budget):
        nonlocal embedding_gap, busy_gap
        if embedder and document_id and embedding_gap is None:
            try:
                index_pending(corpus, document_id, embedder, rate, budget)
            except EmbeddingIndexBusy as exc:
                busy_gap = str(exc)
            except (BudgetDenied, EmbeddingAllowanceExceeded) as exc:
                embedding_gap = str(exc)
                raise
            except EmbeddingUnavailable as exc:
                # Finish structured/keyword ingestion. Retrying this manifest later
                # will index the pending passages without another source read.
                embedding_gap = str(exc)

    job = ledger.open_job('bootstrap:'+manifest.manifest_id,'bootstrap')
    fingerprint = sha256(manifest.model_dump_json().encode()).hexdigest()
    token = uuid4().hex
    with ledger._transaction() as (state, now):
        empty = dict(fetch=0,paid=0,usd='0',ms=0,records=0,bytes=0)
        record = state.setdefault('bootstrap_manifests',{}).setdefault(job.run_id,dict(
            digest=fingerprint,all=empty.copy(),international=empty.copy(),tries={},pages={},lease_until=0))
        if record['digest'] != fingerprint or now < record['lease_until']:
            raise ValueError('Manifest changed or another worker holds the lease')
        record.pop('stop_reason', None)
        record.update(owner=token,lease_until=now+120)
    try:
        for seed in manifest.seeds:
            if job.cancelled:
                break
            url = str(seed.url)
            profile_error = None
            try:
                key, profile = seed_target(seed, corpus, gate._registry_path)
                previous = corpus.state(key)
            except (ValueError, PermissionError) as exc:
                profile_error = exc
                previous = None
            processing = seed_processing(manifest, seed)
            with ledger._transaction() as (state, now):
                record = state['bootstrap_manifests'][job.run_id]
                if record['owner'] != token:
                    raise BudgetDenied('Bootstrap lease lost')
                record['lease_until'] = now+120
                page = record['pages'].get(url,{})
                reusable = (profile_error is None and page.get('status') in {'indexed','empty'} and previous and
                            previous.processing == processing and previous.raw_sha256 == page.get('sha256'))
                if not reusable:
                    usage = record['all']
                    bytes_left = manifest.limits.max_response_bytes - usage['bytes']
                    rows_left = manifest.limits.max_records - usage['records']
                    if rows_left <= 0 or bytes_left < MAX_RESPONSE_BYTES:
                        break
                    row_hold = min(100, rows_left) if seed.format == 'catalog' else 1
                    usage['records'] += row_hold
                    usage['bytes'] += MAX_RESPONSE_BYTES
            budget = SeedBudget(job,manifest,seed,token,gate._registry_path)
            if reusable:
                index(previous.document_id, budget)
                continue
            result = dict(source=seed.source_id,category=seed.category,status='failed',
                          checked_at=datetime.now(timezone.utc).isoformat())
            size = rows = 0
            try:
                if profile_error:
                    raise profile_error
                if not profile and seed.source_id not in DOMESTIC:
                    raise ValueError('Unknown source market')
                policy = get_active_source(seed.source_id,gate._registry_path)
                outcome = gate.fetch(budget,seed.source_id,url,expect='json' if seed.format=='catalog' else 'html',
                                     capability=seed.capability or (profile.capability if seed.listing and profile else None))
                if outcome.errors:
                    result['errors'] = [e.code for e in outcome.errors]
                else:
                    size = len(outcome.body)
                    result['checked_at'] = outcome.observed_at.isoformat()
                    capture = DocumentCapture(key=key,body=outcome.body,relevant_content=outcome.body,
                        origin='bootstrap',checked_at=outcome.observed_at,observed_at=outcome.observed_at,
                        final_url=outcome.final_url,visibility='public',processing=processing,
                        etag=outcome.validators.etag if outcome.validators else None,
                        last_modified=outcome.validators.last_modified if outcome.validators else None)
                    if seed.format == 'catalog':
                        rows = len(_records(outcome,limit=100))
                        if rows > rows_left:
                            raise BudgetDenied('Catalog exceeds remaining record capacity')
                    else:
                        rows = 1
                    receipt = corpus.ingest(capture, lambda page: extract_seed(page, outcome, seed, profile, policy, rows_left))
                    stored_fields = len(corpus.observations(receipt.document_id))
                    stored_passages = len(corpus.passages(receipt.document_id))
                    result.update(status='indexed' if stored_fields or stored_passages else 'empty',
                                  passages=stored_passages,
                                  sha256=outcome.response_sha256,fields=stored_fields)
            except (ValueError,PermissionError,BudgetDenied) as exc:
                result['error'] = str(exc)[:200]
            with ledger._transaction() as (state, now):
                record = state['bootstrap_manifests'][job.run_id]
                if record['owner'] != token:
                    raise BudgetDenied('Bootstrap lease lost')
                record['pages'][url] = result
                record['all']['bytes'] -= MAX_RESPONSE_BYTES - size
                record['all']['records'] -= row_hold - min(row_hold, rows)
            if result['status']=='indexed':
                index(receipt.document_id, budget)
    except (BudgetDenied, EmbeddingAllowanceExceeded) as exc:
        with ledger._transaction() as (state, now):
            record = state['bootstrap_manifests'][job.run_id]
            if record.get('owner') != token:
                raise
            record['stop_reason'] = str(exc)
    finally:
        with ledger._transaction() as (state, now):
            record = state['bootstrap_manifests'][job.run_id]
            if record.get('owner') == token:
                record.update(owner=None,lease_until=0)
    report = ledger.record('bootstrap_manifests', job.run_id)
    if embedding_gap or busy_gap or not embedder:
        report['semantic_gap'] = embedding_gap or busy_gap or 'embeddings not configured'
    return report


def main():
    from app.inference import load_env_file
    load_env_file(names=('OPENROUTER_API_KEY', RATE_VARIABLE))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--embedding-rate',type=Decimal,help='USD per million input tokens')
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_bytes())
    ledger, storage = Ledger.configured(), Storage()
    try:
        route = EmbeddingRoute.configured(rate=args.embedding_rate)
        print(json.dumps(collect(manifest,ledger=ledger,corpus=Corpus(storage),gate=SourceGate(),
                                 embedder=route.provider,rate=route.rate,embedding_gap=route.unavailable),indent=2))
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    main()
