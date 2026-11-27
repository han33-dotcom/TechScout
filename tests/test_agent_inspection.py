import hashlib
import unittest
from unittest.mock import Mock, patch

from app.agent_inspection import inspect_public_listing, read_catalog_listing
from app.onboarding import ActivationRegistry, source_id_for
from app.sources import SourceGate
from app.accounting import BudgetDenied, Ledger
import test_corpus as fixtures


class AgentInspectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.ledger = Ledger(self.storage, 'inspection-fixture', fixture=True)
        self.ledger.provision()
        self.env = {'FIRECRAWL_API_KEY': 'fixture', 'FIRECRAWL_SCRAPE_USD_PER_CREDIT': '.01'}
        self.registry = ActivationRegistry(self.storage)
        self.url = 'https://unseen-shop.example/listings/q20i-black'

    def test_buyer_and_catalog_read_the_same_first_seen_url_through_firecrawl(self):
        html = '<h1>Soundcore Q20i, black</h1><p>USD 45</p>'
        reply = {'success': True, 'data': {'rawHtml': html, 'metadata': {
            'sourceURL': self.url, 'url': self.url, 'statusCode': 200, 'contentType': 'text/html',
            'provider': {'creditsCost': 1}, 'scrapeId': 'fixture-inspection'}}}

        def gate(scope):
            return SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])

        with patch('app.agent_inspection.SourceGate', side_effect=gate), \
             patch.object(SourceGate, 'fetch', side_effect=AssertionError('Unexpected direct GET')), \
             patch('app.firecrawl.post_json', return_value=reply) as send:
            for kind in ('buyer', 'maintenance'):
                job = self.ledger.open_job(kind, kind=kind)
                page = inspect_public_listing(job, self.url, self.registry, environ=self.env)
                self.assertEqual(page.outcome.source_id, source_id_for('unseen-shop.example'))
                self.assertEqual(page.outcome.final_url, self.url)
                self.assertEqual(page.outcome.body, html.encode())
                self.assertEqual(page.outcome.response_sha256, hashlib.sha256(html.encode()).hexdigest())
                self.assertIsNotNone(page.outcome.observed_at.utcoffset())
                self.assertEqual(page.request_id, 'fixture-inspection')
            self.assertEqual(send.call_count, 2)
            for call in send.call_args_list:
                endpoint, _, payload = call.args
                self.assertEqual(endpoint, 'https://api.firecrawl.dev/v2/scrape')
                self.assertEqual(payload['url'], self.url)
                self.assertEqual(payload['maxAge'], 0)
            self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.02')

    def test_private_destination_fails_before_provider_dispatch(self):
        job = self.ledger.open_job('private-destination')
        with patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                   SourceGate._for_tests(scope, resolver=lambda *_: ['127.0.0.1'])), \
             patch('app.firecrawl.post_json') as send:
            with self.assertRaisesRegex(ValueError, 'public destination'):
                inspect_public_listing(job, self.url, self.registry, environ=self.env)
            send.assert_not_called()

    def test_pending_shop_new_path_and_active_shop_workload_limit(self):
        job = self.ledger.open_job('known-hosts')
        for status, configured, expected in [('pending', 0, 3), ('active', 1, 1)]:
            with self.subTest(status=status):
                entry = {'id': 'known', 'origin': 'https://unseen-shop.example', 'status': status,
                         'limits': dict(requests_per_minute=configured, concurrency=configured,
                                        max_requests_per_run=configured)}
                document = {'sources': [entry], 'live_release': {
                    'admitted_source_ids': ['known'] if status == 'active' else []}}
                registry = ActivationRegistry(self.storage, base=Mock(read_registry=lambda: document))

                def provider(gate, env):
                    limits = gate.check_scope('known', self.url, expect='html')
                    self.assertEqual(limits.max_requests_per_run, expected)
                    return Mock()

                with patch('app.agent_inspection.Firecrawl.from_env', side_effect=provider) as make:
                    inspect_public_listing(job, self.url, registry, environ=self.env)
                    make.assert_called_once()

    def test_explicit_shop_stop_applies_to_new_product_links(self):
        entry = {'id': 'shop', 'origin': 'https://unseen-shop.example', 'status': 'paused'}
        document = {'sources': [entry], 'live_release': {'admitted_source_ids': []}}
        registry = ActivationRegistry(self.storage, base=Mock(read_registry=lambda: document))
        job = self.ledger.open_job('paused')
        with patch('app.firecrawl.post_json') as send:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                inspect_public_listing(job, self.url, registry, environ=self.env)
            send.assert_not_called()

    def test_catalog_cannot_offer_a_browser_after_a_source_refusal(self):
        inspect = Mock(side_effect=ValueError('Retailer inspection is disabled'))
        roles = Mock()
        with self.assertRaisesRegex(ValueError, 'disabled'):
            read_catalog_listing(inspect, self.ledger.open_job('blocked-read', 'maintenance'), self.url,
                                 objective='Check the selected SKU', roles=roles)
        self.assertEqual(inspect.call_count, 1)
        self.assertIsNone(inspect.call_args.kwargs['browser'])
        roles.generate.assert_not_called()

    def test_stopped_landing_shop_cannot_supply_an_observation(self):
        landing = 'https://paused.example/products/headphones'
        document = {'sources': [{'id': 'paused', 'origin': 'https://paused.example', 'status': 'paused'}],
                    'live_release': {'admitted_source_ids': []}}
        registry = ActivationRegistry(self.storage, base=Mock(read_registry=lambda: document))
        reply = dict(success=True, data=dict(rawHtml='<p>Fixture</p>', metadata=dict(
            sourceURL=self.url, url=landing, statusCode=200, contentType='text/html')))
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', return_value=reply) as send):
            with self.assertRaisesRegex(ValueError, 'disabled'):
                inspect_public_listing(self.ledger.open_job('paused-landing'), self.url, registry, environ=self.env)
            send.assert_called_once()

    def test_cross_shop_landing_counts_toward_both_shops_on_one_provider_call(self):
        landing = 'https://limited.example/item'
        document = {'sources': [{'id': 'limited', 'origin': 'https://limited.example', 'status': 'active',
                        'limits': dict(requests_per_minute=1, concurrency=1, max_requests_per_run=1)}],
                    'live_release': {'admitted_source_ids': ['limited']}}
        registry = ActivationRegistry(self.storage, base=Mock(read_registry=lambda: document))
        reply = dict(success=True, data=dict(rawHtml='<p>Fixture</p>', metadata=dict(
            sourceURL=self.url, url=landing, statusCode=200, contentType='text/html', provider=dict(creditsCost=1))))
        job = self.ledger.open_job('limited-landing')
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', return_value=reply) as send):
            page = inspect_public_listing(job, self.url, registry, environ=self.env)
            self.assertEqual(page.outcome.source_id, 'limited')
            with self.assertRaisesRegex(BudgetDenied, 'source workload'):
                inspect_public_listing(job, landing, registry, environ=self.env)
            send.assert_called_once()
        attempt, = self.ledger.snapshot()['attempts'].values()
        self.assertEqual((attempt['source'], attempt['landing_source']), (source_id_for('unseen-shop.example'), 'limited'))
