import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from app.accounting import BudgetDenied, Charge, Ledger
from app.bootstrap import Manifest, Seed, collect
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
        from app.retrieval import MODEL, DIMENSIONS
        provider = Mock(side_effect=lambda texts:dict(model=MODEL,usage={'total_tokens':len(texts)},
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
