"""Collect a bounded, resumable public corpus before the first buyer search."""
import argparse
from collections import Counter
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
from app.embeddings import EmbeddingService, OpenAIEmbeddings
from app.extraction import PageProfile, extract_page
from app.source_capabilities import CapabilityRef
from app.source_registry import get_active_source
from app.sources import MAX_RESPONSE_BYTES, SourceGate
from app.storage import Storage


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
    # Category slugs reviewed for this source. Search text alone is insufficient.
    category_slugs: tuple[Text, ...] = ()


class Manifest(Model):
    manifest_id: Text
    processing: ProcessingVersion
    limits: CollectionLimits
    international_limits: CollectionLimits | None = None
    seeds: tuple[Seed, ...] = Field(min_length=1, max_length=3000)

    @model_validator(mode='after')
    def check_targets(self):
        counts = Counter()
        seen = set()
        for seed in self.seeds:
            if (seed.source_id, str(seed.url)) in seen:
                raise ValueError('Duplicate seed URL')
            seen.add((seed.source_id, str(seed.url)))
            if seed.format == 'catalog' and (not seed.category_slugs or seed.content != 'listing'):
                raise ValueError('Catalog seeds need reviewed categories')
            if seed.source_id in INTERNATIONAL:
                if seed.format=='catalog':
                    raise ValueError('International listing allocations require individual product URLs')
                if self.international_limits is None:
                    raise ValueError('International targets require a nested allowance')
                counts[seed.source_id if seed.content == 'listing' else
                       'policy' if seed.content == 'policy' else 'support'] += 1
        if self.international_limits and any(
                getattr(self.international_limits, key) > getattr(self.limits, key)
                for key in CollectionLimits.model_fields):
            raise ValueError('International limits exceed the overall manifest')
        if any(counts[k] > v for k,v in {**INTERNATIONAL,'policy':30,'support':100}.items()):
            raise ValueError('International seed allocation exceeded')
        return self


INTERNATIONAL = {'microless':300, 'bh':300, 'amazon':100}
DOMESTIC = {'mojitech','beytech','class_cellulars','pcandparts','ayoub_computers','abed_tahan'}


class SeedBudget:
    """Reserve every retry and redirect before dispatch, including after a restart."""
    def __init__(self, job, manifest, seed, token, registry):
        self.job, self.run_id, self.registry = job, job.run_id, registry
        self.manifest, self.seed, self.token = manifest, seed, token
        self.foreign = seed.source_id in INTERNATIONAL

    def reserve(self, kind, charge, seconds, **kwargs):
        is_fetch = kind == 'retrieval'
        with self.job.ledger._transaction() as (state, now):
            record = state['bootstrap_manifests'][self.run_id]
            if record.get('owner') != self.token or now >= record['lease_until']:
                raise BudgetDenied('Bootstrap lease expired')
            allocations = [('all', self.manifest.limits)]
            if self.foreign:
                allocations.append(('international', self.manifest.international_limits))
            for scope, limits in allocations:
                used = record[scope]
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
                if self.foreign:
                    bucket = (self.seed.source_id if self.seed.content=='listing' else
                              'policy' if self.seed.content=='policy' else 'support')
                    allocation = state.setdefault('international_seed_urls',{}).setdefault(bucket,{})
                    caps = {**INTERNATIONAL,'policy':30,'support':100}
                    if key not in allocation:
                        if len(allocation) >= caps[bucket]:
                            raise BudgetDenied('International seed URL ceiling reached')
                        if self.seed.content=='listing':
                            share = 35 if self.seed.category in {'phone','laptop'} else 10
                            if sum(c==self.seed.category for c in allocation.values()) >= caps[bucket]*share//100:
                                raise BudgetDenied('International seed category allocation exhausted')
                        allocation[key] = self.seed.category
                tries = record['tries'].get(key,0)
                if tries >= 1 + min(l.max_retries_per_resource for _,l in allocations):
                    raise BudgetDenied('Resource attempt ceiling reached')
                record['tries'][key] = tries + 1
        return self.job.reserve(kind, charge, seconds, **kwargs)

    def reserve_call(self, source_id, max_seconds):
        policy = SourcePolicy.model_validate(get_active_source(source_id,self.registry))
        limits = policy.limits.model_copy(update={'concurrency':1,'requests_per_minute':min(
            policy.limits.requests_per_minute, self.manifest.limits.max_requests_per_source_minute,
            self.manifest.international_limits.max_requests_per_source_minute if self.foreign else 6)})
        try:
            return SourceAttempt(self.reserve('retrieval',Charge(Decimal(0)),max_seconds,
                source=source_id,limits=limits,international=self.foreign))
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
            origin='bootstrap',corpus_version=capture.processing.corpus_version,json_pointer=locator,excerpt=excerpt)
        observations.append(FieldObservation(fact=dict(state='known',value=name,evidence_ids=[evidence.evidence_id]),
                                             evidence=(evidence,)))
        passages.append(PassageDraft(locator=locator,text=name,evidence=evidence))
    return Extraction(observations=tuple(observations),passages=tuple(passages))


def index_pending(corpus, document_id, embedder, rate, budget):
    service = EmbeddingService(corpus,embedder,usd_per_million=rate,job=budget,
                               international=budget.foreign)
    while service.index_document(document_id,owner_id=budget.run_id,
            max_spend_usd=budget.manifest.limits.max_spend_usd):
        pass


def collect(manifest, *, ledger, corpus, gate, embedder=None, rate=None):
    job = ledger.open_job('bootstrap:'+manifest.manifest_id,'bootstrap',
                          international=any(s.source_id in INTERNATIONAL for s in manifest.seeds))
    fingerprint = sha256(manifest.model_dump_json().encode()).hexdigest()
    token = uuid4().hex
    with ledger._transaction() as (state, now):
        empty = dict(fetch=0,paid=0,usd='0',ms=0,records=0,bytes=0)
        record = state.setdefault('bootstrap_manifests',{}).setdefault(job.run_id,dict(
            digest=fingerprint,all=empty.copy(),international=empty.copy(),tries={},pages={},lease_until=0))
        if record['digest'] != fingerprint or now < record['lease_until']:
            raise ValueError('Manifest changed or another worker holds the lease')
        record.update(owner=token,lease_until=now+120)
    try:
        for seed in manifest.seeds:
            if job.cancelled:
                break
            url = str(seed.url)
            key = ResourceKey(source_id=seed.source_id,url=url,representation=seed.format,
                              product_id=seed.product_id,variant_id=seed.sku)
            recipe = sha256(seed.model_dump_json().encode()).hexdigest()[:16]
            processing = manifest.processing.model_copy(update={
                'extraction_version': f'{manifest.processing.extraction_version}/{recipe}'})
            previous = corpus.state(key)
            with ledger._transaction() as (state, now):
                record = state['bootstrap_manifests'][job.run_id]
                if record['owner'] != token:
                    raise BudgetDenied('Bootstrap lease lost')
                record['lease_until'] = now+120
                page = record['pages'].get(url,{})
                reusable = (page.get('status') in {'indexed','empty'} and previous and
                            previous.processing == processing and previous.raw_sha256 == page.get('sha256'))
                if not reusable:
                    allocations = [(record['all'],manifest.limits)]
                    if seed.source_id in INTERNATIONAL:
                        allocations.append((record['international'],manifest.international_limits))
                    bytes_left = min(l.max_response_bytes-u['bytes'] for u,l in allocations)
                    rows_left = min(l.max_records-u['records'] for u,l in allocations)
                    if rows_left <= 0 or bytes_left < MAX_RESPONSE_BYTES:
                        break
                    row_hold = min(100, rows_left) if seed.format == 'catalog' else 1
                    for usage, _ in allocations:
                        usage['records'] += row_hold
                        usage['bytes'] += MAX_RESPONSE_BYTES
            budget = SeedBudget(job,manifest,seed,token,gate._registry_path)
            if reusable:
                if embedder and previous.document_id:
                    index_pending(corpus, previous.document_id, embedder, rate, budget)
                continue
            result = dict(source=seed.source_id,category=seed.category,status='failed',
                          checked_at=datetime.now(timezone.utc).isoformat())
            size = rows = 0
            try:
                if seed.source_id not in DOMESTIC | INTERNATIONAL.keys():
                    raise ValueError('Unknown source market')
                policy = get_active_source(seed.source_id,gate._registry_path)
                outcome = gate.fetch(budget,seed.source_id,url,expect='json' if seed.format=='catalog' else 'html',
                                     capability=seed.capability)
                if outcome.errors:
                    result['errors'] = [e.code for e in outcome.errors]
                else:
                    size = len(outcome.body)
                    capture = DocumentCapture(key=key,body=outcome.body,relevant_content=outcome.body,
                        origin='bootstrap',checked_at=outcome.observed_at,observed_at=outcome.observed_at,
                        final_url=outcome.final_url,visibility='public',processing=processing)
                    if seed.format == 'catalog':
                        rows = len(_records(outcome,limit=100))
                        if rows > rows_left:
                            raise BudgetDenied('Catalog exceeds remaining record capacity')
                        extraction = catalog(capture,outcome,seed,rows_left)
                    else:
                        rows = 1
                        extraction = extract_page(capture,PageProfile(seed.source_id,policy['origin'],
                            sku=seed.sku,policy_element_id=seed.policy_element_id,policy_field=seed.policy_field))
                        if seed.content == 'policy':
                            passages = tuple(p for p in extraction.passages if p.text in seed.policy_statements)
                            extraction = Extraction(passages=passages)
                    receipt = corpus.ingest(capture,lambda _:extraction)
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
                for scope in (('all','international') if budget.foreign else ('all',)):
                    record[scope]['bytes'] -= MAX_RESPONSE_BYTES - size
                    record[scope]['records'] -= row_hold - min(row_hold, rows)
            if embedder and result['status']=='indexed':
                index_pending(corpus, receipt.document_id, embedder, rate, budget)
    finally:
        with ledger._transaction() as (state, now):
            record = state['bootstrap_manifests'][job.run_id]
            if record.get('owner') == token:
                record.update(owner=None,lease_until=0)
    return ledger.snapshot()['bootstrap_manifests'][job.run_id]


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--embedding-rate',type=Decimal,help='USD per million input tokens')
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_bytes())
    ledger, storage = Ledger.configured(), Storage()
    try:
        embed = OpenAIEmbeddings(os.environ['OPENAI_API_KEY']) if args.embedding_rate else None
        print(json.dumps(collect(manifest,ledger=ledger,corpus=Corpus(storage),gate=SourceGate(),
                                 embedder=embed,rate=args.embedding_rate),indent=2))
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    main()
