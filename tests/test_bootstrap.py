import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

from app.accounting import Ledger
from app.bootstrap import Manifest, collect
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
        def collect_once():
            return collect(self.manifest,ledger=self.ledger,corpus=self.corpus,gate=self.gate)
        first = collect_once()
        self.assertEqual({p['category'] for p in first['pages'].values() if p['status']=='indexed'},
                         {'phone','laptop','audio','monitor','tablet'})
        self.assertEqual(collect_once()['pages'],first['pages'])
        self.assertEqual(self.gate.fetch.call_count,6)
        seed = self.manifest.seeds[-1]
        state = self.corpus.state(ResourceKey(source_id=seed.source_id,url=seed.url,representation='html'))
        self.assertEqual([p.text for p in self.corpus.passages(state.document_id)],list(seed.policy_statements))

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

