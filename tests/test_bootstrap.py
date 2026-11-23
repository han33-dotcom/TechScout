from contextlib import redirect_stdout
from io import StringIO
import os
import sys
import hashlib

import httpx2
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from app.accounting import BudgetDenied, Charge, Ledger
from app.bootstrap import Manifest, Seed, collect, main
from app.embeddings import RATE_VARIABLE
from app.retrieval import ROUTED_MODEL, DIMENSIONS
from app.corpus_contracts import ResourceKey
from app.sources import FetchOutcome
from app.source_registry import DEFAULT_REGISTRY
from app.storage import load_fixtures
import test_corpus as fixtures


class BootstrapTests(TestCase):
    def setUp(self):
        fixture = fixtures.CorpusTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        load_fixtures(fixture.storage)
        self.corpus = fixture.corpus
        self.ledger = Ledger(fixture.storage,'bootstrap-test',fixture=True)
        self.ledger.provision()
        self.gate = Mock(_registry_path=DEFAULT_REGISTRY)
        self.manifest = Manifest.model_validate_json(Path('config/domestic-bootstrap-validation.json').read_bytes())

    def test_prequery_index_resume_and_original_policy_evidence(self):
        def fetch(budget, source, url, **options):
            seed = next(s for s in self.manifest.seeds if str(s.url)==url)
            if seed.format == 'catalog':
                body = json.dumps([dict(id=21,name='Synthetic model',parent=0,type='simple',
                                       categories=[dict(slug=seed.category_slugs[0])])]).encode()
            else:
                body = ('<div id="content"><style>.css{}</style><p>'+seed.policy_statements[0]+'</p></div>').encode()
            return FetchOutcome(source,url,(url,),1,2,final_url=url,body=body,
                observed_at=datetime.now(timezone.utc),response_sha256=hashlib.sha256(body).hexdigest())
        self.gate.fetch.side_effect = fetch
        limits = self.manifest.limits.model_copy(update={'max_paid_calls': 6, 'max_spend_usd': Decimal('0.01')})
        self.manifest = self.manifest.model_copy(update={'limits':limits})
        def collect_once():
            return collect(self.manifest,ledger=self.ledger,corpus=self.corpus,gate=self.gate)
        first = collect_once()
        self.assertEqual({p['category'] for p in first['pages'].values() if p['status']=='indexed'},
                         {'phone','laptop','audio','monitor','tablet'})
        self.assertEqual(collect_once()['pages'],first['pages'])
        from app.retrieval import ROUTED_MODEL, DIMENSIONS
        provider = Mock(side_effect=lambda texts:dict(model=ROUTED_MODEL,usage={'total_tokens':len(texts)},
            data=[dict(index=i,embedding=[1.0]+[0.0]*(DIMENSIONS-1)) for i in range(len(texts))]))
        for _ in range(2):
            collect(self.manifest,ledger=self.ledger,corpus=self.corpus,gate=self.gate,
                    embedder=provider,rate=Decimal('0.02'))
        self.assertEqual(provider.call_count,6)
        self.assertEqual(self.gate.fetch.call_count,6)
        # A fresh manifest sees the same corpus. A new processing recipe makes
        # its passages pending, and its paid-call ceiling stops indexing cleanly.
        limited = self.manifest.model_copy(update={
            'manifest_id':'limited-indexing',
            'processing':self.manifest.processing.model_copy(update={'index_version':'test-index-v2'}),
            'limits':limits.model_copy(update={'max_paid_calls':1}),
        })
        for _ in range(2):
            partial = collect(limited, ledger=self.ledger, corpus=self.corpus, gate=self.gate,
                              embedder=provider, rate=Decimal('0.02'))
            self.assertEqual(partial['stop_reason'], 'Manifest allowance exhausted')
            self.assertEqual(partial['all']['paid'], 1)
            self.assertIsNone(partial['owner'])
        self.assertEqual(provider.call_count, 7)
        seed = self.manifest.seeds[-1]
        state = self.corpus.state(ResourceKey(source_id=seed.source_id,url=seed.url,representation='html'))
        self.assertEqual([p.text for p in self.corpus.passages(state.document_id)],list(seed.policy_statements))

    def test_cli_retains_keyword_data_on_refusal_then_resumes_with_only_openrouter(self):
        seed = self.manifest.seeds[0]
        limits = self.manifest.limits.model_copy(update={'max_paid_calls': 3, 'max_spend_usd':Decimal('.01')})
        manifest = self.manifest.model_copy(update={'limits':limits,'seeds':(seed,)})
        path = self.corpus.storage.snapshots.root.parent / 'manifest.json'
        path.write_text(manifest.model_dump_json(), encoding='utf-8')
        url = str(seed.url)
        body = json.dumps([dict(id=21,name='Demo phone',parent=0,type='simple',
                                categories=[dict(slug=seed.category_slugs[0])])]).encode()
        self.gate.fetch.return_value = FetchOutcome(seed.source_id,url,(url,),1,2,final_url=url,body=body,
                observed_at=datetime.now(timezone.utc),response_sha256=hashlib.sha256(body).hexdigest())
        requests = []
        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx2.Response(403, json={'error':{'code':403}})
            return httpx2.Response(200, json={'model':ROUTED_MODEL,'usage':{'total_tokens':2},
                'data':[{'index':0,'embedding':[1.0]+[0.0]*(DIMENSIONS-1)}]})
        client = httpx2.Client
        def fake_http(**kwargs):
            return client(**(kwargs | {'transport':httpx2.MockTransport(handle)}))
        reports = []
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'fixture', RATE_VARIABLE:'0.02'}, clear=True), \
             patch.object(sys, 'argv', ['bootstrap',str(path)]), patch('app.inference.load_env_file'), \
             patch('app.bootstrap.Ledger.configured',return_value=self.ledger), \
             patch('app.bootstrap.Storage',return_value=self.corpus.storage), \
             patch('app.bootstrap.SourceGate',return_value=self.gate), patch('httpx2.Client',fake_http):
            for _ in range(3):
                out = StringIO()
                with redirect_stdout(out):
                    main()
                reports.append(json.loads(out.getvalue()))
        self.assertEqual(reports[0]['pages'][url]['status'], 'indexed')
        self.assertEqual(reports[0]['semantic_gap'], 'OpenRouter HTTP 403')
        self.assertNotIn('semantic_gap', reports[-1])
        self.assertEqual(len(requests), 2)
        self.assertEqual({str(req.url) for req in requests}, {'https://openrouter.ai/api/v1/embeddings'})
        self.assertEqual(self.gate.fetch.call_count, 1)
        self.assertEqual(reports[-1]['all']['paid'], 2)

    def test_new_manifest_respects_existing_campaign_category_allocation(self):
        seed = Seed.model_validate({**self.manifest.seeds[0].model_dump(),
            'source_id':'amazon', 'url':'https://www.amazon.com/dp/example',
            'category':'tablet', 'format':'html', 'capability':None,
        })
        manifest = self.manifest.model_copy(update={
            'manifest_id':'international-allocation-test', 'seeds':(seed,),
            'international_limits':self.manifest.limits,
        })
        with self.ledger._transaction() as (state, _):
            state['international_seed_urls'] = {'amazon':{
                f'https://www.amazon.com/dp/fixture{i}':'tablet' for i in range(10)
            }}
        def fetch(budget, source, url, **options):
            with self.assertRaisesRegex(BudgetDenied, 'category allocation exhausted'):
                budget.reserve('retrieval', Charge(Decimal(0)), 1, international=True)
            raise ValueError('Denied before dispatch')
        self.gate.fetch.side_effect = fetch
        with patch('app.bootstrap.get_active_source', return_value={}):
            run = collect(manifest, ledger=self.ledger, corpus=self.corpus, gate=self.gate)
        self.assertEqual(run['pages'][str(seed.url)]['error'], 'Denied before dispatch')
        self.assertEqual(len(self.ledger.snapshot()['international_seed_urls']['amazon']), 10)

    def test_budget_denial_survives_resume(self):
        m = self.manifest.model_copy(update={'limits':self.manifest.limits.model_copy(update={'max_fetch_attempts':1})})
        calls = []
        def fetch(budget, source, url, **options):
            hold = budget.reserve_call(source,1)
            if hold:
                calls.append(url)
                hold.release()
            raise ValueError('Synthetic retrieval failure')
        self.gate.fetch.side_effect = fetch
        for _ in range(2):
            result = collect(m,ledger=self.ledger,corpus=self.corpus,gate=self.gate)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['all']['fetch'],1)
