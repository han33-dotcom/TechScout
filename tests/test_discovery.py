from copy import deepcopy
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

import httpx2

from app.accounting import Ledger, Charge, BudgetDenied
from app.collection_contracts import RetailerDirectoryEntry
from app.contracts_v2 import ShoppingRequest
from app.directory import Directory, DirectoryRecord
from app.discovery import Discovery, Tavily, Lead, search_query
from app.sources import SourceGate
import test_corpus as fixtures
from test_corpus import START


def request(**changes):
    return ShoppingRequest.model_validate(dict(request_id='request',
        intent={'kind':'exact_model','manufacturer':'Lenovo','model':'ThinkPad'},
        requirements={'category':'laptop','min_ram_gb':16}) | changes)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.registry = self.storage.snapshots.root.parent/'sources.json'
        self.config = {'sources':[], 'live_release':{'admitted_source_ids':[]}}
        self.directory = Directory(self.storage,registry_path=self.registry)
        self.ledger = Ledger(self.storage,'discovery-fixture',fixture=True)
        self.ledger.provision()
        self.job = self.ledger.open_job('run')
        self.seen = []
        def handler(req):
            self.seen.append(str(req.url))
            body = b'<html>listing</html>'
            return httpx2.Response(200,headers={'content-type':'text/html','content-length':str(len(body))},
                                   stream=httpx2.ByteStream(body))
        self.gate = SourceGate._for_tests(self.registry,resolver=lambda *_:['8.8.8.8'],
                                         transport=httpx2.MockTransport(handler))

    def seller(self, index=0, *, market='LB', identity='retailer', admitted=True, score=.9):
        source_id, origin = f'store-{index}', f'https://store-{index}.example'
        url = origin + '/products/laptop'
        source = self.storage.load_source(fixtures.SOURCE).model_dump(mode='json')
        source.update(id=source_id, origin=origin, allowed_endpoints=[url])
        self.config['sources'].append(source)
        if admitted:
            self.config['live_release']['admitted_source_ids'].append(source_id)
        self.registry.write_text(json.dumps(self.config),encoding='utf-8')
        self.directory.upsert(DirectoryRecord(entry=RetailerDirectoryEntry(
            retailer_id=source_id,name=source_id,origin=origin,markets=(market,),categories=('laptop',),
            discovery_status='discovered'),updated_at=START,source_id=source_id,source_country=market,
            seller_identity=identity,seller_id=source_id if identity=='retailer' else None))
        return Lead(url,'ThinkPad','UNVERIFIED price $1 in stock',score)

    def run_discovery(self, leads, req=None, **kwargs):
        provider = Mock()
        provider.search.return_value = tuple(leads)
        return Discovery(provider,self.directory,self.gate).discover(req or request(),self.job,**kwargs)

    def test_search_payload_scope_and_usage(self):
        captured = []
        def handler(req):
            captured.append(json.loads(req.content))
            self.assertEqual(str(req.url),'https://api.tavily.com/search')
            self.assertEqual(req.headers['authorization'],'Bearer synthetic-key')
            return httpx2.Response(200,json={'results':[{'url':'https://new.example/p','title':'model',
                'content':'lead only','score':.8}],'usage':{'credits':1}})
        provider = Tavily('synthetic-key',usd_per_credit=Decimal('.01'),transport=httpx2.MockTransport(handler))
        leads = provider.search(request(),self.job)
        self.assertEqual(leads[0].snippet,'lead only')
        self.assertEqual(captured[0]['country'],'lebanon')
        self.assertFalse(captured[0]['auto_parameters'])
        self.assertFalse(captured[0]['include_answer'])
        self.assertFalse(captured[0]['include_raw_content'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][1],1)
        foreign = request(market_scope={'include_international':True,'international_markets':['US']})
        job = self.ledger.open_job('foreign',international=True)
        provider.search(foreign,job)
        self.assertEqual(captured[1]['country'],'lebanon')
        self.assertEqual(captured[0]['query'],captured[1]['query'])

    def test_query_keeps_hard_and_preference_budget_distinct(self):
        q = search_query(request(budget={'maximum':{'amount':'1000','currency':'USD'},
                                        'target':{'amount':'800','currency':'USD'}}))
        self.assertIn('maximum 1000 USD',q)
        self.assertIn('target 800 USD',q)
        self.assertIn('min_ram_gb 16',q)
        self.assertIn('condition new',q)

    def test_domain_discovery_has_no_fifteen_store_sample_cap(self):
        leads = [self.seller(i,score=.5+i/100) for i in range(20)]
        report = self.run_discovery(leads,max_sources=3)
        self.assertEqual(report.coverage.model_dump(),dict(discovered=20,attempted=3,retrieved=3,qualifying=0))
        self.assertEqual(set(self.seen),{leads[i].url for i in (19,18,17)})
        self.assertEqual(sum(r.status=='unchecked' for r in report.retailers.values()),17)

    def test_unknown_and_aggregator_snippets_never_become_listing_evidence(self):
        leads = [self.seller(identity='marketplace'),Lead('https://unknown.example/p','offer','in stock',1)]
        report = self.run_discovery(leads)
        self.assertEqual(len(report.leads),2)
        self.assertEqual(report.coverage.discovered,0)
        self.assertEqual(self.seen,[])

    def test_new_reviewed_retailer_and_unconfigured_domain_remain_distinct(self):
        good,bad = self.seller(21),self.seller(22,admitted=False)
        report = self.run_discovery([bad,good])
        self.assertEqual(self.seen,[good.url])
        self.assertEqual(report.retailers['store-22'].status,'unchecked')
        self.assertEqual(report.coverage.qualifying,0)

    def test_foreign_store_stays_out_of_scope_even_with_old_opt_in(self):
        lead = self.seller(market='US')
        report = self.run_discovery([lead])
        self.assertEqual(report.coverage.retrieved,0)
        self.job = self.ledger.open_job('foreign',international=True)
        report = self.run_discovery([lead],request(market_scope={'include_international':True,'international_markets':['US']}))
        self.assertEqual(report.coverage.attempted,0)
        self.assertEqual(self.seen,[])

    def test_covered_low_relevance_and_wrong_category_are_not_queried(self):
        first,second = self.seller(1),self.seller(2,score=.1)
        report = self.run_discovery([first,second],already_covered=('store-1',))
        self.assertEqual(report.coverage.attempted,0)
        self.assertEqual(self.seen,[])

    def test_remaining_budget_keeps_unchecked_distinct_from_failed(self):
        lead = self.seller()
        self.job.cancel()
        report = self.run_discovery([lead])
        self.assertEqual(report.retailers['store-0'].status,'unchecked')
        self.assertEqual(report.coverage.attempted,0)

    def test_retrieval_failure_and_no_qualification(self):
        lead = self.seller()
        self.gate = SourceGate._for_tests(self.registry,resolver=lambda *_:['8.8.8.8'],
            transport=httpx2.MockTransport(lambda _:httpx2.Response(404)))
        report = self.run_discovery([lead])
        self.assertEqual(report.retailers['store-0'].status,'failed')
        self.assertEqual(report.coverage.model_dump(),dict(discovered=1,attempted=1,retrieved=0,qualifying=0))

    def test_provider_failure_and_explicit_retry_each_hold_charge(self):
        count = []
        def handler(req):
            count.append(1)
            return httpx2.Response(503,json={'error':'unavailable'})
        provider = Tavily('synthetic',usd_per_credit=Decimal('.01'),transport=httpx2.MockTransport(handler))
        service = Discovery(provider,self.directory,self.gate)
        for _ in range(2):
            report = service.discover(request(),self.job)
            self.assertEqual(report.search_error,'HTTPStatusError')
        self.assertEqual(len(count),2)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3],'0.02')

    def test_malformed_oversized_and_unsafe_provider_results(self):
        for raw in ({'results':[]}, {'results':[{}],'usage':{'credits':1}}):
            provider = Tavily('synthetic',usd_per_credit=Decimal('.01'),
                transport=httpx2.MockTransport(lambda _, raw=raw:httpx2.Response(200,json=raw)))
            with self.assertRaises(ValueError):
                provider.search(request(),self.job)
        provider = Tavily('synthetic',usd_per_credit=Decimal('.01'),
            transport=httpx2.MockTransport(lambda _:httpx2.Response(200,json={'results':[],
                'usage':{'credits':1},'padding':'x'*2_097_153})))
        with self.assertRaisesRegex(ValueError,'size bound'):
            provider.search(request(),self.job)

    def test_provider_billing_overrun_stops_campaign(self):
        provider = Tavily('synthetic',usd_per_credit=Decimal('.01'),
            transport=httpx2.MockTransport(lambda _:httpx2.Response(200,json={
                'results':[], 'usage':{'credits':2}})))
        with self.assertRaises(BudgetDenied):
            provider.search(request(),self.job)
        self.assertTrue(self.ledger.snapshot()['blocked'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.02')

    def test_revocation_between_selection_and_fetch_is_honored(self):
        lead = self.seller()
        original = self.gate.fetch
        def revoke(*args,**kwargs):
            self.config['live_release']['admitted_source_ids']=[]
            self.registry.write_text(json.dumps(self.config),encoding='utf-8')
            return original(*args,**kwargs)
        self.gate.fetch = revoke
        report = self.run_discovery([lead])
        self.assertEqual(report.coverage.attempted,0)
        self.assertEqual(self.seen,[])
