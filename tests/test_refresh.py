from datetime import timedelta
import json
from unittest import TestCase

import httpx2

from app.accounting import BudgetDenied
from app.bootstrap import seed_target
from app.refresh import cadence, plan, refresh
from app.sources import CacheValidators, SourceGate, SourceLimiter
import test_domestic_catalog as catalog_fixtures
from test_corpus import START


class RefreshTests(TestCase):
    def setUp(self):
        f = catalog_fixtures.DomesticListingTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.fixture = f
        self.now = START
        f.ledger.clock = lambda: self.now
        data = json.loads(f.registry.read_text())
        data['live_release'] = {'admitted_source_ids': ['one', 'two']}
        f.registry.write_text(json.dumps(data), encoding='utf-8')
        self.requests = []
        self.status, self.tag, self.banner = 200, '"first"', b''
        self.gate = SourceGate._for_tests(f.registry, resolver=lambda *_: ['1.1.1.1'],
            clock=lambda: self.now.timestamp(), wall_clock=lambda: self.now, limiter=SourceLimiter(),
            transport=httpx2.MockTransport(self.reply))
        self.manifest = f.template.model_copy(update={'seeds': tuple(f.seeds)})
        self.url = str(f.seeds[0].url)

    def reply(self, request):
        self.requests.append(request)
        body = self.fixture.body(request.url.host.split('.')[0]) + self.banner if self.status == 200 else b''
        headers = {'Content-Type': 'text/html', 'Content-Length': str(len(body))}
        if self.tag:
            headers['ETag'] = self.tag
        return httpx2.Response(self.status, headers=headers, stream=httpx2.ByteStream(body))

    def run_batch(self, name, limit=1):
        f = self.fixture
        return refresh(self.manifest, ledger=f.ledger, corpus=f.corpus, gate=self.gate,
                       batch_id=name, limit=limit, high_use=(self.url,))

    def state(self):
        f = self.fixture
        key, _ = seed_target(f.seeds[0], f.corpus, f.registry)
        return f.corpus.state(key)

    def test_select_due_and_replay_original_batch_across_process_restart(self):
        f = self.fixture
        first = self.run_batch('first')
        self.assertEqual(first['plan']['selected'], [self.url])
        self.assertEqual(first['plan']['remaining_due'], 1)
        self.assertEqual((first['source_attempts'], first['spend_usd'], first['embedding_calls']), (1, '0', 0))
        self.assertEqual(first['pages'][self.url]['status'], 'new')
        self.assertEqual(self.state().validated_at, START)
        self.now += timedelta(hours=1)
        self.assertEqual(first, self.run_batch('first'))
        with self.assertRaisesRegex(ValueError, 'different refresh request'):
            self.run_batch('first', limit=2)
        self.assertEqual(len(self.requests), 1)
        snapshot = f.ledger.snapshot()
        self.assertEqual(snapshot['jobs']['refresh:first']['kind'], 'maintenance')
        self.assertEqual(snapshot['scopes']['maintenance:' + START.date().isoformat()][0], 1)
        for high, minimum, maximum in [(True, 12, 24), (False, 72, 168)]:
            interval = cadence(f.seeds[0], high)
            self.assertTrue(timedelta(hours=minimum) <= interval <= timedelta(hours=maximum))
            for delta, expected in [(timedelta(seconds=-1), 'deferred'), (timedelta(0), 'due')]:
                preview = plan(self.manifest, f.corpus, self.gate, now=START + interval + delta,
                               limit=1, high_use=[self.url] if high else [])
                self.assertEqual(preview['resources'][0]['state'], expected)
        self.assertEqual(len(self.requests), 1)

    def test_validators_banner_price_and_recipe_changes_keep_separate_evidence(self):
        self.manifest = self.manifest.model_copy(update={'seeds': self.manifest.seeds[:1]})
        self.run_batch('initial')
        self.now += timedelta(days=1)
        self.status = 304
        page = self.run_batch('304')['pages'][self.url]
        self.assertEqual((page['status'], page['extractions']), ('unchanged', 0))
        self.assertEqual(self.requests[-1].headers['if-none-match'], '"first"')
        self.assertEqual((self.state().validated_at, self.state().content_changed_at), (self.now, START))

        self.now += timedelta(days=1)
        self.status, self.tag, self.banner = 200, None, b'<footer>New banner</footer>'
        page = self.run_batch('banner')['pages'][self.url]
        self.assertEqual((page['status'], page['changed_fields'], page['changed_passages']), ('unchanged', [], 0))
        self.assertEqual(self.state().content_changed_at, START)
        self.now += timedelta(days=1)
        self.fixture.pages['one']['offers']['price'] = '125.00'
        page = self.run_batch('price')['pages'][self.url]
        self.assertNotIn('if-none-match', self.requests[-1].headers)
        self.assertEqual((page['status'], page['changed_fields'], page['changed_passages']), ('changed', ['price'], 0))
        facts = {f.evidence[0].field_path: f.evidence[0] for f in self.fixture.corpus.observations(self.state().document_id)}
        self.assertEqual((facts['price'].origin, facts['price'].observed_at), ('maintenance', self.now))
        self.assertEqual(facts['variant.product.model'].observed_at, START)

        self.now += timedelta(minutes=1)
        self.manifest = self.manifest.model_copy(update={'processing': self.manifest.processing.model_copy(
            update={'index_version': 'next-index'})})
        page = self.run_batch('reindex')['pages'][self.url]
        self.assertEqual((page['status'], page['changed_fields'], page['extractions']), ('reprocessed', [], 0))

    def test_failure_backoff_and_stale_reply(self):
        self.manifest = self.manifest.model_copy(update={'seeds': self.manifest.seeds[:1]})
        self.run_batch('first')
        self.now += timedelta(days=1)
        self.status = 404
        page = self.run_batch('404')['pages'][self.url]
        self.assertEqual((page['status'], page['errors']), ('failed', ['HTTP_ERROR']))
        self.assertEqual(self.state().validated_at, START)
        self.assertEqual(self.run_batch('backoff')['plan']['resources'][0]['state'], 'backoff')
        self.now += timedelta(hours=6)
        self.status, self.tag = 200, '"second"'
        self.fixture.pages['one']['offers']['price'] = '110'
        self.assertEqual(self.run_batch('after-backoff')['pages'][self.url]['status'], 'changed')
        self.now += timedelta(days=1)
        self.gate._wall_clock = lambda: START + timedelta(minutes=1)
        self.fixture.pages['one']['offers']['price'] = '5'
        self.assertEqual(self.run_batch('late')['pages'][self.url]['status'], 'superseded')
        self.assertGreater(self.state().validated_at, START + timedelta(days=1))

    def test_worker_lease_recovery_and_exhaustion(self):
        def interrupted(_):
            with self.assertRaisesRegex(BudgetDenied, 'Another worker'):
                self.run_batch('interrupted', limit=2)
            raise KeyboardInterrupt
        self.gate._transport = httpx2.MockTransport(interrupted)
        with self.assertRaises(KeyboardInterrupt):
            self.run_batch('interrupted', limit=2)
        self.gate._transport = httpx2.MockTransport(self.reply)
        self.now += timedelta(seconds=121)
        report = self.run_batch('interrupted', limit=2)
        self.assertEqual(report['counts'], {'interrupted': 1, 'new': 1, 'pending': 0})
        self.assertEqual(report['source_attempts'], 2)
        self.assertEqual(self.run_batch('interrupted', limit=2), report)
        self.now += timedelta(days=7)
        self.manifest = self.manifest.model_copy(update={'limits': self.manifest.limits.model_copy(
            update={'max_fetch_attempts': 1})})
        partial = self.run_batch('bounded', limit=2)
        self.assertEqual((partial['source_attempts'], partial['counts']['pending']), (1, 1))
        self.assertEqual(self.run_batch('bounded', limit=2), partial)

    def test_unscoped_or_unsolicited_304_and_disabled_retailer(self):
        self.status = 304
        page = self.run_batch('unsolicited')['pages'][self.url]
        self.assertEqual(page['status'], 'failed')
        self.assertNotIn('if-none-match', self.requests[-1].headers)
        self.assertEqual(CacheValidators(self.url, '"first"', None).headers(self.url + '?page=2'), {})
        self.assertEqual(CacheValidators(self.url, '*', 'invalid date').headers(self.url), {})
        f = self.fixture
        record = f.directory.get('one')
        f.directory.upsert(record.model_copy(update={'background_collection': False,
                                                    'updated_at': self.now + timedelta(seconds=1)}))
        preview = plan(self.manifest, f.corpus, self.gate, now=self.now, limit=1)
        self.assertEqual(preview['resources'][0]['state'], 'ineligible')
        self.assertEqual(len(self.requests), 1)

    def test_redirect_receives_only_its_own_validators(self):
        f = self.fixture
        final = self.url + '/final'
        registry = json.loads(f.registry.read_text())
        registry['sources'][0]['allowed_endpoints'].append(final)
        f.registry.write_text(json.dumps(registry), encoding='utf-8')
        seen = []
        def redirect_then_check(request):
            seen.append((str(request.url), request.headers.get('if-modified-since')))
            if str(request.url) == self.url:
                return httpx2.Response(302, headers={'location': final})
            return httpx2.Response(304)

        self.gate._transport = httpx2.MockTransport(redirect_then_check)
        budget = f.ledger.open_job('conditional', 'maintenance').source_budget(f.registry)
        modified = 'Mon, 16 Nov 2026 12:00:00 GMT'
        result = self.gate.fetch(budget, 'one', self.url, expect='html',
                                 validators=CacheValidators(final, None, modified))
        self.assertEqual((result.status_code, result.body, result.attempts), (304, None, 2))
        self.assertEqual(seen, [(self.url, None), (final, modified)])
