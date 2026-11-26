from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import unittest

import httpx2
from app.accounting import Charge
from app.contracts_v2 import VerifiedOffer
from app.corpus_contracts import ProcessingVersion, ResourceKey
from app.domestic_discovery import CHANNEL_LIMITS, DomesticDiscovery, SearchObjective
from app.extraction import PageProfile
from app.search_connectors import (
    BraveSearch, DirectoryPage, FirecrawlSearch, ManufacturerDirectories, SerpApiSearch, TavilySearch,
)
from app.sources import SourceGate
import test_discovery as fixtures
import test_writer as writer

RATE = Decimal('.01')


class DomesticDiscoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.DiscoveryTests.setUp(self)
        self.objective = SearchObjective('حاسوب X16 بيروت لبنان', 'Find another seller for the same laptop',
                                         language='ar', limit=4)
        self.calls = []
        self.responses = {}

    def seller(self, i=0):
        return fixtures.DiscoveryTests.seller(self, i).url

    def service(self):
        def respond(req):
            self.calls.append(req)
            value = self.responses[req.url.host]
            if isinstance(value, Exception):
                raise value
            return httpx2.Response(value if isinstance(value, int) else 200,
                                   headers={'x-request-id': 'http-fixture'}, json=value)
        providers = {name: factory('fixture-only', RATE, transport=httpx2.MockTransport(respond))
                     for name, factory in (('tavily', TavilySearch), ('brave', BraveSearch),
                                           ('serpapi', SerpApiSearch), ('firecrawl', FirecrawlSearch))}
        return DomesticDiscovery(self.gate, self.directory, providers)

    def test_localized_direct_channels_deduplicate_reviewed_aliases_and_attribute_each_hit(self):
        first, second = self.seller(), self.seller(1)
        store = self.directory.get('store-0')
        raw = store.model_dump(mode='json')
        raw['updated_at'] = store.updated_at + timedelta(seconds=1)
        raw['entry']['related_origins'] = ['https://alias.example/']
        self.directory.upsert(type(store).model_validate(raw))
        self.responses = {
            'api.tavily.com': {'request_id': 'one', 'usage': {'credits': 1},
                               'results': [dict(url=first, title='listing', content='unverified', score=.8)]},
            'serpapi.com': {'search_metadata': {'id': 'two', 'status': 'Success'},
                            'organic_results': [dict(link=first, title='listing two'),
                                                dict(link='https://alias.example/listing', title='alias')]},
            'api.search.brave.com': {'type': 'search', 'web': {'results': [dict(url=second, title='new seller')]}},
            'api.firecrawl.dev': {'success': True, 'id': 'four', 'creditsUsed': 2,
                                  'data': {'web': [dict(url=second, title='same seller')]}},
        }
        with self.assertLogs('httpx2', level='INFO') as logs:
            result = self.service().rotate(self.objective, self.job, count=5)
        self.assertNotIn('fixture-only', str(logs.output))
        self.assertNotIn('api_key=', str(logs.output))
        self.assertEqual([r.status for r in result.attempts], ['ok','ok','ok','unavailable','ok'])
        self.assertEqual([lead.seller_key for lead in result.leads], ['seller:store-0','seller:store-0','seller:store-1'])
        self.assertEqual([a.request_id for a in result.leads[0].attribution], ['one','two'])
        self.assertEqual(result.leads[0].attribution[0].score, .8)
        self.assertEqual([a.channel for a in result.leads[-1].attribution], ['brave','firecrawl'])
        self.assertEqual(result.leads[-1].attribution[0].request_id, 'http-fixture')
        self.assertEqual([r.actual_usd for r in result.attempts], [RATE,None,None,None,2*RATE])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.05')
        tavily, google, brave, fire = self.calls
        self.assertEqual(json.loads(tavily.content)['language'], 'ar')
        self.assertEqual(json.loads(tavily.content)['country'], 'lebanon')
        self.assertEqual(google.url.params['hl'], 'ar')
        self.assertEqual(google.url.params['gl'], 'lb')
        self.assertEqual(brave.url.params['q'], self.objective.query)
        self.assertEqual(brave.url.params['search_lang'], 'ar')
        self.assertEqual(brave.headers['x-subscription-token'], 'fixture-only')
        self.assertNotIn('scrapeOptions', json.loads(fire.content))
        self.assertEqual(json.loads(fire.content)['country'], 'LB')
        self.assertEqual(self.seen, [])  # search does not read or activate the listings

    def test_manufacturer_uses_only_configured_seller_anchors_and_attributes_the_listing_page(self):
        source_page = self.seller()
        markup = b"""<a class="dealer" href="https://other.example/">outside</a>
            <section id="lebanon"><a href="https://ads.example/">ad</a>
            <a class="dealer" href="https://shop.example/">Trusted <b>retailer</b></a>
            <a class="dealer" href="http://unsafe.example/">insecure</a></section>"""
        def respond(req):
            self.assertEqual(str(req.url), source_page)
            return httpx2.Response(200, headers={'content-type':'text/html','content-length':str(len(markup))},
                                   stream=httpx2.ByteStream(markup))
        gate = SourceGate._for_tests(self.registry, resolver=lambda *_: ['8.8.8.8'],
                                     transport=httpx2.MockTransport(respond))
        page = DirectoryPage('ASUS', 'store-0', source_page, 'lebanon', 'dealer')
        service = DomesticDiscovery(gate, self.directory, {'manufacturer': ManufacturerDirectories(
            gate, self.directory, [page])})
        search = replace(self.objective, channel='manufacturer')
        result = service.search(search, self.job)
        self.assertEqual([a.status for a in result.attempts], ['ok'])
        self.assertEqual(len(result.leads), 1)
        lead = result.leads[0]
        self.assertEqual((lead.url, lead.seller_key), ('https://shop.example/', 'host:shop.example'))
        self.assertEqual((lead.attribution[0].source_url, lead.attribution[0].title),
                         (source_page,'Trusted retailer'))
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][0:2], [1,0])
        self.config['live_release']['admitted_source_ids'] = []
        self.registry.write_text(json.dumps(self.config))
        self.assertEqual(service.search(search, self.job).attempts[0].status, 'refused')

    def test_timeout_and_rate_limit_fail_over_within_one_job_and_keep_the_uncertain_quotes(self):
        self.responses = {'api.tavily.com': httpx2.ReadTimeout('fixture'), 'serpapi.com': 429,
                          'api.search.brave.com': {'type':'search', 'web': {'results':[]}}}
        result = self.service().search(self.objective, self.job, alternatives=('serpapi','brave','firecrawl'))
        self.assertEqual([a.status for a in result.attempts], ['timeout','rate_limited','ok'])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][1:2], [3])
        for attempt in self.ledger.snapshot()['attempts'].values():
            self.assertEqual((attempt['job'], attempt['status']), ('run', 'uncertain'))

    def test_provider_source_concurrency_and_campaign_denial(self):
        held = self.job.reserve('search', Charge(RATE), 20, source='discovery:tavily', limits=CHANNEL_LIMITS)
        self.responses = {'serpapi.com': {'search_metadata': {'id':'test','status':'Success'}}}
        other = self.ledger.open_job('sibling')
        result = self.service().search(self.objective, other, alternatives=('serpapi',))
        self.assertEqual([a.status for a in result.attempts], ['rate_limited','ok'])
        held.release()
        self.job.reserve('model', Charge(Decimal('.75')), 1)
        result = self.service().search(self.objective, self.job, alternatives=('brave',))
        self.assertEqual([a.status for a in result.attempts], ['budget_denied'])
        self.assertEqual(len(self.calls), 1)

    def test_reported_overage_stops_failover_and_is_settled_at_actual_cost(self):
        self.responses = {'api.tavily.com': {'results':[{'url': None}], 'usage': {'credits':5}}}
        result = self.service().search(self.objective, self.job, alternatives=('brave',))
        self.assertEqual(result.attempts[0].status, 'budget_denied')
        self.assertEqual(result.attempts[0].actual_usd, 5*RATE)
        self.assertTrue(self.ledger.snapshot()['blocked'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.05')
        self.assertEqual(len(self.calls), 1)

    def test_search_outage_allows_direct_known_listing_read_and_cancel_stops_new_calls(self):
        url = self.seller()
        body = b'{"@type":"Product","sku":"X16","offers":{"@type":"Offer","price":"91","priceCurrency":"USD","availability":"https://schema.org/InStock"}}'
        html = b'<script type="application/ld+json">' + body + b'</script>'
        def respond(req):
            self.assertEqual(str(req.url), url)
            return httpx2.Response(200, headers={'content-type':'text/html','content-length':str(len(html))},
                                   stream=httpx2.ByteStream(html))
        gate = SourceGate._for_tests(self.registry, resolver=lambda *_: ['8.8.8.8'],
                                     transport=httpx2.MockTransport(respond))
        service = self.service()
        service.gate = gate
        self.responses = {host: 503 for host in ('api.tavily.com','serpapi.com','api.search.brave.com')}
        result = service.search(self.objective, self.job, alternatives=('serpapi','brave','firecrawl'))
        self.assertEqual([a.status for a in result.attempts], ['provider_error']*3)
        self.assertEqual(result.leads, ())
        key = ResourceKey(source_id='store-0', url=url, representation='html', product_id='product', variant_id='X16')
        processing = ProcessingVersion(corpus_version='corpus', extraction_version='v1',
                                       chunk_version='v1', index_version='index')
        page = service.read(self.job, key, PageProfile('store-0','https://store-0.example',sku='X16'),
                            processing, origin='runtime')
        fields = {o.evidence[0].field_path:o.fact.value for o in page.extraction.observations}
        self.assertEqual(fields['price']['amount'], '91')
        self.assertTrue(fields['stock'])
        self.job.cancel()
        result = service.search(self.objective, self.job, alternatives=('brave',))
        self.assertEqual(result.attempts[0].status, 'cancelled')
        self.assertEqual(len(self.calls), 3)

    def test_configuration_is_separate_for_each_channel_and_reader(self):
        env = dict(BRAVE_SEARCH_API_KEY='only', BRAVE_USD_PER_SEARCH='.005', SERPAPI_USD_PER_SEARCH='.01',
                   FIRECRAWL_API_KEY='scrape-only', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.01',
                   TAVILY_API_KEY='malformed-price', TAVILY_USD_PER_CREDIT='NaN')
        service = DomesticDiscovery.from_env(self.gate, self.directory, environ=env)
        self.assertEqual(set(service.connectors), {'brave'})
        self.assertIsNotNone(service.fallback)
        env['FIRECRAWL_SEARCH_USD_PER_CREDIT'] = '.01'
        del env['FIRECRAWL_SCRAPE_USD_PER_CREDIT']
        service = DomesticDiscovery.from_env(self.gate, self.directory, environ=env)
        self.assertIn('firecrawl', service.connectors)
        self.assertIsNone(service.fallback)

    def test_included_quota_requires_an_explicit_setting_and_uses_a_call_slot(self):
        env = dict(SERPAPI_API_KEY='fixture-only', SERPAPI_USD_PER_SEARCH='included',
                   BRAVE_SEARCH_API_KEY='fixture-only', BRAVE_USD_PER_SEARCH='0')
        service = DomesticDiscovery.from_env(self.gate, self.directory, environ=env)
        self.assertEqual(set(service.connectors), {'serpapi'})
        connector = service.connectors['serpapi']
        self.assertEqual(connector.quote(self.objective), Decimal(0))
        self.responses = {'serpapi.com': {'search_metadata': {'status': 'Success'}, 'organic_results': []}}
        connector.transport = self.service().connectors['serpapi'].transport
        result = service.survey(self.objective, self.job, ('serpapi',))
        self.assertEqual(result.attempts[0].status, 'ok')
        self.assertIsNone(result.attempts[0].usage_units)
        self.assertIsNone(result.attempts[0].actual_usd)
        usage = self.ledger.snapshot()['scopes']['project']
        self.assertEqual((usage[0], Decimal(usage[3]), usage[4]), (1, Decimal(0), 1))

    def test_incremental_coverage_requires_an_established_seller_and_same_variant(self):
        """Coverage credit uses the worker's seller rule (#185), not dispatch origin."""
        first, second = self.seller(), self.seller(1)
        self.responses = {'api.tavily.com': dict(results=[dict(url=first)], usage=dict(credits=1)),
                          'serpapi.com': dict(search_metadata=dict(status='Success'),
                                             organic_results=[dict(link=first),dict(link=second)])}
        def verified(index, *, seller='first_party', receipts_run=None):
            raw = deepcopy(writer.primary(writer.snapshot()))
            offer = raw['offer']
            url = (first, second)[index]
            sid = 'store-' + str(index)
            offer.update(source_id=sid, retailer_id=sid, listing_url=url)
            offer['seller'].update(seller_id=sid, first_party=seller == 'first_party')
            offer['route']['origin_country'] = 'ZZ'  # an unknown warehouse never withholds credit
            raw['verification']['run']['run_id'] = self.job.run_id
            for e in offer['evidence']:
                e.update(source_id=sid, origin_url=url, final_url=url)
                if e.get('run'):
                    e['run']['run_id'] = self.job.run_id
            if seller == 'listing':
                price = next(e for e in offer['evidence'] if e['field_path'] == 'price')
                run = dict(price['run'], run_id=receipts_run or self.job.run_id)
                offer['evidence'] += [dict(price, evidence_id=f'{sid}:{path}', field_path=path, excerpt=path, run=run)
                                      for path in ('seller.name', 'seller.market')]
            return VerifiedOffer.model_validate(raw)
        report = self.service().rotate(self.objective, self.job, count=2)
        a, b = verified(0), verified(1)
        for unestablished in (verified(1, seller=None), verified(1, seller='listing', receipts_run='earlier-run')):
            self.assertEqual(report.coverage((a, unestablished))['serpapi']['additional_usable_sellers'], 0)
        named = report.coverage((a, verified(1, seller='listing')))
        self.assertEqual(named['serpapi'], dict(additional_usable_sellers=1, new_same_variant_comparisons=1))
        counts = report.coverage((a,b))
        self.assertEqual(counts['tavily'], dict(additional_usable_sellers=1,new_same_variant_comparisons=0))
        self.assertEqual(counts['serpapi'], dict(additional_usable_sellers=1,new_same_variant_comparisons=1))
        from_catalog = report.coverage((b,), baseline=(a,))
        self.assertEqual(from_catalog['tavily']['additional_usable_sellers'], 0)
        self.assertEqual(from_catalog['serpapi']['new_same_variant_comparisons'], 1)
        unrelated = replace(report.leads[1], url='https://store-1.example/another-listing')
        sibling_report = replace(report, leads=(report.leads[0], unrelated))
        self.assertEqual(sibling_report.coverage((a,b))['serpapi']['new_same_variant_comparisons'], 0)
