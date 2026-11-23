"""Automatic retailer onboarding (issue #99). Offline: fake Tavily, HTTP and model; synthetic shop."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import httpx2
from sqlalchemy import event

from app.accounting import Charge, Ledger
from app.collection_contracts import RetailerDirectoryEntry
from app.contracts_v2 import RunReference, ShoppingRequest, VerifiedOffer
from app.corpus_contracts import DocumentCapture, ResourceKey
from app.directory import Directory, DirectoryQuery, DirectoryRecord
from app.discovery import Discovery, Lead, Tavily
from app.eligibility import check_full_product
from app.extraction import PageProfile, extract_structured
from app.model_provider import ModelResult
from app import onboarding as ob
from app.source_capabilities import CapabilityRef
from app.sources import SourceGate, SourceLimiter
from app.storage import Storage, source_activations
from test_storage import migrate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'onboarding'
START = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
SHOP = 'https://cedartech.example'
SCOUT = SHOP + '/product/scout-book'
TRAIL = SHOP + '/product/trail-book'
SOURCE_ID = ob.source_id_for('cedartech.example')


def request(**changes):
    return ShoppingRequest.model_validate(dict(request_id='request-1',
        intent={'kind': 'exact_model', 'manufacturer': 'Example', 'model': 'Scout Book'},
        requirements={'category': 'laptop', 'min_ram_gb': 16},
        fulfillment={'mode': 'delivery', 'destination': {'country': 'LB', 'city': 'Beirut'}}) | changes)


class Shop:
    """A fake public shop behind the real SourceGate; it records every request it serves."""

    def __init__(self):
        self.pages = {SCOUT: (FIXTURES / 'scout-book.html').read_bytes(),
                      TRAIL: (FIXTURES / 'trail-book.html').read_bytes()}
        self.status, self.redirects, self.seen = {}, {}, []

    def handler(self, req):
        url = str(req.url)
        self.seen.append(url)
        if url in self.redirects:
            return httpx2.Response(302, headers={'location': self.redirects[url], 'content-length': '0'})
        status = self.status.get(url, 200)
        body = self.pages.get(url, b'<html>missing</html>') if status == 200 else b'error'
        if url not in self.pages and status == 200:
            status = 404
        return httpx2.Response(status, headers={'content-type': 'text/html; charset=utf-8',
                                                'content-length': str(len(body))}, stream=httpx2.ByteStream(body))


class OnboardingCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.url = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{Path(directory) / 'onboarding.db'}"
        migrate(self.url, 'base')
        migrate(self.url)
        self.storage = Storage(self.url, Path(directory) / 'evidence')
        self.addCleanup(migrate, self.url, 'base')
        self.addCleanup(self.storage.engine.dispose)
        self.now = START
        self.clock = lambda: self.now
        self.base = Path(directory) / 'sources.json'
        self.base.write_text(json.dumps({'sources': [], 'live_release': {'admitted_source_ids': []}}), encoding='utf-8')
        self.registry = ob.ActivationRegistry(self.storage, base=self.base, clock=self.clock)
        self.directory = Directory(self.storage, registry_path=self.registry)
        self.queue = ob.OnboardingQueue(self.storage, self.registry, directory=self.directory, clock=self.clock)
        self.ledger = Ledger(self.storage, 'onboarding-fixture', fixture=True, clock=self.clock)
        self.ledger.provision()
        self.shop = Shop()
        self.addresses = ['93.184.216.34']
        self.limiter = SourceLimiter()
        self.live = None  # a clock that moves on every call, like a real one

    def ticking(self):
        def tick():
            self.now += timedelta(microseconds=1)
            return self.now
        self.live = tick

    def gate(self, registry):
        # The gate's per-minute limiter runs on the same fake time as the ledger and the queue.
        return SourceGate._for_tests(registry, resolver=lambda *_: list(self.addresses),
                                     wall_clock=self.live or self.clock,
                                     clock=lambda: (self.now - START).total_seconds(),
                                     sleep=lambda _: None, limiter=self.limiter,
                                     transport=httpx2.MockTransport(self.shop.handler))

    def worker(self, proposer=None, **kwargs):
        return ob.OnboardingWorker(self.storage, self.ledger, registry=self.registry, queue=self.queue,
                                   proposer=proposer or ob.HeuristicProposer(), gate_factory=self.gate,
                                   directory=self.directory, clock=self.live or self.clock, **kwargs)

    def enqueue(self, *urls, req=None):
        return self.queue.enqueue_tavily([Lead(url, 'Scout Book', 'snippet only', .8) for url in urls], req or request())

    def proposal(self, **changes):
        data = dict(name='Cedar Tech', category='laptop',
                    samples=[{'url': SCOUT, 'sku': 'SB-16-512'}, {'url': SCOUT, 'sku': 'SB-32-1024'},
                             {'url': TRAIL, 'sku': 'TB-8-256'}],
                    operations=[{'operation': 'product', 'path': ['product', {'kind': 'token', 'max_length': 128}]},
                                {'operation': 'variant', 'path': ['product', {'kind': 'token', 'max_length': 128}]}])
        return ob.ConfigurationProposal.model_validate(data | changes)

    def onboard(self, proposer=None, **kwargs):
        self.enqueue(SCOUT, TRAIL)
        return self.worker(proposer, **kwargs).run()

    def exhaust_maintenance_time(self):
        job = self.ledger.open_job('other-maintenance', 'maintenance')
        for _ in range(15):  # 15 x 120 s fills the 30-minute daily maintenance allowance
            attempt = job.reserve('work', Charge(Decimal(0)), 120)
            attempt.dispatch()
            attempt.complete(Decimal(0), 120)


class FixedProposer(ob.HeuristicProposer):
    def __init__(self, configuration=None, targets=None):
        self.configuration, self.targets = configuration, targets

    def propose_targets(self, view, job):
        return ob.TargetChoice(urls=self.targets) if self.targets else super().propose_targets(view, job)

    def propose_configuration(self, view, pages, job):
        self.pages = pages
        return self.configuration or super().propose_configuration(view, pages, job)


class LeadIntakeTests(OnboardingCase):
    def test_leads_deduplicate_by_shop_domain_across_channels(self):
        first = self.enqueue(SCOUT)[0]
        self.assertEqual((first.status, first.lead_id), ('queued', 'cedartech.example'))
        self.assertEqual(self.enqueue('https://www.cedartech.example/product/scout-book')[0].status, 'merged')
        self.assertEqual(self.queue.enqueue_buyer([TRAIL], request())[0].status, 'merged')
        (state,) = self.queue.leads()
        self.assertEqual(state.record.mentions, 3)
        self.assertEqual(set(state.record.channels), {'tavily', 'buyer'})
        self.assertEqual(set(state.record.hosts), {'cedartech.example', 'www.cedartech.example'})
        self.assertEqual(state.record.source_id, SOURCE_ID)
        self.assertNotIn('Scout Book', json.dumps(state.record.model_dump(mode='json')))  # no buyer text kept

    def test_background_rotation_preserves_channel_attribution_in_the_onboarding_queue(self):
        from app import discover_retailers
        from app.domestic_discovery import DomesticDiscovery
        from app.search_connectors import Hit, Response

        connectors = {}
        for name in ('firecrawl', 'tavily'):
            connector = Mock(rate=Decimal('0.001'))
            connector.quote.return_value = connector.rate
            connector.fetch.return_value = Response((Hit(SCOUT, 'Public listing', score=.8),),
                                                    request_id=name + '-request', units=1)
            connectors[name] = connector
        tools = DomesticDiscovery(self.gate(self.registry), self.directory, connectors)
        output = io.StringIO()
        with patch.object(discover_retailers, 'load_env_file'), \
                patch.object(discover_retailers, 'Storage', return_value=self.storage), \
                patch.object(discover_retailers.Ledger, 'configured', return_value=self.ledger), \
                patch.object(discover_retailers.DomesticDiscovery, 'from_env', return_value=tools), \
                redirect_stdout(output):
            status = discover_retailers.main(['--job-id', 'domestic-cycle', '--query', 'حاسوب محمول',
                '--objective', 'Find a second seller', '--language', 'ar', '--category', 'laptop',
                '--cursor', '4', '--count', '2'])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['next_cursor'], 6)
        self.assertEqual([a['channel'] for a in result['report']['attempts']], ['firecrawl', 'tavily'])
        (state,) = self.queue.leads()
        self.assertEqual(state.record.mentions, 1)
        self.assertEqual(set(state.record.channels), {'firecrawl', 'tavily'})
        self.assertEqual(state.record.markets, ('LB',))
        self.assertNotIn('Find a second seller', state.record.model_dump_json())
        self.assertEqual(self.ledger.snapshot()['jobs']['domestic-cycle']['kind'], 'maintenance')
        self.assertFalse(self.registry.activations(status='active'))

    def test_unsafe_urls_are_rejected_before_storage(self):
        for url in ('http://cedartech.example/p', 'https://127.0.0.1/p', 'https://localhost.localhost/p',
                    'https://user:pw@cedartech.example/p', 'https://cedartech.example:8443/p',
                    'https://cedartech.example/cart/add', 'https://cedartech.example/p%2F..', 'ftp://x.example/p'):
            with self.subTest(url=url):
                self.assertEqual(self.queue.enqueue(url, channel='tavily', categories=('laptop',), reason='t').status,
                                 'rejected')
        self.assertEqual(self.queue.leads(), ())

    def test_market_and_category_scope_come_from_the_request(self):
        self.enqueue(SCOUT)
        self.assertEqual(self.queue.get('cedartech.example').record.markets, ('LB',))
        foreign = request(market_scope={'include_international': True, 'international_markets': ['US']})
        self.queue.enqueue_tavily([Lead('https://usshop.example/p/1', 't', 's', .5)], foreign)
        self.assertEqual(self.queue.get('usshop.example').record.markets, ('LB',))
        self.assertEqual(self.queue.enqueue(SCOUT, channel='buyer', categories=('laptop',),
            markets=('US',), reason='overseas only').status, 'rejected')
        unsupported = self.queue.enqueue(SCOUT, channel='tavily', categories=('toaster',), reason='t')
        self.assertEqual(unsupported.status, 'rejected')

    def test_reviewed_and_marketplace_domains_are_not_onboarded_automatically(self):
        self.base.write_text(json.dumps({'live_release': {'admitted_source_ids': ['known']}, 'sources': [
            {'id': 'known', 'origin': 'https://known.example', 'status': 'active'},
            {'id': 'revoked', 'origin': 'https://revoked.example', 'status': 'revoked'},
            {'id': 'pending', 'origin': 'https://pending.example', 'status': 'pending'}]}), encoding='utf-8')
        status = lambda url: self.queue.enqueue(url, channel='tavily', categories=('laptop',), reason='t').status
        self.assertEqual(status('https://known.example/p/1'), 'configured')
        self.assertEqual(status('https://revoked.example/p/1'), 'excluded')
        self.assertEqual(status('https://pending.example/p/1'), 'queued')
        self.directory.upsert(DirectoryRecord(entry=RetailerDirectoryEntry(retailer_id='bazaar', name='Bazaar',
            origin='https://bazaar.example', markets=('LB',), categories=('laptop',), discovery_status='discovered'),
            updated_at=START, source_id='bazaar', marketplace_id='bazaar', seller_identity='marketplace'))
        self.assertEqual(status('https://bazaar.example/p/1'), 'excluded')
        (exception,) = self.queue.exceptions()
        self.assertEqual(exception.record.exception.code, 'marketplace')

    def test_priority_prefers_coverage_gaps_mentions_and_buyer_demand(self):
        record = lambda **c: ob.LeadRecord.model_validate(dict(lead_id='a.example', source_id='auto-a-example',
            hosts=['a.example'], categories=['laptop'], markets=['LB']) | c)
        gap = ob.OnboardingQueue.priority(record(), {('laptop', 'LB'): 0})
        covered = ob.OnboardingQueue.priority(record(), {('laptop', 'LB'): 3})
        self.assertGreater(gap, covered)
        self.assertGreater(ob.OnboardingQueue.priority(record(mentions=3, channels=['buyer']), {}), gap)
        self.queue.enqueue('https://low.example/p/1', channel='tavily', categories=('laptop',), relevance=.1, reason='t')
        self.queue.enqueue('https://high.example/p/1', channel='buyer', categories=('laptop',), relevance=.9, reason='t')
        self.assertEqual(self.queue.claim().lead_id, 'high.example')

    def test_directory_leads_without_configuration_are_queued(self):
        self.directory.upsert(DirectoryRecord(entry=RetailerDirectoryEntry(retailer_id='cedar_tech', name='Cedar Tech',
            origin=SHOP, markets=('LB',), categories=('laptop',), discovery_status='discovered'), updated_at=START,
            source_id='cedar_tech', seller_identity='unresolved',
            product_urls=(SCOUT, 'https://marketplace.example/item/scout-book')))
        (result,) = self.queue.enqueue_directory(DirectoryQuery(category='laptop'))  # its own domain only
        self.assertEqual(result.status, 'queued')
        record = self.queue.get('cedartech.example').record
        self.assertEqual((record.channels, record.retailer_id, record.name_hint), (('directory',), 'cedar_tech', 'Cedar Tech'))
        self.assertFalse(self.directory.select(DirectoryQuery(category='laptop')))
        # After onboarding, the same directory record becomes a configured source with tested coverage.
        self.queue.enqueue_buyer([TRAIL], request())
        (outcome,) = ob.OnboardingWorker(self.storage, self.ledger, registry=self.registry, queue=self.queue,
            proposer=ob.HeuristicProposer(), gate_factory=self.gate, directory=self.directory, clock=self.clock).run()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        (selection,) = self.directory.select(DirectoryQuery(category='laptop'))
        self.assertTrue(selection.configured)
        self.assertEqual(set(selection.permitted_urls), {SCOUT, TRAIL})
        record = self.directory.get('cedar_tech')
        self.assertEqual((record.source_id, record.seller_identity, record.seller_id), (SOURCE_ID, 'retailer', 'cedar_tech'))
        self.assertTrue(record.coverage[0].retrieval_tested and not record.coverage[0].qualifying_offer)


class ActivationTests(OnboardingCase):
    def test_newly_activated_shop_can_supply_a_domestic_finalist_without_a_registry_edit(self):
        from app.domestic_buyer import current_problem, domestic_assessment
        from app.domestic_buyer_sources import DomesticBuyerSources
        outcome, = self.onboard()
        self.assertEqual(outcome.status,'active')
        url = SHOP+'/product/new-book'
        page = {'@type':'Product','name':'Scout Book','brand':'Example','model':'Scout Book',
                'category':'laptop','sku':'SB-NEW','productID':'scout-new','url':url,
                'additionalProperty':[{'name':'RAM','value':'16 GB'},{'name':'Purchasable','value':True}],
                'offers':{'@type':'Offer','price':'900.25','priceCurrency':'USD',
                    'availability':'https://schema.org/InStock','itemCondition':'https://schema.org/NewCondition',
                    'seller':{'name':'Cedar Tech'},'priceSpecification':{'valueAddedTaxIncluded':True},
                    'shippingDetails':{'shippingOrigin':{'addressCountry':'LB'},'shippingDestination':{'addressCountry':'LB'},
                                       'shippingRate':{'currency':'USD','value':'10'}}}}
        self.shop.pages[url] = ('<script type="application/ld+json">'+json.dumps(page)+'</script>').encode()
        self.now += timedelta(minutes=2)
        job = self.ledger.open_job('later-finalist')
        job.enter_final_phase()
        req = request(intent=dict(kind='product_link',url=url))
        run = RunReference(run_id=job.run_id,run_revision=1,request_id=req.request_id,request_revision=req.revision)
        reader = DomesticBuyerSources(self.storage,self.gate(self.registry),self.directory)
        read = reader.read(req,url,job,run,room=lambda:30)
        self.assertEqual(read.status,'ok',read.reason)
        offer, = read.offers
        assessment = domestic_assessment(req,offer,self.now)
        self.assertIsNone(current_problem(offer,assessment,read,run,self.now,self.now))
        verified = VerifiedOffer(offer=offer,cost=assessment.cost,verification=dict(run=run,checked_at=self.now))
        self.assertEqual(str(verified.cost.total.amount),'910.25')
        self.assertEqual(json.loads(self.base.read_bytes())['sources'],[])

    def test_tavily_lead_activates_and_a_later_buyer_search_verifies_its_offer(self):
        # A buyer search finds only an unconfigured lead and queues it for background onboarding.
        tavily_calls = []
        def tavily_api(req):
            tavily_calls.append(json.loads(req.content))
            return httpx2.Response(200, json={'results': [{'url': SCOUT, 'title': 'Scout Book', 'score': .9,
                'content': 'UNVERIFIED snippet: $1 in stock'}], 'usage': {'credits': 1}})
        tavily = Tavily('synthetic-key', usd_per_credit=Decimal('0.008'), transport=httpx2.MockTransport(tavily_api))
        buyer_gate = self.gate(self.registry)
        first = self.ledger.open_job('run-first')
        report = Discovery(tavily, self.directory, buyer_gate).discover(request(), first)
        self.assertEqual((report.coverage.discovered, self.shop.seen), (0, []))
        self.assertEqual(self.queue.enqueue_tavily(report.leads, request())[0].status, 'queued')
        self.queue.enqueue_buyer([TRAIL], request())

        # Background onboarding probes, validates and activates without any config edit.
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        self.assertEqual({c for c, _, o in outcome.capabilities if o == 'activated'}, {'product-page', 'variant-page'})
        self.assertEqual(sorted(self.shop.seen), [SCOUT, TRAIL])
        self.assertEqual(json.loads(self.base.read_text()), {'sources': [], 'live_release': {'admitted_source_ids': []}})
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        self.assertEqual((product.market, product.seller, product.category), ('LB', 'Cedar Tech', 'laptop'))
        self.assertEqual(product.scope['variant_case'], 'passed')
        self.assertIn('costs.order.tax', product.scope['unknown_terms'])
        self.assertGreaterEqual(product.cost['fetch_attempts'], 2)
        self.assertEqual(product.cost['usd'], '0')
        self.assertTrue(any(e.field_path == 'price' and e.response_sha256 for e in product.evidence))
        self.assertEqual(self.queue.get('cedartech.example').status, 'active')

        # A later buyer search reaches the new shop through the normal gate and evidence rules.
        self.now += timedelta(minutes=5)
        later = self.ledger.open_job('run-later')
        report = Discovery(tavily, self.directory, buyer_gate).discover(request(), later)
        row = report.retailers[SOURCE_ID]
        self.assertEqual(row.status, 'checked', row.reason)
        self.assertEqual(report.coverage.qualifying, 0)  # discovery alone never qualifies an offer

        # Finalist verification in the final phase re-reads the listing through the activated capability.
        later.enter_final_phase()
        self.now += timedelta(seconds=10)
        final = buyer_gate.fetch(later.source_budget(self.registry), SOURCE_ID, SCOUT, expect='html',
                                 capability=CapabilityRef(capability_id='product-page', version=product.capability.version))
        self.assertEqual(final.status, 'ok')
        run = RunReference(run_id='run-later', run_revision=1, request_id='request-1', request_revision=1)
        verified = self.verified_offer(final, run)
        self.assertEqual(check_full_product(request(), verified.offer).outcome, 'pass')
        report.qualify(verified)
        self.assertEqual(report.coverage.qualifying, 1)

    def verified_offer(self, final, run):
        capture = DocumentCapture(key=ResourceKey(source_id=SOURCE_ID, url=SCOUT, representation='html',
            product_id='scout-book', variant_id='SB-16-512'), final_url=final.final_url, visibility='public',
            origin='runtime', observed_at=final.observed_at, checked_at=final.observed_at, body=final.body,
            relevant_content=final.body, processing=ob.PROCESSING)
        fields = {o.evidence[0].field_path: o for o in extract_structured(
            capture, PageProfile(SOURCE_ID, SHOP, sku='SB-16-512'), run=run).observations}
        raw = json.loads((ROOT / 'tests/fixtures/v2/workspace.json').read_text())['current']['recommendations'][0]['primary']
        offer = raw['offer']
        offer.update(offer_id='cedar-sb-16-512', retailer_id=SOURCE_ID, source_id=SOURCE_ID, listing_url=SCOUT,
                     source_product_id='scout-book', source_variant_id='SB-16-512')
        offer['seller'] = {'seller_id': SOURCE_ID, 'name': 'Cedar Tech', 'market': 'LB'}
        offer['variant']['product'].update(manufacturer='Example', model='Scout Book')
        # Route, purchasability and cost evidence remain separate supplementary synthetic receipts.
        for receipt in offer['evidence']:
            receipt.update(source_id=SOURCE_ID, product_id='scout-book', variant_id='SB-16-512',
                           origin_url=SCOUT, final_url=SCOUT, run=run.model_dump(mode='json'), origin='runtime',
                           observed_at=final.observed_at.isoformat())
        offer['evidence'] = [r for r in offer['evidence'] if r['field_path'] not in ('price', 'stock', 'condition')]
        for field in ('price', 'stock', 'condition'):
            offer[field] = fields[field].fact.model_dump(mode='json')
            offer['evidence'] += [r.model_dump(mode='json') for r in fields[field].evidence]
        raw['verification'] = {'checked_at': (final.observed_at + timedelta(seconds=1)).isoformat(),
                               'run': run.model_dump(mode='json')}
        raw['cost']['total']['amount'] = str(Decimal('900.25') + Decimal('10'))
        return VerifiedOffer.model_validate(raw)

    def test_probe_uses_the_gate_and_is_counted_in_shared_limits(self):
        (outcome,) = self.onboard()
        self.assertEqual(outcome.status, 'active')
        scopes = self.ledger.snapshot()['scopes']
        day = next(k for k in scopes if k.startswith('maintenance:'))
        self.assertEqual(scopes[day][0], 2)  # two probe fetches in today's maintenance allowance
        kinds = [a['kind'] for a in self.ledger.snapshot()['attempts'].values()
                 if a['job'].startswith('onboarding:cedartech.example:')]
        self.assertEqual((kinds.count('retrieval'), kinds.count('work')), (2, 3))  # parsing and validation are metered
        self.assertEqual(scopes['project'][0], 2)
        self.assertNotIn('bootstrap', scopes)

    def test_replay_and_parallel_activation_record_one_capability_version(self):
        self.onboard()
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        plan = ob.ActivationPlan(SOURCE_ID, 'cedartech.example', (product,))
        self.assertEqual(self.registry.activate(plan)[0][2], 'already_active')
        results, errors = [], []
        def activate():
            try:
                results.append(self.registry.activate(plan)[0][2])
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=activate) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((errors, set(results)), ([], {'already_active'}))
        with self.storage.engine.connect() as db:
            rows = db.execute(source_activations.select().where(source_activations.c.source_id == SOURCE_ID)).all()
        self.assertEqual(sorted(r.capability_id for r in rows), ['product-page', 'variant-page'])
        # A rerun of the whole lead also adds nothing.
        self.assertEqual(self.queue.enqueue_buyer([SCOUT], request())[0].status, 'configured')

    def test_parallel_fresh_activation_of_the_same_plan_inserts_once(self):
        self.onboard()
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        other = ob.source_id_for('other.example')
        fresh = product.model_copy(update={'source_id': other, 'origin': 'https://other.example'})
        plan = ob.ActivationPlan(other, 'other.example', (fresh,))
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.registry.activate(plan)[0][2])) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ['activated', 'already_active', 'already_active', 'already_active'])
        self.assertEqual(len(self.registry.activations(other)), 1)

    def test_a_new_version_supersedes_and_suspension_keeps_history(self):
        self.onboard()
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        gate = self.gate(self.registry)
        ref = CapabilityRef(capability_id='product-page', version=product.capability.version)
        job = self.ledger.open_job('run-suspend')
        self.assertEqual(gate.fetch(job.source_budget(self.registry), SOURCE_ID, TRAIL, expect='html', capability=ref).status, 'ok')
        self.assertTrue(self.registry.suspend(SOURCE_ID, 'product-page', reason='Listing layout changed'))
        self.assertTrue(self.registry.suspend(SOURCE_ID, 'variant-page', reason='Listing layout changed'))
        refused = gate.fetch(job.source_budget(self.registry), SOURCE_ID, TRAIL, expect='html', capability=ref)
        self.assertEqual(refused.errors[0].code, 'SOURCE_DISABLED')
        (kept,) = [a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product']
        self.assertEqual((kept.status, kept.changes[-1]['reason']), ('suspended', 'Listing layout changed'))
        self.assertTrue(kept.evidence)
        kinds = [e['kind'] for e in self.registry.history(source_id=SOURCE_ID)]
        self.assertEqual((kinds.count('activated'), kinds.count('suspended')), (2, 2))
        # The suspended identical scope is not silently reactivated by a replay.
        plan = ob.ActivationPlan(SOURCE_ID, 'cedartech.example', (kept.model_copy(update={'status': 'active'}),))
        self.assertEqual(self.registry.activate(plan)[0][2], 'kept_suspended')
        # A different validated scope is a new version and supersedes nothing that is suspended.
        wider = kept.capability.model_copy(update={'version': 'v1-other', 'trailing_slash': False})
        newer = kept.model_copy(update={'capability': wider, 'status': 'active'})
        self.assertEqual(self.registry.activate(ob.ActivationPlan(SOURCE_ID, 'cedartech.example', (newer,)))[0][2], 'activated')
        self.assertEqual(len(self.registry.activations(SOURCE_ID, 'active')), 1)

    def test_recheck_suspends_a_capability_that_no_longer_validates(self):
        self.onboard()
        worker = self.worker()
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in worker.recheck(SOURCE_ID)), {'valid'})
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(b'"price":"640.00",', b'')
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in worker.recheck(SOURCE_ID)), {'suspended'})
        self.assertEqual(self.registry.activations(SOURCE_ID, 'active'), ())
        self.assertNotIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])
        self.assertEqual(len(self.registry.activations(SOURCE_ID, 'suspended')), 2)

    def test_recheck_keeps_capabilities_on_transient_failure(self):
        self.onboard()
        self.shop.status[SCOUT] = self.shop.status[TRAIL] = 503
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in self.worker().recheck(SOURCE_ID)), {'transient'})
        self.assertEqual(len(self.registry.activations(SOURCE_ID, 'active')), 2)

    def test_activation_is_visible_to_another_process_without_a_config_edit(self):
        api_storage = Storage(self.url, self.storage.snapshots.root)  # a separate engine, as in the API process
        self.addCleanup(api_storage.engine.dispose)
        api_registry = ob.ActivationRegistry(api_storage, base=self.base, clock=self.clock)
        api_gate = self.gate(api_registry)
        self.assertRaises(ValueError, api_gate.admitted_policy, SOURCE_ID)
        self.onboard()
        policy = api_gate.admitted_policy(SOURCE_ID)
        self.assertEqual({c.capability_id for c in policy.endpoint_capabilities}, {'product-page', 'variant-page'})
        self.assertEqual(policy.limits.max_requests_per_run, 6)
        self.registry.suspend(SOURCE_ID, 'product-page', reason='synthetic suspension')
        self.registry.suspend(SOURCE_ID, 'variant-page', reason='synthetic suspension')
        self.assertRaises(ValueError, api_gate.admitted_policy, SOURCE_ID)

    def test_probe_scope_is_short_lived_and_exact(self):
        probe = ob.ProbeRegistry(source_id=SOURCE_ID, name='Cedar Tech', origin=SHOP, targets=(SCOUT,),
                                 expires_at=START + ob.PROBE_TTL, clock=self.clock)
        gate, job = self.gate(probe), self.ledger.open_job('probe-scope', 'maintenance')
        budget = job.source_budget(probe)
        self.assertEqual(gate.fetch(budget, SOURCE_ID, TRAIL, expect='html').errors[0].message.split()[0], 'blocked:url')
        self.assertEqual(gate.fetch(budget, SOURCE_ID, SCOUT, expect='html').status, 'ok')
        self.now = START + ob.PROBE_TTL
        self.assertEqual(gate.fetch(budget, SOURCE_ID, SCOUT, expect='html').errors[0].code, 'SOURCE_DISABLED')
        self.now = START
        probe.close()
        self.assertEqual(gate.fetch(budget, SOURCE_ID, SCOUT, expect='html').errors[0].code, 'SOURCE_DISABLED')
        self.assertEqual(self.shop.seen, [SCOUT])

    def test_reviewed_registry_wins_over_an_onboarded_entry(self):
        self.onboard()
        self.assertIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])
        self.base.write_text(json.dumps({'live_release': {'admitted_source_ids': []}, 'sources': [
            {'id': 'cedar', 'origin': 'https://cedartech.example', 'status': 'revoked'}]}), encoding='utf-8')
        self.assertNotIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])

    def test_policy_text_is_preserved_and_missing_terms_stay_unknown(self):
        proposer = FixedProposer(self.proposal(policy_element_id='shipping-policy'))
        (outcome,) = self.onboard(proposer)
        self.assertEqual(outcome.status, 'active')
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        policy = [e for e in product.evidence if e.field_path == 'policy.shipping']
        self.assertTrue(policy and all(e.scope == 'source' for e in policy))
        self.assertEqual(product.scope['unknown_terms'], ['policy.warranty', 'policy.returns', 'costs.order.tax'])

    def test_page_text_cannot_widen_scope_or_add_targets(self):
        proposer = FixedProposer()
        (outcome,) = self.onboard(proposer)
        self.assertEqual(outcome.status, 'active')
        self.assertTrue(all('evil' not in url and '127.0.0.1' not in url for url in self.shop.seen))
        summaries = json.dumps([p.model_dump(mode='json') for p in proposer.pages])
        self.assertNotIn('Ignore previous', summaries)  # descriptions and body text are not passed on
        for activation in self.registry.activations(SOURCE_ID):
            self.assertEqual(activation.capability.host, 'cedartech.example')
            self.assertFalse(activation.capability.matches('https://evil.example/admin'))
            self.assertFalse(activation.capability.matches(SHOP + '/my-account/orders'))

    def test_each_operation_is_validated_independently(self):
        token = {'kind': 'token', 'max_length': 128}
        proposer = FixedProposer(self.proposal(operations=[
            {'operation': 'product', 'path': ['product', token]},
            {'operation': 'catalog', 'path': ['shop', token]},
            {'operation': 'variant', 'path': ['offers', token]}]))
        (outcome,) = self.onboard(proposer)
        self.assertEqual([c for c, _, _ in outcome.capabilities], ['product-page'])
        rejected = {r['operation']: r['reason'] for r in self.registry.activations(SOURCE_ID)[0].scope['rejected_operations']}
        self.assertIn('teammate', rejected['catalog'])
        self.assertIn('does not cover', rejected['variant'])

    def test_scope_must_stay_narrow_even_when_it_matches(self):
        from hashlib import sha256
        deep = {SHOP + '/c/laptops/x1/scout-book': self.shop.pages[SCOUT],
                SHOP + '/c/monitors/x2/trail-book': self.shop.pages[TRAIL]}
        pages = {url: ob.FetchOutcome(SOURCE_ID, url, (url,), 1, 1, final_url=url, status_code=200,
                                      content_type='text/html', body=body, response_sha256=sha256(body).hexdigest(),
                                      observed_at=START) for url, body in deep.items()}
        token = {'kind': 'token', 'max_length': 128}
        samples = [{'url': url, 'sku': sku} for url, sku in zip(deep, ('SB-16-512', 'TB-8-256'))]
        def decide(path, query=()):
            proposal = self.proposal(samples=samples, operations=[{'operation': 'product', 'path': path, 'query': query}])
            return ob.validate_configuration(proposal, pages, source_id=SOURCE_ID, host='cedartech.example',
                                             market_hints=('LB',), categories=('laptop',)).decisions[0]
        broad = decide(['c', token, token, token])
        self.assertFalse(broad.approved)
        self.assertIn('broader than a product page', broad.reason)
        self.assertTrue(ob.EndpointCapability(capability_id='x', version='1', host='cedartech.example',
            operation='product', expect='html', path=('c', token, token, token)).matches(next(iter(deep))))
        search = {'name': 'q', 'parameter': {'kind': 'search_text', 'max_length': 64}, 'required': True}
        optional = {'name': 'ref', 'parameter': token, 'required': False}
        for query in ([search], [optional]):
            with self.subTest(query=query):
                self.assertIn('broader', decide(['c', token], query).reason)  # one parameter each: the rule itself decides

    def test_variant_operation_needs_a_variant_case(self):
        proposer = FixedProposer(self.proposal(samples=[{'url': SCOUT, 'sku': 'SB-16-512'},
                                                        {'url': TRAIL, 'sku': 'TB-8-256'}]))
        (outcome,) = self.onboard(proposer)
        self.assertEqual([c for c, _, _ in outcome.capabilities], ['product-page'])
        product = self.registry.activations(SOURCE_ID)[0]
        self.assertEqual(product.scope['variant_case'], 'single_sku_sampled')


class FailureTests(OnboardingCase):
    def failure(self, lead='cedartech.example'):
        state = self.queue.get(lead)
        self.assertEqual(state.status, 'exception')
        return state.record.exception

    def test_ambiguous_variant_is_rejected_with_evidence(self):
        page = self.shop.pages[SCOUT]
        duplicate = page.replace(b'"price":"900.25"', b'"price":"899.00"').split(b'<script type="application/ld+json">')[1]
        self.shop.pages[SCOUT] = page.replace(b'</head>', b'<script type="application/ld+json">' + duplicate + b'</head>', 1)
        (outcome,) = self.onboard()
        self.assertEqual(outcome.status, 'exception')
        failure = self.failure()
        self.assertEqual(failure.code, 'ambiguous_variant')
        self.assertEqual(set(failure.urls), {SCOUT, TRAIL})
        self.assertTrue(failure.fetches and all(f['response_sha256'] for f in failure.fetches))
        self.assertTrue(any(e.field_path == 'price' for e in failure.evidence))
        self.assertEqual(self.registry.activations(), ())

    def test_unknown_sku_and_missing_stock_are_rejected(self):
        unknown = self.proposal(samples=[{'url': SCOUT, 'sku': 'SB-64'}, {'url': TRAIL, 'sku': 'TB-8-256'}])
        self.onboard(FixedProposer(unknown))
        self.assertEqual((self.failure().code, self.failure().reason),
                         ('validation', 'The sampled page does not expose the proposed SKU'))
        self.queue.reopen('cedartech.example', note='synthetic retest')
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(b'"availability":"https://schema.org/InStock",', b'')
        self.worker().run()
        self.assertEqual(self.failure().code, 'validation')
        self.assertIn('stock', self.failure().reason)
        self.assertEqual(self.registry.activations(), ())

    def test_too_few_listings_are_not_enough_evidence(self):
        self.onboard(FixedProposer(self.proposal(samples=[{'url': SCOUT, 'sku': 'SB-16-512'},
                                                          {'url': SCOUT, 'sku': 'SB-32-1024'}])))
        self.assertEqual(self.failure().code, 'no_samples')

    def test_third_party_seller_and_missing_identity_need_a_teammate(self):
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(b'"seller":{"@type":"Organization","name":"Cedar Tech"}',
                                                                b'"seller":{"@type":"Organization","name":"Other Seller"}')
        self.onboard()
        self.assertEqual(self.failure().code, 'identity')
        self.assertIn('another seller', self.failure().reason)
        self.queue.reopen('cedartech.example', note='synthetic retest')
        self.shop.pages[TRAIL] = (FIXTURES / 'trail-book.html').read_bytes().replace(b'"addressCountry":"Lebanon"',
                                                                                    b'"addressCountry":"France"')
        self.shop.pages[SCOUT] = self.shop.pages[SCOUT].replace(b'"addressCountry":"LB"', b'"addressCountry":"FR"')
        self.worker().run()
        self.assertIn('market', self.failure().reason)

    def test_unsafe_proposed_targets_are_never_fetched(self):
        for targets in (('https://evil.example/product/x',), (SCOUT, 'https://www.cedartech.example/product/x'),
                        ('https://127.0.0.1/product/x',)):
            with self.subTest(targets=targets):
                self.shop.seen.clear()
                for state in self.queue.leads():
                    self.queue.reopen(state.lead_id, note='retest') if state.status == 'exception' else None
                if not self.queue.leads():
                    self.enqueue(SCOUT, TRAIL)
                (outcome,) = self.worker(FixedProposer(targets=targets)).run()
                self.assertEqual((outcome.status, self.failure().code), ('exception', 'unsafe_scope'))
                self.assertEqual(self.shop.seen, [])

    def test_private_address_redirect_and_size_limits_block_the_probe(self):
        self.addresses = ['10.0.0.7']
        self.onboard()
        self.assertEqual(self.failure().code, 'blocked')
        self.assertEqual(self.shop.seen, [])
        self.assertIn('BLOCKED', self.failure().reason)
        self.addresses = ['93.184.216.34']
        self.queue.reopen('cedartech.example', note='retest redirect')
        self.shop.redirects[SCOUT] = SHOP + '/my-account/login'
        self.shop.redirects[TRAIL] = 'https://elsewhere.example/product/trail-book'
        self.worker().run()
        self.assertEqual(self.failure().code, 'blocked')
        self.assertEqual(set(self.shop.seen), {SCOUT, TRAIL})
        self.shop.redirects.clear()
        self.queue.reopen('cedartech.example', note='retest size')
        self.shop.pages = {url: b'<html>' + b'x' * (2 * 1024 * 1024 + 1) + b'</html>' for url in self.shop.pages}
        self.worker().run()
        self.assertIn('BLOCKED', self.failure().reason)
        self.assertTrue(all(f['error_code'] == 'BLOCKED' for f in self.failure().fetches))

    def test_blocked_source_is_a_teammate_exception(self):
        self.shop.status = {SCOUT: 403, TRAIL: 404}
        (outcome,) = self.onboard()
        self.assertEqual((outcome.status, self.failure().code), ('exception', 'blocked'))
        self.assertEqual({f['error_code'] for f in self.failure().fetches}, {'HTTP_ERROR'})

    def test_managed_fallback_reads_a_blocked_page_within_probe_scope(self):
        shop = self.shop
        class Fallback:
            def __init__(self, gate):
                self.gate, self.calls = gate, []
            def scrape(self, job, source_id, url, *, capability=None, international=False):
                self.gate.check_provider_target(source_id, url, expect='html', capability=capability)
                self.calls.append(url)
                body = shop.pages[url]
                from hashlib import sha256
                outcome = ob.FetchOutcome(source_id, url, (url,), 1, 1, final_url=url, status_code=200,
                    content_type='text/html', body=body, response_sha256=sha256(body).hexdigest(), observed_at=START)
                return type('Managed', (), {'outcome': outcome})()
        self.shop.status = {SCOUT: 403}
        made = []
        (outcome,) = self.onboard(fallback_factory=lambda gate: made.append(Fallback(gate)) or made[-1])
        self.assertEqual(outcome.status, 'active', outcome.reason)
        self.assertEqual(made[0].calls, [SCOUT])

    def test_evidence_must_cite_the_reply_it_came_from(self):
        shop = self.shop
        class MismatchedFallback:
            def __init__(self, gate):
                self.gate = gate
            def scrape(self, job, source_id, url, *, capability=None, international=False):
                body = shop.pages[url]
                outcome = ob.FetchOutcome(source_id, url, (url,), 1, 1, final_url=url, status_code=200,
                    content_type='text/html', body=body, response_sha256='0' * 64, observed_at=START)
                return type('Managed', (), {'outcome': outcome})()
        self.shop.status = {SCOUT: 403}
        self.onboard(fallback_factory=MismatchedFallback)
        failure = self.failure()
        self.assertEqual((failure.code, failure.reason),
                         ('validation', 'Price or stock evidence is not bound to the sampled reply'))
        self.assertEqual(self.registry.activations(), ())

    def test_transient_failures_retry_with_bounded_backoff_then_surface(self):
        self.shop.status = {SCOUT: 503, TRAIL: 503}
        (first,) = self.onboard()
        state = self.queue.get('cedartech.example')
        self.assertEqual((first.status, state.status, state.attempts), ('retry', 'retry', 1))
        self.assertEqual(state.next_attempt_at, START + ob.RETRY_DELAYS[0])
        self.assertEqual(self.worker().run(), ())  # not due yet
        self.now = state.next_attempt_at
        (second,) = self.worker().run()
        self.assertEqual(self.queue.get('cedartech.example').next_attempt_at, self.now + ob.RETRY_DELAYS[1])
        self.now = self.queue.get('cedartech.example').next_attempt_at
        (third,) = self.worker().run()
        failure = self.failure()
        self.assertEqual((third.status, failure.code), ('exception', 'retry_limit'))
        self.assertEqual(set(failure.urls), {SCOUT, TRAIL})
        self.assertEqual(len(self.shop.seen), 3 * 2 * 3)  # 3 attempts x 2 pages x (1 + 2 gate retries)

    def test_transient_failure_recovers_on_the_next_attempt(self):
        self.shop.status = {SCOUT: 503, TRAIL: 503}
        self.onboard()
        self.shop.status = {}
        self.now += ob.RETRY_DELAYS[0]
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active')

    def test_budget_exhaustion_defers_without_spending_an_attempt(self):
        self.enqueue(SCOUT, TRAIL)
        self.queue.enqueue('https://second.example/p/1', channel='tavily', categories=('laptop',), reason='t')
        self.exhaust_maintenance_time()
        outcomes = self.worker().run()
        self.assertEqual([o.status for o in outcomes], ['deferred'])  # the run stops at the first refusal
        state = self.queue.get(outcomes[0].lead_id)
        self.assertEqual((state.status, state.attempts, state.record.deferrals), ('retry', 0, 1))
        self.assertEqual(state.next_attempt_at, datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual(self.shop.seen, [])
        self.assertEqual(self.registry.activations(), ())

    def test_busy_background_slots_pause_briefly(self):
        self.enqueue(SCOUT, TRAIL)
        other = self.ledger.open_job('other-maintenance', 'maintenance')
        held = [other.reserve('work', Charge(Decimal(0)), 60) for _ in range(2)]  # two live background slots
        (outcome,) = self.worker().run()
        state = self.queue.get('cedartech.example')
        self.assertEqual((outcome.status, state.attempts, state.next_attempt_at), ('deferred', 0, START + ob.BUSY_PAUSE))
        for attempt in held:
            attempt.release()
        self.now += ob.BUSY_PAUSE
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active')

    def test_old_scope_after_a_deferral_keeps_both_jobs_domestic(self):
        self.enqueue(SCOUT, TRAIL)
        other = self.ledger.open_job('other-maintenance', 'maintenance')
        held = [other.reserve('work', Charge(Decimal(0)), 60) for _ in range(2)]
        self.assertEqual(self.worker().run()[0].status, 'deferred')
        foreign = request(market_scope={'include_international': True, 'international_markets': ['US']})
        self.assertEqual(self.queue.enqueue_tavily([Lead(SCOUT, 't', 's', .5)], foreign)[0].status, 'merged')
        for attempt in held:
            attempt.release()
        self.now += ob.BUSY_PAUSE
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        jobs = [j for j in self.ledger.snapshot()['jobs'] if j.startswith('onboarding:cedartech.example:1:')]
        self.assertEqual(sorted(j.split(':')[4] for j in jobs), ['domestic', 'domestic'])  # one job per claim

    def test_project_stop_also_defers(self):
        self.enqueue(SCOUT, TRAIL)
        with self.ledger._transaction() as (state, _):
            state['blocked'] = True
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'deferred')

    def test_restart_resumes_an_expired_lease_and_a_late_worker_cannot_activate(self):
        self.enqueue(SCOUT, TRAIL)
        stale = self.queue.claim()  # a worker that stopped after claiming
        self.assertEqual(self.worker().run(), ())  # the lease is still live
        self.now += ob.LEASE + timedelta(seconds=1)
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active')
        kinds = [e['kind'] for e in self.registry.history(lead_id='cedartech.example')]
        self.assertIn('recovered', kinds)
        late = self.worker().onboard(stale)
        self.assertEqual(late.status, 'lease_lost')
        self.assertEqual(len(self.registry.activations(SOURCE_ID)), 2)

    def test_interrupted_leads_stop_after_the_attempt_limit(self):
        self.enqueue(SCOUT, TRAIL)
        for _ in range(ob.MAX_ATTEMPTS):
            self.assertIsNotNone(self.queue.claim())
            self.now += ob.LEASE + timedelta(seconds=1)
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.failure().code, 'retry_limit')


class ModelProposerTests(OnboardingCase):
    class Provider:
        def __init__(self, *answers):
            self.answers, self.calls = list(answers), []

        def generate(self, instructions, data, output, *, budget, cancelled=None):
            self.calls.append((instructions, data, output))
            status, value = self.answers.pop(0)
            return ModelResult(status, value, {})

    def test_model_proposals_are_validated_like_any_other(self):
        targets = ob.TargetChoice(urls=(SCOUT, TRAIL))
        provider = self.Provider(('completed', targets), ('completed', self.proposal()))
        (outcome,) = self.onboard(ob.ModelProposer(provider))
        self.assertEqual(outcome.status, 'active')
        self.assertIn('untrusted', provider.calls[1][0])
        pages = provider.calls[1][1]['pages']
        self.assertEqual({p['url'] for p in pages}, {SCOUT, TRAIL})

    def test_model_cannot_name_another_shop_or_widen_scope(self):
        wrong = self.proposal(operations=[{'operation': 'product', 'path': ['product', {'kind': 'token', 'max_length': 128},
                                                                            {'kind': 'token', 'max_length': 128},
                                                                            {'kind': 'token', 'max_length': 128}]}])
        provider = self.Provider(('completed', ob.TargetChoice(urls=(SCOUT, TRAIL))), ('completed', wrong))
        self.onboard(ob.ModelProposer(provider))
        self.assertEqual(self.queue.get('cedartech.example').record.exception.code, 'validation')
        self.queue.reopen('cedartech.example', note='retest')
        provider.answers = [('completed', ob.TargetChoice(urls=(SCOUT, TRAIL))),
                            ('completed', self.proposal(name='Big Retailer'))]
        self.worker(ob.ModelProposer(provider)).run()
        self.assertEqual(self.queue.get('cedartech.example').record.exception.code, 'identity')

    def test_unavailable_model_uses_the_deterministic_fallback_and_budget_refusal_defers(self):
        provider = self.Provider(('timeout', None), ('invalid_output', None))
        (outcome,) = self.onboard(ob.ModelProposer(provider, fallback=ob.HeuristicProposer()))
        self.assertEqual(outcome.status, 'active')
        self.queue.enqueue('https://second.example/p/1', channel='tavily', categories=('laptop',), reason='t')
        provider.answers = [('budget_denied', None)]
        (deferred,) = self.worker(ob.ModelProposer(provider, fallback=ob.HeuristicProposer())).run()
        self.assertEqual(deferred.status, 'deferred')


class ReviewRegressionTests(OnboardingCase):
    """One test per finding of the independent review of the first version."""

    def test_probe_works_with_a_clock_that_moves_between_the_gates_two_reads(self):
        self.ticking()
        (outcome,) = self.onboard()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        probe = ob.ProbeRegistry(source_id=SOURCE_ID, name='Cedar Tech', origin=SHOP, targets=(SCOUT,),
                                 expires_at=self.now + ob.PROBE_TTL, clock=self.live)
        self.assertEqual(probe.read_registry(), probe.read_registry())

    def test_suspension_surfaces_the_lead_and_reopening_can_reactivate(self):
        self.onboard()
        self.registry.suspend(SOURCE_ID, 'product-page', reason='Listing layout changed')
        (exception,) = self.queue.exceptions()
        self.assertEqual(exception.record.exception.code, 'suspended')
        self.assertEqual(set(exception.record.exception.urls), {SCOUT, TRAIL})
        self.registry.suspend(SOURCE_ID, 'variant-page', reason='Listing layout changed')
        self.assertNotIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])
        self.assertTrue(self.queue.reopen('cedartech.example', note='layout fixed'))
        self.now += timedelta(hours=1)
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        self.assertEqual({o for _, _, o in outcome.capabilities}, {'reactivated'})
        self.assertIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])
        kinds = [e['kind'] for e in self.registry.history(source_id=SOURCE_ID)]
        self.assertEqual((kinds.count('suspended'), kinds.count('reactivated')), (2, 2))
        (product,) = [a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product']
        self.assertEqual([c['status'] for c in product.changes], ['suspended', 'reactivated'])

    def test_a_removed_listing_asks_for_new_samples_instead_of_suspending(self):
        self.onboard()
        self.shop.status[TRAIL] = 404
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in self.worker().recheck(SOURCE_ID)), {'needs_samples'})
        self.assertEqual(len(self.registry.activations(SOURCE_ID, 'active')), 2)
        self.shop.status[TRAIL] = 200
        self.shop.redirects[TRAIL] = 'https://elsewhere.example/product/trail-book'
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in self.worker().recheck(SOURCE_ID)), {'suspended'})

    def test_a_redirect_between_targets_is_one_page_not_two_listings(self):
        self.shop.redirects[TRAIL] = SCOUT
        self.onboard()
        failure = self.queue.get('cedartech.example').record.exception
        self.assertEqual(failure.code, 'no_samples')
        self.assertEqual(self.registry.activations(), ())

    def test_string_and_reference_sellers_are_checked(self):
        offer_seller = b'"seller":{"@type":"Organization","name":"Cedar Tech"}'
        original = self.shop.pages[TRAIL]
        for seller, outcome in ((b'"seller":"Marketplace Vendor 42"', 'exception'),
                                (b'"seller":{"@id":"#vendor-42"}', 'exception'),
                                (b'"seller":[{"name":"Cedar Tech"},{"name":"Vendor 7"}]', 'exception'),
                                (b'"seller":{"@id":"#shop"}', 'active')):
            with self.subTest(seller=seller):
                self.now += timedelta(minutes=2)  # a fresh minute for the shop's request limit
                page = original.replace(offer_seller, seller)
                if b'#shop' in seller:
                    page = page.replace(b'"@type":"ElectronicsStore",', b'"@type":"ElectronicsStore","@id":"#shop",')
                self.shop.pages[TRAIL] = page
                for state in self.queue.leads():
                    self.queue.reopen(state.lead_id, note='retest')
                if not self.queue.leads():
                    self.enqueue(SCOUT, TRAIL)
                (result,) = self.worker().run()
                self.assertEqual(result.status, outcome, result.reason)
                if outcome == 'exception':
                    self.assertEqual(self.queue.get('cedartech.example').record.exception.code, 'identity')

    def test_a_reviewed_pending_entry_keeps_its_id_and_wins_once_reviewed(self):
        entry = lambda status: {'id': 'cedar_tech', 'name': 'Cedar Tech', 'origin': SHOP, 'status': status,
            'reviewed_at': '2026-11-16', 'allowed_endpoints': [SCOUT], 'admission': None if status == 'pending' else {
                'basis': 'Teammate review', 'reviewed_at': '2026-11-17T00:00:00Z', 'scope': 'Read-only',
                'evidence': [SCOUT]}, 'limits': {'requests_per_minute': 6, 'concurrency': 1, 'max_requests_per_run': 6}}
        self.base.write_text(json.dumps({'sources': [entry('pending')], 'live_release': {'admitted_source_ids': []}}),
                             encoding='utf-8')
        self.directory.upsert(DirectoryRecord(entry=RetailerDirectoryEntry(retailer_id='cedar_tech', name='Cedar Tech',
            origin=SHOP, markets=('LB',), categories=('laptop',), discovery_status='discovered'), updated_at=START,
            source_id='cedar_tech', seller_identity='unresolved', product_urls=(SCOUT,)))
        (outcome,) = self.onboard()
        self.assertEqual((outcome.status, outcome.source_id), ('active', 'cedar_tech'))
        document = self.registry.read_registry()
        (stand_in,) = [s for s in document['sources'] if s['id'] == 'cedar_tech']
        self.assertEqual(stand_in['status'], 'active')
        self.assertEqual(self.directory.get('cedar_tech').source_id, 'cedar_tech')
        self.assertTrue(self.directory.select(DirectoryQuery(category='laptop'))[0].configured)
        # A teammate promotes the reviewed entry: it wins, and the directory link still works.
        self.base.write_text(json.dumps({'sources': [entry('active')],
                                         'live_release': {'admitted_source_ids': ['cedar_tech']}}), encoding='utf-8')
        (reviewed,) = [s for s in self.registry.read_registry()['sources'] if s['id'] == 'cedar_tech']
        self.assertEqual(reviewed['allowed_endpoints'], [SCOUT])
        self.assertNotIn('endpoint_capabilities', reviewed)
        (selection,) = self.directory.select(DirectoryQuery(category='laptop'))
        self.assertEqual((selection.configured, selection.permitted_urls), (True, (SCOUT,)))

    def test_a_directory_link_to_another_reviewed_source_needs_a_teammate(self):
        self.base.write_text(json.dumps({'live_release': {'admitted_source_ids': []}, 'sources': [
            {'id': 'old_cedar', 'origin': 'https://cedar-old.example', 'status': 'pending'}]}), encoding='utf-8')
        self.directory.upsert(DirectoryRecord(entry=RetailerDirectoryEntry(retailer_id='cedar_tech', name='Cedar Tech',
            origin=SHOP, markets=('LB',), categories=('laptop',), discovery_status='discovered'), updated_at=START,
            source_id='old_cedar', seller_identity='unresolved', product_urls=(SCOUT,)))
        self.onboard()
        self.assertEqual(self.queue.get('cedartech.example').record.exception.code, 'reviewed_registry')
        self.assertEqual(self.shop.seen, [])

    def test_a_late_worker_neither_fetches_nor_writes(self):
        self.enqueue(SCOUT, TRAIL)
        stale = self.queue.claim()
        self.now += ob.LEASE + timedelta(seconds=1)
        self.assertEqual(self.worker().run()[0].status, 'active')
        seen, record = list(self.shop.seen), self.directory.get(SOURCE_ID)
        self.assertEqual(self.worker().onboard(stale).status, 'lease_lost')
        self.assertEqual((self.shop.seen, self.directory.get(SOURCE_ID)), (seen, record))

    def test_automatic_ids_do_not_collide_and_belong_to_their_domain(self):
        self.assertNotEqual(ob.source_id_for('cedar-tech.example'), ob.source_id_for('cedar.tech.example'))
        self.onboard()
        (product,) = [a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product']
        foreign = product.model_copy(update={'source_id': SOURCE_ID, 'origin': 'https://other.example'})
        plan = ob.ActivationPlan(SOURCE_ID, 'other.example', (foreign.model_copy(update={
            'capability': product.capability.model_copy(update={'capability_id': 'other-page'})}),))
        self.registry.activate(plan)
        (entry,) = [s for s in self.registry.read_registry()['sources'] if s['id'] == SOURCE_ID]
        self.assertEqual(entry['origin'], SHOP)  # the gate keeps validating one host per source

    def test_a_revoked_parent_domain_covers_its_subdomains(self):
        self.base.write_text(json.dumps({'live_release': {'admitted_source_ids': []}, 'sources': [
            {'id': 'cedar', 'origin': SHOP, 'status': 'revoked'}]}), encoding='utf-8')
        result = self.queue.enqueue('https://shop.cedartech.example/p/1', channel='tavily', categories=('laptop',),
                                    reason='t')
        self.assertEqual(result.status, 'excluded')

    def test_each_claim_gets_its_own_ledger_job(self):
        self.enqueue(SCOUT, TRAIL)
        other = self.ledger.open_job('other-maintenance', 'maintenance')
        held = [other.reserve('work', Charge(Decimal(0)), 60) for _ in range(2)]
        self.assertEqual(self.worker().run()[0].status, 'deferred')
        for attempt in held:
            attempt.release()
        self.now += ob.BUSY_PAUSE
        self.assertEqual(self.worker().run()[0].status, 'active')
        jobs = [j for j in self.ledger.snapshot()['jobs'] if j.startswith('onboarding:cedartech.example:1:')]
        self.assertEqual(len(jobs), 2)

    def test_a_stated_product_category_must_not_contradict_the_lead(self):
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(b'"sku":"TB-8-256",',
                                                                b'"sku":"TB-8-256","category":"Monitors",')
        self.onboard()
        failure = self.queue.get('cedartech.example').record.exception
        self.assertEqual((failure.code, failure.reason), ('validation', 'A sampled product states a different category'))
        self.queue.reopen('cedartech.example', note='retest')
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(b'"Monitors"', b'"Laptops & Notebooks"')
        self.assertEqual(self.worker().run()[0].status, 'active')
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        self.assertEqual(product.scope['category_basis'], 'lead')  # the Scout Book pages state no category

    def test_a_model_timeout_retries_instead_of_failing_the_shop(self):
        provider = ModelProposerTests.Provider(('timeout', None))
        (outcome,) = self.onboard(ob.ModelProposer(provider))
        self.assertEqual(outcome.status, 'retry')
        provider.answers = [('refused', None)]
        self.now += ob.RETRY_DELAYS[0]
        (outcome,) = self.worker(ob.ModelProposer(provider)).run()
        self.assertEqual(outcome.status, 'exception')

    def test_research_reports_can_enqueue_their_leads(self):
        from app.research_workers import Report
        reports = (Report.model_construct(leads=(SCOUT, 'https://127.0.0.1/x')), Report.model_construct(leads=(TRAIL,)))
        results = self.queue.enqueue_reports(reports, request())
        self.assertEqual([r.status for r in results], ['queued', 'rejected', 'merged'])
        self.assertEqual(self.queue.get('cedartech.example').record.channels, ('buyer',))

    def concurrently(self, action, count):
        """Run action in parallel threads and re-raise any thread's error in the test."""
        if self.storage.engine.dialect.name == 'sqlite':  # wait for the file lock like PG waits for a row lock
            event.listen(self.storage.engine, 'connect', lambda connection, _: connection.execute(
                'PRAGMA busy_timeout = 60000'))
            self.storage.engine.dispose()
        results, errors = [], []
        def run():
            try:
                results.append(action())
            except Exception as exc:  # surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
        return results

    def test_concurrent_claims_and_workers_never_share_a_lead(self):
        self.enqueue(SCOUT, TRAIL)
        claims = self.concurrently(self.queue.claim, 4)
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.now += ob.LEASE + timedelta(seconds=1)
        second = 'https://pinetech.example'
        for path in ('/product/scout-book', '/product/trail-book'):
            self.shop.pages[second + path] = self.shop.pages[SHOP + path]
        self.queue.enqueue_buyer([second + '/product/scout-book', second + '/product/trail-book'], request())
        outcomes = [o for run in self.concurrently(lambda: self.worker().run(max_leads=1), 2) for o in run]
        self.assertEqual(sorted(o.lead_id for o in outcomes), ['cedartech.example', 'pinetech.example'])
        self.assertTrue(all(o.status == 'active' for o in outcomes), outcomes)
        claimed = [e['lead_id'] for e in self.registry.history() if e['kind'] in ('claimed', 'recovered')]
        self.assertEqual(sorted(claimed), ['cedartech.example', 'cedartech.example', 'pinetech.example'])


class SecondReviewRegressionTests(OnboardingCase):
    """Findings from the re-review and the Codex review of PR #137."""

    def reviewed(self, *entries, admitted=()):
        self.base.write_text(json.dumps({'sources': list(entries),
                                         'live_release': {'admitted_source_ids': list(admitted)}}), encoding='utf-8')

    def test_a_decided_parent_domain_overrides_a_pending_child_entry(self):
        child = 'https://shop.cedartech.example'
        self.reviewed({'id': 'shop_child', 'origin': child, 'status': 'pending'},
                      {'id': 'cedar_parent', 'origin': SHOP, 'status': 'revoked'})
        result = self.queue.enqueue(child + '/product/x', channel='tavily', categories=('laptop',), reason='t')
        self.assertEqual(result.status, 'excluded')
        self.assertIsNone(next((r for r in [self.registry.reviewed('shop.cedartech.example')]
                                if r.status == 'pending'), None))
        self.assertFalse(self.registry._belongs('shop_child', 'shop.cedartech.example',
                                                self.registry.reviewed_hosts()))

    def test_reactivation_supersedes_a_newer_active_version(self):
        self.onboard()
        (product,) = [a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product']
        self.registry.suspend(SOURCE_ID, 'product-page', reason='synthetic layout change')
        other = product.capability.model_copy(update={'version': 'v1-teammate-fix'})
        plan = ob.ActivationPlan(SOURCE_ID, 'cedartech.example', (product.model_copy(update={'capability': other}),))
        self.assertEqual(self.registry.activate(plan)[0][2], 'activated')
        self.queue.reopen('cedartech.example', note='layout fixed')
        self.now += timedelta(hours=1)
        (outcome,) = self.worker().run()
        self.assertIn(('product-page', product.capability.version, 'reactivated'), outcome.capabilities)
        active = [a for a in self.registry.activations(SOURCE_ID, 'active') if a.capability.operation == 'product']
        self.assertEqual([a.capability.version for a in active], [product.capability.version])
        policy = self.gate(self.registry).admitted_policy(SOURCE_ID)
        ids = [c.capability_id for c in policy.endpoint_capabilities]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_new_pending_review_entry_does_not_take_an_automatic_shop_down(self):
        self.onboard()
        self.reviewed({'id': 'cedar', 'origin': SHOP, 'status': 'pending'})
        self.assertIn(SOURCE_ID, self.registry.read_registry()['live_release']['admitted_source_ids'])
        self.assertTrue(self.directory.select(DirectoryQuery(category='laptop'))[0].configured)

    def test_a_suspension_during_a_running_attempt_stops_it(self):
        self.onboard()
        self.registry.suspend(SOURCE_ID, 'product-page', reason='first suspension')
        self.queue.reopen('cedartech.example', note='retest')
        self.now += timedelta(hours=1)
        lease = self.queue.claim()
        self.registry.suspend(SOURCE_ID, 'variant-page', reason='variant pages list marketplace sellers')
        state = self.queue.get('cedartech.example')
        self.assertEqual((state.status, state.record.exception.code), ('exception', 'suspended'))
        self.assertEqual(self.worker().onboard(lease).status, 'lease_lost')
        self.assertEqual(self.registry.activations(SOURCE_ID, 'active'), ())

    def test_removed_listings_reach_a_teammate(self):
        self.onboard()
        self.shop.status = {SCOUT: 404, TRAIL: 410}
        self.now += timedelta(hours=1)
        self.assertEqual(set(o for _, o in self.worker().recheck(SOURCE_ID)), {'needs_samples'})
        state = self.queue.get('cedartech.example')
        self.assertEqual((state.status, state.record.exception.code), ('exception', 'needs_samples'))
        self.assertEqual(set(state.record.exception.urls), {SCOUT, TRAIL})
        self.assertEqual(len(self.registry.activations(SOURCE_ID, 'active')), 2)
        self.assertIn('needs_samples', [e['kind'] for e in self.registry.history(source_id=SOURCE_ID)])

    def test_a_two_category_lead_uses_the_category_its_pages_state(self):
        self.queue.enqueue(SCOUT, channel='tavily', categories=('phone',), reason='t')
        self.queue.enqueue(TRAIL, channel='tavily', categories=('laptop',), reason='t')
        for url, sku in ((SCOUT, b'"sku":"SB-16-512",'), (SCOUT, b'"sku":"SB-32-1024",'), (TRAIL, b'"sku":"TB-8-256",')):
            self.shop.pages[url] = self.shop.pages[url].replace(sku, sku + b'"category":"Laptops",')
        (outcome,) = self.worker().run()
        self.assertEqual(outcome.status, 'active', outcome.reason)
        product = next(a for a in self.registry.activations(SOURCE_ID) if a.capability.operation == 'product')
        self.assertEqual((product.category, product.scope['category_basis']), ('laptop', 'page'))

    def test_a_slow_worker_keeps_a_lead_nobody_else_took(self):
        self.enqueue(SCOUT, TRAIL)
        lease = self.queue.claim()
        self.now += ob.LEASE + timedelta(minutes=1)
        self.assertEqual(self.worker().onboard(lease).status, 'active')

    def test_an_offered_by_seller_is_checked_too(self):
        self.shop.pages[TRAIL] = self.shop.pages[TRAIL].replace(
            b'"seller":{"@type":"Organization","name":"Cedar Tech"}',
            b'"offeredBy":{"@type":"Organization","name":"Vendor 42"}')
        self.onboard()
        self.assertEqual(self.queue.get('cedartech.example').record.exception.code, 'identity')


class SnapshotRaceTests(OnboardingCase):
    def test_a_concurrent_save_of_the_same_page_is_not_an_error(self):
        from hashlib import sha256
        from app.storage import snapshots
        body = self.shop.pages[SCOUT]
        other = Storage(self.url, self.storage.snapshots.root)  # another process
        self.addCleanup(other.engine.dispose)
        raced = []
        def race(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('INSERT INTO SNAPSHOTS') and not raced:
                raced.append(True)
                with other.transaction() as db:
                    db.execute(snapshots.insert().values(sha256=sha256(body).hexdigest(), size_bytes=len(body),
                                                         media_type='text/html', stored_at=START))
        event.listen(self.storage.engine, 'before_cursor_execute', race)
        self.addCleanup(event.remove, self.storage.engine, 'before_cursor_execute', race)
        self.assertEqual(self.storage.save_snapshot(body, 'text/html'), sha256(body).hexdigest())
        self.assertEqual(raced, [True])
        self.assertEqual(self.storage.load_snapshot(sha256(body).hexdigest()), body)


from test_runs import RunCase  # noqa: E402  (the v1 run controller's offline fixture)


class FileRegistry:
    """A registry object, as ActivationRegistry is, over the same reviewed file."""

    def __init__(self, path):
        self.path = path

    def read_registry(self):
        return json.loads(Path(self.path).read_text(encoding='utf-8'))


class V1RunnerRegistryTests(RunCase):
    def test_the_v1_run_controller_reads_a_registry_object(self):
        by_file = self.search()
        by_object = self.search(registry_path=FileRegistry(self.registry), run_id='run-2')
        self.assertEqual(by_object.status, by_file.status)
        self.assertEqual([(c.source_id, c.status) for c in by_object.source_coverage],
                         [(c.source_id, c.status) for c in by_file.source_coverage])


class CommandTests(OnboardingCase):
    def test_teammate_commands_list_suspend_and_reopen(self):
        self.shop.status = {SCOUT: 403, TRAIL: 404}
        self.onboard()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(ob.main(['exceptions'], storage=self.storage), 0)
        line = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual((line['lead_id'], line['code']), ('cedartech.example', 'blocked'))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ob.main(['reopen', '--lead', 'cedartech.example', '--note', 'parser fixed'],
                                     storage=self.storage), 0)
        self.assertEqual(self.queue.get('cedartech.example').status, 'queued')


if __name__ == '__main__':
    unittest.main()
