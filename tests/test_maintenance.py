from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx2

from app.accounting import BudgetDenied, Charge
from app.bootstrap import Seed
from app.domestic_discovery import CHANNELS, ChannelAttempt, SearchReport
from app.maintenance import Maintenance, Settings, control, signal, status
from app.maintenance_discovery import SearchDecision
import test_refresh as fixtures


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = fixtures.RefreshTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.ledger = f.fixture.ledger
        self.tools = Mock(connectors=dict.fromkeys(CHANNELS))
        self.tools.search.side_effect = lambda objective, job: SearchReport(job.run_id, (
            ChannelAttempt(objective.channel, 'ok'),), ())
        self.queue = Mock()
        self.queue.enqueue_domestic.return_value = ()
        def choose(prompt, data, schema, **kwargs):
            channel = data['available'][0]
            return SimpleNamespace(status='completed', value=SearchDecision(action='search', channel=channel,
                    language='ar', query='Lebanon ' + channel))
        self.roles = Mock()
        self.roles.settings.timeout_seconds = 16
        self.roles.generate.side_effect = choose
        self.runner = Maintenance(self.ledger, f.fixture.corpus, f.gate, f.manifest,
                Settings.model_validate_json(Path('config/maintenance.json').read_bytes()),
                discovery=self.tools, queue=self.queue, roles=self.roles)

    def test_control_rotation_gap_privacy_and_interrupted_agent(self):
        self.assertIsNone(self.runner.run('discovery', force=True))
        control(self.ledger, True)
        signal(self.ledger, 'monitor', catalog_urls=('https://one.example/selected',))
        self.assertNotIn('one.example', json.dumps(self.ledger.snapshot()['maintenance_schedule']))
        first = self.runner.run('discovery')
        self.assertEqual(first['category'], 'monitor')
        self.assertIsNone(self.runner.run('discovery'))
        for _ in range(2):
            self.fixture.now += timedelta(days=1)
            self.runner.run('discovery')
        self.assertEqual({c.args[0].channel for c in self.tools.search.call_args_list}, set(CHANNELS))
        self.assertEqual(self.roles.generate.call_args.args[1]['previous'][0]['status'], 'ok')
        self.fixture.now += timedelta(days=1)
        self.roles.generate.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.runner.run('discovery')
        self.assertIsNone(self.runner.run('discovery', force=True))
        self.fixture.now += timedelta(seconds=185)
        recovered = self.runner.run('discovery')
        self.assertIn('Interrupted', recovered['stop_reason'])
        self.assertEqual(recovered['calls'], 0)
        control(self.ledger, False)
        self.assertFalse(status(self.ledger)['enabled'])

    def test_other_process_shares_resource_and_job_envelope(self):
        f = self.fixture
        control(self.ledger, True)
        active = self.runner.claim('offers', False)
        envelope = dict(calls=1, seconds=30, usd='0.01')
        job = self.ledger.open_job(active['job'], 'maintenance', envelope=envelope)
        policy = f.fixture.policies[0]
        from app.contracts import SourceLimits
        limits = SourceLimits.model_validate(dict(policy['limits'], concurrency=3))
        lease = job.reserve('retrieval', Charge(Decimal(0)), 8, source='one', limits=limits, resource=f.url)
        lease.dispatch()
        code = """
import sys
import os
from app.accounting import Ledger, BudgetDenied, Charge
from app.contracts import SourceLimits
from app.storage import Storage
from datetime import datetime
from decimal import Decimal
db,project,now,url=sys.argv[1:]
ledger=Ledger(Storage(os.environ.get('TEST_DATABASE_URL') or db),project,fixture=True,clock=lambda:datetime.fromisoformat(now))
job=ledger.open_job('buyer-api')
try:
    job.reserve('retrieval',Charge(Decimal(0)),5,source='one',resource=url,
                limits=SourceLimits(requests_per_minute=6,concurrency=3,max_requests_per_run=6))
except BudgetDenied as exc:
    print(str(exc))
"""
        child = subprocess.run([sys.executable, '-c', code, str(self.ledger.storage.engine.url),
            self.ledger.project, f.now.isoformat(), f.url], check=True, capture_output=True, text=True)
        self.assertIn('Another process is fetching this resource', child.stdout)
        lease.complete(Decimal(0), 1)
        with self.assertRaisesRegex(BudgetDenied, 'job envelope'):
            job.reserve('model', Charge(Decimal('.001')), 2)
        control(self.ledger, False)
        self.assertTrue(job.cancelled)

    def test_only_changed_specification_passages_require_new_embeddings(self):
        f = self.fixture
        control(self.ledger, True)
        self.runner.embedding = f.fixture.route
        limits = self.runner.manifest.limits.model_copy(update={'max_paid_calls': 4, 'max_spend_usd': Decimal('.01')})
        self.runner.manifest = self.runner.manifest.model_copy(update={'limits':limits,'seeds':(f.fixture.seeds[0],)})
        signal(self.ledger,'phone',gap=False,catalog_urls=(f.url,))
        self.assertEqual(self.runner.run('offers')['embedding_calls'], 1)
        f.fixture.pages['one']['offers']['price'] = '98'
        f.now += timedelta(days=1)
        price = self.runner.run('offers')
        self.assertEqual((price['counts']['changed'], price['embedding_calls']), (1, 0))
        f.fixture.pages['one']['additionalProperty'][0]['value'] = '256 GB'
        f.now += timedelta(days=1)
        spec = self.runner.run('offers')
        self.assertEqual(spec['pages'][f.url]['changed_passages'], 1)
        self.assertEqual(spec['embedding_calls'], 1)

    def test_changed_policy_requires_an_explicit_reviewed_statement(self):
        f = self.fixture
        control(self.ledger, True)
        statements = ('Shipping takes three days.', 'Shipping takes five days.')
        seed = Seed(source_id='one', url=f.url, category='phone', content='policy', format='html',
                    policy_element_id='terms', policy_statements=statements)
        self.runner.manifest = f.manifest.model_copy(update={'seeds':(seed,)})
        f.banner = ('<p id="terms">'+statements[0]+'</p>').encode()
        self.assertEqual(self.runner.run('offers')['counts']['new'], 1)
        f.now += timedelta(days=7)
        f.banner = ('<p id="terms">'+statements[1]+'</p>').encode()
        page = self.runner.run('offers')['pages'][f.url]
        self.assertEqual((page['status'], page['changed_passages']), ('changed', 1))

    def test_catalog_adds_only_admitted_listing_then_price_failure_and_retirement(self):
        f = self.fixture
        control(self.ledger, True)
        url = 'https://one.example/new-phone'
        catalog_url = 'https://one.example/catalog'
        source = json.loads(f.fixture.registry.read_bytes())
        source['sources'][0]['allowed_endpoints'].extend((url, catalog_url))
        f.fixture.registry.write_text(json.dumps(source), encoding='utf-8')
        self.runner.manifest = f.manifest.model_copy(update={'seeds': (
            Seed(source_id='one', category='phone', format='catalog', content='listing',
                 url=catalog_url, category_slugs=('phones',)),)})
        product = dict(f.fixture.pages['one'], url=url, sku='NEW', **{'@id':url})
        rows = [dict(id=i, type='simple', parent=0, sku='NEW', name='Phone X',
                     categories=[dict(slug='phones')], permalink=u)
                for i,u in enumerate((url,'https://one.example/unadmitted'), 1)]
        status_code = 200
        def serve(request):
            is_catalog = str(request.url) == catalog_url
            body = (json.dumps(rows) if is_catalog else
                    '<script type="application/ld+json">'+json.dumps(product)+'</script>').encode()
            return httpx2.Response(status_code, headers={'content-type':'application/json' if is_catalog else 'text/html',
                'content-length':str(len(body))}, stream=httpx2.ByteStream(body))
        f.gate._transport = httpx2.MockTransport(serve)
        discovery = self.runner.run('catalog')
        self.assertEqual(discovery['enumerated_product_urls'], 2)
        self.assertEqual(status(self.ledger)['admitted_listing_urls'], 1)
        first = self.runner.run('offers')
        self.assertEqual((first['counts']['new'], first['exact_listings_extracted']), (1,1))
        signal(self.ledger,'phone',gap=False,catalog_urls=(url,))
        f.now += timedelta(days=1)
        product['offers']['price'] = '109.00'
        price = self.runner.run('offers')
        self.assertEqual(price['pages'][url]['changed_fields'], ['price'])
        self.assertEqual(price['pages'][url]['changed_passages'], 0)
        observed = f.now
        f.now += timedelta(days=1)
        status_code = 404
        failure = self.runner.run('offers')
        self.assertEqual(failure['counts']['failed'], 1)
        item, = self.runner.catalog.lookup(category='phone', listing_url=url)
        self.assertEqual(item.observations['price'].evidence[0].observed_at, observed)
        f.now += timedelta(hours=7)
        status_code = 200
        product['offers']['availability'] = 'https://schema.org/Discontinued'
        removed = self.runner.run('offers')
        self.assertEqual(removed['counts']['removed'], 1)
        self.assertEqual(tuple(self.runner.catalog.lookup(category='phone', listing_url=url)), ())
        self.assertEqual(self.runner.run('offers', force=True)['checked'], 0)
