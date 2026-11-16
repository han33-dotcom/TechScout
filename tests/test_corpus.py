"""Exercise the corpus at its storage entry points on SQLite and PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

from pydantic import ValidationError
from sqlalchemy import func, select, text

from app.contracts import SourcePolicy
from app.contracts_v2 import Evidence
from app.corpus import Corpus, RetentionPolicy
from app.corpus_contracts import DocumentCapture, Extraction, FieldObservation, PassageDraft, ResourceKey
from app.storage import (
    Storage, corpus_documents, corpus_embeddings, corpus_observations, corpus_passages, corpus_receipts,
)
from test_storage import migrate

URL = 'https://store.example/products/sample'
SOURCE = 'offline-catalog'
START = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
MODEL = 'text-embedding-3-small'


def serialized(value):
    return json.dumps(value, separators=(',', ':')).encode()


def unsupported(_):
    raise AssertionError('The extractor should not have been called')


def citation(capture, field, excerpt, *, suffix=''):
    fingerprint = sha256(capture.body).hexdigest()
    identity = (capture.key.model_dump(mode='json'), capture.observed_at.isoformat(),
                capture.processing.model_dump(mode='json'), field, suffix)
    return Evidence(
        evidence_id=sha256(serialized(identity)).hexdigest(), source_id=capture.key.source_id,
        product_id='book', variant_id='book-16', scope='variant', field_path=field,
        origin_url=capture.key.url, final_url=capture.final_url, observed_at=capture.observed_at,
        response_sha256=fingerprint, adapter_version=capture.processing.extraction_version,
        excerpt=excerpt, origin=capture.origin, corpus_version=capture.processing.corpus_version,
        index_version=capture.processing.index_version,
    )


def extract(capture):
    raw = json.loads(capture.body)
    price = citation(capture, 'price', f'"price":"{raw["price"]}"')
    ram = citation(capture, 'variant.specs.ram_gb', f'"ram":{raw["ram"]}')
    return Extraction(
        observations=(
            FieldObservation(fact={'state': 'known', 'value': {'amount': raw['price'], 'currency': 'USD'},
                                   'evidence_ids': [price.evidence_id]}, evidence=(price,)),
            FieldObservation(fact={'state': 'known', 'value': raw['ram'],
                                   'evidence_ids': [ram.evidence_id]}, evidence=(ram,)),
        ),
        passages=(PassageDraft(locator='specification', text=f'RAM: {raw["ram"]} GB', evidence=ram),),
    )


class CorpusTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.url = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{Path(directory) / 'corpus.db'}"
        migrate(self.url, 'base')
        migrate(self.url)
        self.storage = Storage(self.url, Path(directory) / 'evidence')
        self.addCleanup(migrate, self.url, 'base')
        self.addCleanup(self.storage.engine.dispose)
        self.storage.save_source(SourcePolicy.model_validate({
            'id': SOURCE, 'name': 'Offline store', 'origin': 'https://store.example',
            'status': 'active', 'reviewed_at': START.date(),
            'admission': {'basis': 'Synthetic', 'evidence': [URL], 'reviewed_at': START, 'scope': 'Read-only'},
            'allowed_endpoints': [URL],
            'limits': {'requests_per_minute': 6, 'concurrency': 1, 'max_requests_per_run': 4},
        }))
        self.corpus = Corpus(self.storage)
        self.key = ResourceKey(source_id=SOURCE, url=URL, representation='product-json',
                               product_id='book', variant_id='book-16')

    def capture(self, second=0, price='100.25', *, noise='a', processing=None, key=None,
                visibility='public', origin='bootstrap', etag='"v1"'):
        data = {'price': price, 'ram': 16}
        return DocumentCapture(
            key=key or self.key, final_url=URL, visibility=visibility, origin=origin,
            observed_at=START + timedelta(seconds=second), checked_at=START + timedelta(seconds=second + 1),
            body=serialized(data | {'banner': noise}), relevant_content=serialized(data),
            processing=processing or dict(corpus_version='corpus-1', index_version='index-1',
                                          extraction_version='extractor-1', chunk_version='chunk-1'),
            etag=etag, last_modified='Mon, 16 Nov 2026 12:00:00 GMT',
        )

    def count(self, table):
        with self.storage.engine.connect() as db:
            return db.execute(select(func.count()).select_from(table)).scalar_one()

    def fields(self, document_id):
        return {item.evidence[0].field_path: item for item in self.corpus.observations(document_id)}

    def test_exact_and_raw_only_changes_reuse_evidence_and_keep_timestamps(self):
        extractor = Mock(side_effect=extract)
        first = self.corpus.ingest(self.capture(), extractor)
        self.assertEqual(first.status, 'new')
        self.assertEqual(self.corpus.ingest(self.capture(2, origin='runtime'), unsupported).document_id,
                         first.document_id)
        newer = self.corpus.ingest(self.capture(4, noise='changed', origin='maintenance'), unsupported)
        self.assertEqual(newer.status, 'unchanged')
        self.assertNotEqual(newer.document_id, first.document_id)
        self.assertEqual(self.corpus.load_raw(first.document_id), self.capture().body)
        self.assertEqual(self.corpus.load_raw(newer.document_id), self.capture(noise='changed').body)
        self.assertEqual(self.corpus.observations(newer.document_id), self.corpus.observations(first.document_id))
        self.assertEqual(self.corpus.passages(newer.document_id), self.corpus.passages(first.document_id))
        state = self.corpus.state(self.key)
        self.assertEqual(state.validated_at, START + timedelta(seconds=4))
        self.assertEqual(state.content_changed_at, START)
        self.assertEqual(state.checked_at, START + timedelta(seconds=5))
        self.assertNotEqual(state.raw_sha256, state.relevant_sha256)
        self.assertEqual(self.count(corpus_documents), 2)
        self.assertEqual(self.count(corpus_observations), 2)
        self.assertEqual(extractor.call_count, 1)

    def test_price_change_reuses_specification_passage_and_embedding(self):
        first = self.corpus.ingest(self.capture(), extract).document_id
        passage, = self.corpus.passages(first)
        vector = [1.0] + [0.0] * 1535
        self.assertEqual(self.corpus.pending_embeddings(first, MODEL, 1536), (int(passage.passage_id),))
        for _ in range(2):
            self.corpus.save_embedding(int(passage.passage_id), 'index-1', MODEL, vector)
        later = self.corpus.ingest(self.capture(8, '124.35000001', origin='runtime'), extract)
        self.assertEqual(later.status, 'changed')
        self.assertEqual(self.corpus.passages(later.document_id), (passage,))
        price, spec = (self.fields(later.document_id)[field] for field in ('price', 'variant.specs.ram_gb'))
        self.assertEqual(price.fact.value['amount'], '124.35000001')
        self.assertEqual(price.evidence[0].observed_at, START + timedelta(seconds=8))
        self.assertEqual(spec.evidence[0].observed_at, START)
        self.assertEqual(self.fields(first)['price'].fact.value['amount'], '100.25')
        self.assertEqual(self.corpus.get_evidence(price.evidence[0].evidence_id), price.evidence[0])
        self.assertEqual(self.count(corpus_observations), 3)
        self.assertEqual(self.count(corpus_embeddings), 1)
        self.assertEqual(self.corpus.pending_embeddings(later.document_id, MODEL, 1536), ())
        with self.storage.engine.connect() as db:
            amount = db.execute(select(corpus_observations.c.price_amount)
                                .where(corpus_observations.c.observed_at == price.evidence[0].observed_at)
                                .where(corpus_observations.c.field_path == 'price')).scalar_one()
            self.assertEqual(amount, Decimal('124.35000001'))
            if db.dialect.name == 'postgresql':
                self.assertTrue(db.execute(text(
                    "SELECT to_tsvector('simple'::regconfig, text) @@ plainto_tsquery('simple','ram') "
                    "FROM corpus_passages")).scalar_one())
                self.assertEqual(db.execute(text(
                    "SELECT vector_dims(embedding), embedding <=> embedding FROM corpus_embeddings"
                )).one(), (1536, 0.0))
                definitions = {name: ddl for name, ddl in db.execute(text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname IN ('ix_corpus_embeddings_hnsw','ix_corpus_passages_fulltext')"
                ))}
                self.assertIn('USING hnsw', definitions['ix_corpus_embeddings_hnsw'])
                self.assertIn('USING gin', definitions['ix_corpus_passages_fulltext'])

    def test_check_failure_and_exact_304_never_retime_fact_observations(self):
        first = self.corpus.ingest(self.capture(), extract).document_id
        original = self.corpus.state(self.key)
        self.corpus.record_failed_check(self.key, START + timedelta(seconds=10))
        failed = self.corpus.state(self.key)
        self.assertEqual((failed.check_status, failed.validated_at), ('failed', START))
        self.assertTrue(self.corpus.record_not_modified(self.key, original, START + timedelta(seconds=20)))
        verified = self.corpus.state(self.key)
        self.assertEqual(verified.document_id, first)
        self.assertEqual(verified.validated_at, START + timedelta(seconds=20))
        self.assertEqual(verified.check_status, 'not_modified')
        self.assertEqual(self.fields(first)['price'].evidence[0].observed_at, START)

        self.corpus.ingest(self.capture(30, etag='"v2"'), unsupported)
        self.assertFalse(self.corpus.record_not_modified(self.key, original, START + timedelta(seconds=40)))
        key = self.key.model_copy(update={'representation': 'other-format'})
        self.assertFalse(self.corpus.record_not_modified(key, verified, START + timedelta(seconds=40)))
        self.assertIsNone(self.corpus.state(key))

    def test_extractor_and_chunk_versions_can_reprocess_without_freshness_invention(self):
        capture = self.capture()
        first = self.corpus.ingest(capture, extract).document_id
        processing = capture.processing.model_copy(update={
            'extraction_version': 'extractor-2', 'chunk_version': 'chunk-2', 'index_version': 'index-2',
        })
        second = self.corpus.ingest(self.capture(processing=processing), extract)
        self.assertEqual(second.status, 'unchanged')
        self.assertNotEqual(second.document_id, first)
        self.assertNotEqual(self.corpus.passages(first)[0].passage_id,
                            self.corpus.passages(second.document_id)[0].passage_id)
        self.assertEqual(self.fields(second.document_id)['price'].evidence[0].observed_at, START)
        self.assertEqual(self.corpus.state(self.key).content_changed_at, START)

    def test_conflicts_are_immutable_and_never_query_as_known_price(self):
        def conflict(capture):
            a = citation(capture, 'price', '"price":"100.25"')
            b = citation(capture, 'price', '"banner":"110.25"', suffix='b')
            values = [{'amount': amount, 'currency': 'USD'} for amount in ('100.25', '110.25')]
            return Extraction(observations=(FieldObservation(
                fact={'state': 'conflict', 'reason': 'Two advertised prices',
                      'alternatives': values, 'evidence_ids': [a.evidence_id, b.evidence_id]},
                evidence=(a, b),
            ),))

        first = self.corpus.ingest(self.capture(noise='110.25'), conflict).document_id
        later = self.corpus.ingest(self.capture(15, '120'), extract).document_id
        self.assertEqual(self.fields(first)['price'].fact.state, 'conflict')
        self.assertEqual(self.fields(later)['price'].fact.state, 'known')
        with self.storage.engine.connect() as db:
            unsafe_price = db.execute(select(corpus_observations.c.price_amount)
                                      .where(corpus_observations.c.fact_state == 'conflict')).scalar_one()
        self.assertIsNone(unsafe_price)

    def test_private_unbound_and_invalid_extraction_cannot_leave_known_facts(self):
        with self.assertRaisesRegex(ValueError, 'Private'):
            self.corpus.ingest(self.capture(visibility='private'), unsupported)
        self.assertEqual(tuple(self.corpus.snapshots.digests()), ())
        with self.assertRaisesRegex(ValidationError, 'Invalid structured fact'):
            self.corpus.ingest(self.capture(price='unknown'), extract)

        def forged(capture):
            receipt = citation(capture, 'price', 'invented receipt')
            return Extraction(observations=(FieldObservation(
                fact={'state': 'known', 'value': {'amount': '100', 'currency': 'USD'},
                      'evidence_ids': [receipt.evidence_id]}, evidence=(receipt,),
            ),))

        with self.assertRaisesRegex(ValueError, 'original excerpt'):
            self.corpus.ingest(self.capture(), forged)
        self.assertIsNone(self.corpus.state(self.key))
        self.assertEqual(self.count(corpus_documents), 0)
        self.assertEqual(self.count(corpus_observations), 0)
        self.corpus.prune(now=START)
        self.assertEqual(tuple(self.corpus.snapshots.digests()), ())
        self.assertEqual(self.corpus.ingest(self.capture(), extract).status, 'new')

    def test_older_parallel_capture_cannot_overwrite_newer_head(self):
        inside = Event()
        release = Event()

        def blocked(capture):
            inside.set()
            if not release.wait(5):
                raise AssertionError('Concurrent fixture did not release the extractor')
            return extract(capture)

        second_storage = Storage(self.url, self.storage.snapshots.root)
        self.addCleanup(second_storage.engine.dispose)
        other = Corpus(second_storage)
        with ThreadPoolExecutor(max_workers=2) as workers:
            newer = workers.submit(self.corpus.ingest, self.capture(20, '125'), blocked)
            try:
                self.assertTrue(inside.wait(5))
                older = workers.submit(other.ingest, self.capture(5, '90'), unsupported)
            finally:
                release.set()
            self.assertEqual(newer.result(timeout=10).status, 'new')
            self.assertEqual(older.result(timeout=10).status, 'stale')
        self.assertEqual(self.count(corpus_documents), 1)
        self.assertEqual(self.fields(self.corpus.state(self.key).document_id)['price'].fact.value['amount'], '125')

    def test_retention_preserves_shared_bytes_and_deletes_expired_indexes(self):
        capture = self.capture()
        first = self.corpus.ingest(capture, extract).document_id
        passage = self.corpus.passages(first)[0]
        self.corpus.save_embedding(int(passage.passage_id), 'index-1', MODEL, [1.0, 2.0])
        other = self.key.model_copy(update={'representation': 'also-public'})
        shared = self.corpus.ingest(self.capture(2, key=other), extract).document_id
        legacy = self.storage.save_snapshot(capture.body, 'application/json')

        self.corpus.delete_resource(other)
        self.assertEqual(self.corpus.load_raw(first), capture.body)
        with self.assertRaises(KeyError):
            self.corpus.load_raw(shared)
        policy = RetentionPolicy(raw_age=timedelta(days=1), content_age=timedelta(days=2))
        self.corpus.prune(now=START + timedelta(days=1, seconds=1), policy=policy)
        with self.assertRaises(KeyError):
            self.corpus.load_raw(first)
        self.assertEqual(tuple(self.corpus.snapshots.digests()), ())
        self.assertEqual(self.storage.load_snapshot(legacy), capture.body)
        self.assertEqual(self.corpus.get_evidence(passage.evidence.evidence_id), passage.evidence)
        self.assertEqual(self.count(corpus_embeddings), 1)

        self.corpus.prune(now=START + timedelta(days=2, seconds=1), policy=policy)
        self.assertEqual(self.count(corpus_passages), 0)
        self.assertEqual(self.count(corpus_receipts), 0)
        self.assertEqual(self.count(corpus_embeddings), 0)
        self.assertEqual(self.corpus.observations(first), ())
        self.assertIsNone(self.corpus.get_evidence(passage.evidence.evidence_id))
        self.assertIsNone(self.corpus.state(self.key).conditional_url)
        self.assertEqual(self.corpus.ingest(capture, unsupported).status, 'stale')
        recreated = self.corpus.ingest(self.capture(3 * 86400), extract)
        self.assertNotEqual(self.corpus.passages(recreated.document_id)[0].passage_id, passage.passage_id)
        with self.assertRaises(KeyError):
            self.corpus.save_embedding(int(passage.passage_id), 'index-1', MODEL, [1.0, 2.0])

    def test_expired_provenance_is_removed_from_a_newer_document(self):
        first = self.corpus.ingest(self.capture(), extract).document_id
        passage = self.corpus.passages(first)[0]
        self.corpus.save_embedding(int(passage.passage_id), 'index-1', MODEL, [1.0, 2.0])
        price_change = self.corpus.ingest(self.capture(9, '120'), extract).document_id
        self.assertEqual(self.corpus.passages(price_change), (passage,))

        policy = RetentionPolicy(raw_age=timedelta(seconds=5), content_age=timedelta(seconds=10))
        self.corpus.prune(now=START + timedelta(seconds=11), policy=policy)
        self.assertEqual(self.corpus.state(self.key).document_id, price_change)
        self.assertIsNone(self.corpus.state(self.key).conditional_url)
        self.assertEqual(self.corpus.passages(price_change), ())
        self.assertEqual(set(self.fields(price_change)), {'price'})
        self.assertEqual(self.count(corpus_embeddings), 0)

        rebuilt = self.corpus.ingest(self.capture(12, '120'), extract).document_id
        self.assertEqual(set(self.fields(rebuilt)), {'price', 'variant.specs.ram_gb'})
        self.assertEqual(self.fields(rebuilt)['price'].evidence[0].observed_at, START + timedelta(seconds=9))
        self.assertNotEqual(self.corpus.passages(rebuilt)[0].passage_id, passage.passage_id)
