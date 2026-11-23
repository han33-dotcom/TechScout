"""Offline English/Arabic and near-variant retrieval comparisons on both databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

import httpx2
from sqlalchemy import select

from app.corpus import Corpus
from app.corpus_contracts import Extraction, PassageDraft
from app.embeddings import (EmbeddingRoute, EmbeddingService, EmbeddingUnavailable, EmbeddingIndexBusy,
                            OpenRouterEmbeddings, check_route)
from app.freshness import FreshnessContext
from app.retrieval import EvidenceQuery, EvidenceRetrieval, MODEL, DIMENSIONS, ROUTED_MODEL, routed_index_version
from app.storage import Storage, embedding_calls
import test_corpus as fixtures
from test_corpus import START, SOURCE, citation, extract


def vector(direction=0):
    return [float(i==direction) for i in range(DIMENSIONS)]


class RetrievalTests(unittest.TestCase):
    setUp = fixtures.CorpusTests.setUp
    capture = fixtures.CorpusTests.capture

    def query(self,text='RAM',**changes):
        data = dict(text=text,source_ids=(SOURCE,),scope='variant',field_paths=('variant.specs.ram_gb',),
            context=FreshnessContext(now=START+timedelta(seconds=300),product_id='book',variant_id='book-16'),
            corpus_version='corpus-1',index_version='index-1',chunk_version='chunk-1')
        return EvidenceQuery(**(data|changes))

    def add(self,text,*,second=0,variant='book-16',route=None,direction=0,visibility='public'):
        key = self.key.model_copy(update={'variant_id':variant,'route_id':route,
            'representation':f'fixture-{variant}-{route}'})
        capture = self.capture(second,key=key,visibility=visibility).model_copy(update={
            'body':text.encode(),'relevant_content':text.encode()})
        def extraction(c):
            receipt = citation(c,'variant.specs.ram_gb',text).model_copy(update={'variant_id':variant,'route_id':route})
            return Extraction(passages=(PassageDraft(locator='spec',text=text,evidence=receipt),))
        document = self.corpus.ingest(capture,extraction).document_id
        passage = self.corpus.passages(document)[0]
        self.corpus.save_embedding(int(passage.passage_id),'index-1',MODEL,vector(direction))
        return passage

    def test_english_arabic_keyword_vs_hybrid_returns_scoped_receipts(self):
        correct = self.add('ذاكرة 16 جيجابايت')
        wrong = self.add('RAM memory 32 GB',variant='book-32')
        retrieval = EvidenceRetrieval(self.corpus)
        self.assertEqual(retrieval.retrieve(self.query('memory')),())
        hybrid = retrieval.retrieve(self.query('memory'),vector=vector())
        self.assertEqual([h.passage.passage_id for h in hybrid],[correct.passage_id])
        self.assertEqual(hybrid[0].channels,('vector',))
        arabic = retrieval.retrieve(self.query('ذاكرة'))
        self.assertEqual(arabic[0].passage.evidence.evidence_id,correct.evidence.evidence_id)
        self.assertEqual(arabic[0].channels,('keyword',))
        self.assertNotEqual(arabic[0].passage.passage_id,wrong.passage_id)

    def test_route_private_and_superseded_passages_are_excluded(self):
        old = self.add('RAM old')
        new = self.add('RAM new',second=60)
        self.add('RAM route',route='other-route')
        with self.assertRaisesRegex(ValueError,'Private'):
            self.add('RAM private',variant='private',visibility='private')
        hits = EvidenceRetrieval(self.corpus).retrieve(self.query(),vector=vector())
        self.assertEqual([h.passage.passage_id for h in hits],[new.passage_id])
        self.assertNotEqual(old.passage_id,new.passage_id)

    def test_wrong_index_chunk_source_and_disabled_source_are_excluded(self):
        self.add('RAM 16 GB')
        retrieval = EvidenceRetrieval(self.corpus)
        for change in ({'index_version':'index-2'},{'chunk_version':'chunk-2'},
                       {'corpus_version':'corpus-2'},{'source_ids':('other',)}):
            self.assertEqual(retrieval.retrieve(self.query(**change),vector=vector()),())
        source = self.storage.load_source(SOURCE)
        self.storage.save_source(source.model_copy(update={'status':'paused'}))
        self.assertEqual(retrieval.retrieve(self.query()),())

    def test_result_limit_and_historical_evidence_is_not_a_fresh_claim(self):
        passage = self.add('RAM 16 GB')
        query = self.query(limit=1,candidate_limit=1,context=FreshnessContext(
            now=START+timedelta(days=31),product_id='book',variant_id='book-16'))
        hit, = EvidenceRetrieval(self.corpus).retrieve(query,vector=vector())
        self.assertEqual(hit.freshness.state,'historical')
        self.assertEqual(hit.passage.evidence,passage.evidence)
        for value in ([0]*1536,[1]*20,[float('nan')]*1536,[1e40]*1536,[1e-50]*1536):
            with self.assertRaises(ValueError):
                EvidenceRetrieval(self.corpus).retrieve(query,vector=value)

    def test_supported_retirement_excludes_even_a_matching_vector(self):
        self.add('RAM 16 GB')
        key = self.key.model_copy(update={'representation':'fixture-book-16-None'})
        capture = self.capture(60,key=key).model_copy(update={
            'body':b'Discontinued', 'relevant_content':b'Discontinued'})
        receipt = citation(capture,'resource.availability','Discontinued')
        self.corpus.record_retirement(capture,receipt,status='discontinued')
        self.assertEqual(EvidenceRetrieval(self.corpus).retrieve(self.query(),vector=vector()),())

    def test_deleted_evidence_and_stale_vectors_stay_out_after_reopen(self):
        older = self.add('RAM original receipt')
        current = self.add('RAM corrected receipt', second=60)
        self.add('RAM different configuration', variant='book-32')
        key = self.key.model_copy(update={'representation':'fixture-book-16-None'})
        retrieval = EvidenceRetrieval(self.corpus)
        self.assertEqual([hit.passage for hit in retrieval.retrieve(self.query(), vector=vector())],
                         [current])
        self.corpus.delete_resource(key)

        reopened = Storage(self.url, self.storage.snapshots.root)
        self.addCleanup(reopened.engine.dispose)
        corpus = Corpus(reopened)
        retrieval = EvidenceRetrieval(corpus)
        self.assertIsNone(corpus.get_evidence(older.evidence.evidence_id))
        self.assertIsNone(corpus.get_evidence(current.evidence.evidence_id))
        self.assertEqual(retrieval.retrieve(self.query()), ())
        self.assertEqual(retrieval.retrieve(self.query(), vector=vector()), ())
        neighboring = self.query(context=FreshnessContext(
            now=START+timedelta(seconds=300), product_id='book', variant_id='book-32'))
        self.assertEqual(len(retrieval.retrieve(neighboring, vector=vector())), 1)

    def service(self,provider):
        from app.accounting import Ledger
        ledger = Ledger(self.storage, 'retrieval-fixture', fixture=True)
        ledger.provision()
        return EmbeddingService(self.corpus,provider,usd_per_million=Decimal('0.02'),
                                job=ledger.open_job('embedding-fixture'))

    def response(self,texts):
        return {'model':ROUTED_MODEL,'usage':{'total_tokens':len(texts),'cost':Decimal('0.00000002'),'is_byok':False},
                'data':[{'index':i,'embedding':vector()} for i,_ in enumerate(texts)]}

    def usage(self):
        with self.storage.engine.connect() as db:
            return db.execute(select(embedding_calls)).all()

    def test_embedding_meter_and_idempotent_indexing(self):
        document = self.corpus.ingest(self.capture(),extract).document_id
        provider = Mock(side_effect=self.response)
        service = self.service(provider)
        self.assertEqual(service.index_document(document,owner_id='job-1',max_spend_usd=Decimal('1')),1)
        self.assertEqual(service.index_document(document,owner_id='job-1',max_spend_usd=Decimal('1')),0)
        self.assertEqual(provider.call_count,1)
        usage, = self.usage()
        self.assertEqual((usage.status,usage.tokens,usage.actual_usd),('completed',1,Decimal('0.00000002')))
        self.assertEqual(self.corpus.pending_embeddings(document,ROUTED_MODEL,1536,index_version=routed_index_version('index-1')),())

    def test_failure_keeps_uncertain_cost_and_budget_prevents_dispatch(self):
        provider = Mock(side_effect=TimeoutError('synthetic timeout'))
        service = self.service(provider)
        with self.assertRaises(ValueError):
            service.embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('0'))
        provider.assert_not_called()
        with self.assertRaises(EmbeddingUnavailable):
            service.embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        usage, = self.usage()
        self.assertEqual(usage.status,'uncertain')
        self.assertIsNone(usage.actual_usd)
        self.assertGreater(usage.reserved_usd,0)

    def test_openrouter_request_uncertain_billing_and_malformed_vectors(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertEqual(str(request.url),'https://openrouter.ai/api/v1/embeddings')
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-key')
            reply = self.response(['text'])
            reply['usage'] = {'total_tokens': 1}
            return httpx2.Response(200,json=reply)
        provider = OpenRouterEmbeddings('synthetic-key',transport=httpx2.MockTransport(handler))
        self.service(provider).embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        self.assertEqual((seen[0]['model'],seen[0]['dimensions']), (ROUTED_MODEL,1536))
        self.assertEqual(seen[0]['provider']['only'], ['openai'])
        self.assertFalse(seen[0]['provider']['allow_fallbacks'])
        row, = self.usage()
        self.assertEqual((row.status, row.tokens, row.actual_usd), ('uncertain', 1, None))
        invalid = self.response(['text'])
        invalid['data'][0]['embedding']=[1.0]
        with self.assertRaises(ValueError):
            self.service(lambda _:invalid).embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        self.assertEqual(sorted(u.status for u in self.usage()),['completed','uncertain'])

    def test_rebuild_legacy_space_once_without_retiming_or_embedding_a_price_change(self):
        document = self.corpus.ingest(self.capture(), extract).document_id
        original = self.corpus.passages(document)[0]
        self.corpus.save_embedding(int(original.passage_id), 'index-1', MODEL, vector(1))
        retrieval = EvidenceRetrieval(self.corpus)
        self.assertFalse(retrieval.has_routed_vectors(self.query()))
        provider = Mock(side_effect=self.response)
        service = self.service(provider)
        def index(doc):
            return service.index_document(doc, owner_id='bootstrap', max_spend_usd=Decimal('1'))
        self.assertEqual(index(document), 1)
        self.assertTrue(retrieval.has_routed_vectors(self.query()))
        refreshed = self.corpus.ingest(self.capture(60, price='102.00'), extract).document_id
        self.assertEqual(index(refreshed), 0)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.corpus.passages(refreshed)[0], original)
        price = next(o for o in self.corpus.observations(refreshed) if o.evidence[0].field_path == 'price')
        self.assertEqual(price.evidence[0].observed_at, START + timedelta(seconds=60))
        usage, = self.usage()
        self.assertEqual((usage.model, usage.index_version), (ROUTED_MODEL, routed_index_version('index-1')))

    def test_bounded_route_check_records_reported_cost_even_when_billing_is_uncertain(self):
        def reply(request):
            self.assertEqual(json.loads(request.content)['input'], ['TechScout'])
            return httpx2.Response(200, json=dict(model=ROUTED_MODEL,
                openrouter_metadata={'endpoints':{'available':[
                    {'provider':'OpenAI','name':'fixture-route','selected':True}]}},
                usage={'total_tokens':2,'cost':0.00000004},
                data=[dict(index=0, embedding=vector())]))
        route = EmbeddingRoute.configured(environ={'OPENROUTER_API_KEY':'secret'},
            rate=Decimal('.02'), transport=httpx2.MockTransport(reply))
        ledger = self.service(Mock()).job.ledger
        receipt = check_route(route, ledger, self.storage)
        self.assertEqual((receipt['result'], receipt['billing'], receipt['actual_usd']),
                         ('validated','uncertain',None))
        self.assertEqual((Decimal(receipt['reported_cost_usd']), receipt['is_byok']),
                         (Decimal('.00000004'), None))
        self.assertEqual((receipt['serving_provider'], receipt['serving_endpoint']), ('OpenAI','fixture-route'))
        self.assertLessEqual(Decimal(receipt['reserved_usd']), Decimal('.000001'))
        self.assertNotIn('secret', repr(receipt))
        self.assertEqual(len(ledger.snapshot()['attempts']), 1)

    def test_simultaneous_refresh_and_resume_only_send_one_index_batch(self):
        document = self.corpus.ingest(self.capture(), extract).document_id
        entered, finish = Event(), Event()
        def slow(texts):
            entered.set()
            self.assertTrue(finish.wait(5))
            return self.response(texts)
        provider = Mock(side_effect=slow)
        service = self.service(provider)
        kwargs = dict(owner_id='indexer', max_spend_usd=Decimal('1'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(service.index_document, document, **kwargs)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(EmbeddingIndexBusy):
                    service.index_document(document, **kwargs)
            finally:
                finish.set()
            self.assertEqual(result.result(), 1)
        self.assertEqual(service.index_document(document, **kwargs), 0)
        self.assertEqual(provider.call_count, 1)

    def test_cancellation_after_dispatch_ignores_late_vectors_and_keeps_reservation(self):
        entered, finish = Event(), Event()
        def slow(texts):
            entered.set()
            self.assertTrue(finish.wait(5))
            return self.response(texts)
        service = self.service(slow)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(service.embed, ['text'], owner_id='research', purpose='query',
                                 index_version='i', max_spend_usd=Decimal('.01'))
            try:
                self.assertTrue(entered.wait(5))
                service.job.cancel()
                with self.assertRaisesRegex(EmbeddingUnavailable, 'cancelled'):
                    result.result(timeout=4)
            finally:
                finish.set()
        usage, = self.usage()
        self.assertEqual((usage.status, usage.actual_usd), ('uncertain', None))

    def test_missing_route_and_cancelled_job_never_use_direct_credentials(self):
        route = EmbeddingRoute.configured(rate=Decimal('.02'), environ={'OPENAI_API_KEY':'direct'})
        self.assertIsNone(route.provider)
        self.assertIn('OPENROUTER_API_KEY', route.unavailable)
        provider = Mock()
        service = self.service(provider)
        service.job.cancel()
        from app.accounting import BudgetDenied
        with self.assertRaises(BudgetDenied):
            service.embed(['text'], owner_id='run', purpose='query', index_version='i', max_spend_usd=Decimal('1'))
        provider.assert_not_called()
        self.assertEqual(self.usage(), [])
