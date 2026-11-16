"""Offline English/Arabic and near-variant retrieval comparisons on both databases."""
from datetime import timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

import httpx2
from sqlalchemy import select

from app.corpus_contracts import Extraction, PassageDraft
from app.embeddings import EmbeddingService, OpenAIEmbeddings
from app.freshness import FreshnessContext
from app.retrieval import EvidenceQuery, EvidenceRetrieval, MODEL, DIMENSIONS
from app.storage import embedding_calls
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
        for value in ([0]*1536,[1]*20,[float('nan')]*1536):
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

    def service(self,provider):
        from app.accounting import Ledger
        ledger = Ledger(self.storage, 'retrieval-fixture', fixture=True)
        ledger.provision()
        return EmbeddingService(self.corpus,provider,usd_per_million=Decimal('0.02'),
                                job=ledger.open_job('embedding-fixture'))

    def response(self,texts):
        return {'model':MODEL,'usage':{'total_tokens':len(texts)},
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
        self.assertEqual(self.corpus.pending_embeddings(document,MODEL,1536),())

    def test_failure_keeps_uncertain_cost_and_budget_prevents_dispatch(self):
        provider = Mock(side_effect=TimeoutError('synthetic timeout'))
        service = self.service(provider)
        with self.assertRaises(ValueError):
            service.embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('0'))
        provider.assert_not_called()
        with self.assertRaises(TimeoutError):
            service.embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        usage, = self.usage()
        self.assertEqual(usage.status,'uncertain')
        self.assertIsNone(usage.actual_usd)
        self.assertGreater(usage.reserved_usd,0)

    def test_direct_api_contract_and_malformed_vectors(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertEqual(str(request.url),'https://api.openai.com/v1/embeddings')
            return httpx2.Response(200,json=self.response(['text']))
        provider = OpenAIEmbeddings('synthetic-key',transport=httpx2.MockTransport(handler))
        self.service(provider).embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        self.assertEqual(seen[0]['dimensions'],1536)
        invalid = self.response(['text'])
        invalid['data'][0]['embedding']=[1.0]
        with self.assertRaises(ValueError):
            self.service(lambda _:invalid).embed(['text'],owner_id='run-1',purpose='query',index_version='i',max_spend_usd=Decimal('1'))
        self.assertEqual(sorted(u.status for u in self.usage()),['completed','uncertain'])
