"""Real Chromium, with all retailer and Firecrawl responses supplied offline."""
from contextlib import contextmanager
from functools import partial
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx2

from app.agent_inspection import inspect_public_listing, read_catalog_listing
from app.bootstrap import seed_target
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.maintenance import control
from app.maintenance_discovery import SearchDecision
from app.onboarding import ActivationRegistry
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_domestic_buyer import lead
import test_maintenance as maintenance
from test_public_listing_buyer import BuyerDecisions, URL, WORDING, listing
from test_shopping_agent import development_tools


def storefront(url, dropdown):
    default = listing('White', '39.00').replace(URL, url).replace('schema.org/InStock', 'schema.org/OutOfStock')
    variants = json.dumps([
        dict(id=3034, options={'Color': 'White'}, raw=39, price='$39.00', stock=False),
        dict(id=3035, options={'Color': 'Black'}, raw=49, price='$49.00', stock=True)])
    controls = ('<select name="options[Color]"><option value="White" selected>White</option>'
                '<option value="Black">Black</option></select>' if dropdown else
                default[default.index('<input'):default.index('<p>')])
    return (default[:default.index('<input')] +
            '<meta property="og:site_name" content="Fixture Audio">'
            '<meta property="product:price:currency" content="USD">'
            '<p>Fixture Audio, Beirut, Lebanon</p>'
            f"<div data-variant-list='{variants}'>" +
            controls +
            '<input type="hidden" name="variant_id" value="3034">'
            '<span data-variant-price>$39.00</span>'
            '<span data-variant-stock data-in="In stock" data-out="Out of stock">Out of stock</span>'
            '</div><script src="/static/colors.js"></script>')


# The merchant changes the displayed offer but leaves its default product JSON intact.
CHANGE = """document.addEventListener('change', e => {
  if (e.target.value === 'Black') {
    document.querySelector('[name="variant_id"]').value = '3035';
    document.querySelector('[data-variant-price]').textContent = '$49.00';
    document.querySelector('[data-variant-stock]').textContent = 'In stock';
  }
});"""


@contextmanager
def retailer(storage, url, *, registry=None, script=CHANGE, dropdown=False):
    reads = []
    body = storefront(url, dropdown)
    limiter = SourceLimiter()

    def provider(endpoint, key, payload, **kwargs):
        assert 'actions' not in payload and payload['url'] == url
        reads.append(('firecrawl', url))
        return dict(success=True, data=dict(rawHtml=body, metadata=dict(url=url, sourceURL=url,
            statusCode=200, contentType='text/html', provider=dict(creditsCost=1))))

    def respond(request):
        reads.append((request.method, request.url.path))
        if str(request.url) == url:
            content, mime = body, 'text/html'
        elif request.url.path == '/static/colors.js':
            content, mime = script, 'application/javascript'
        else:
            raise AssertionError(f'Unexpected source request {request.url.path}')
        body_bytes = content.encode()
        return httpx2.Response(200, headers={'content-type': mime, 'content-length': str(len(body_bytes))},
                               stream=httpx2.ByteStream(body_bytes))

    def gate(scope):
        return SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'], limiter=limiter,
                                     transport=httpx2.MockTransport(respond))

    with patch('app.agent_inspection.SourceGate', side_effect=gate), patch('app.firecrawl.post_json', side_effect=provider):
        inspect = partial(inspect_public_listing, registry=registry or ActivationRegistry(storage),
                          environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='included'))
        yield inspect, reads


class BuyerBrowserTests(FlowCase):
    def test_buyer_worker_opens_new_seller_then_clicks_and_returns_changed_price(self):
        self.openrouter = BuyerDecisions(['Search serpapi', 'Inspect', 'Select Black', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        deps.research.workers.domestic_tools.search = lambda query, job, alternatives: SearchReport(
            job.run_id, (ChannelAttempt('serpapi', 'ok'),), (lead(URL),))
        with retailer(self.storage, URL) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING), allowed_colors=['Black'],
                fulfillment=dict(destination=dict(city='Batroun')))
        self.assertEqual(reads, [('firecrawl', URL), ('GET', '/products/soundcore-q20i'), ('GET', '/static/colors.js')])
        self.assertEqual(run.status, 'partial', self.trace(run))
        item, = run.uncertain
        offer = item.offer
        self.assertEqual((offer.source_variant_id, offer.variant.color.value, str(offer.price.value.amount),
                          offer.stock.value, offer.condition.state), ('3035', 'Black', '49', True, 'unknown'))
        self.assertEqual({e.response_sha256 for e in offer.evidence}, {offer.evidence[0].response_sha256})
        self.assertEqual([a['reader'] for a in self.trace(run)['actions'] if 'reader' in a], ['firecrawl-v2', 'playwright'])


class CatalogBrowserTests(TestCase):
    def setUp(self):
        self.schedule = maintenance.ScheduleTests()
        self.schedule.setUp()
        self.addCleanup(self.schedule.doCleanups)
        self.f = self.schedule.fixture
        self.registry = ActivationRegistry(self.f.fixture.storage, base=self.f.fixture.registry)

    def test_manual_offer_job_persists_only_clicked_variant(self):
        f, runner = self.f, self.schedule.runner
        seed = f.manifest.seeds[0].model_copy(update={'sku': '3035', 'selection_color': 'Black'})
        runner.manifest = f.manifest.model_copy(update={'seeds': (seed,)})
        with retailer(f.fixture.storage, f.url, registry=self.registry) as (runner.inspect_listing, reads):
            control(self.schedule.ledger, True)
            report = runner.run('offers', force=True)
        page = report['pages'][f.url]
        self.assertEqual((page['status'], page.get('reader')), ('new', 'playwright'), report)
        self.assertEqual(reads, [('firecrawl', f.url), ('GET', '/devices/selected'), ('GET', '/static/colors.js')])
        state = f.fixture.corpus.state(seed_target(seed, f.fixture.corpus, f.fixture.registry)[0])
        observations = f.fixture.corpus.observations(state.document_id)
        facts = {o.evidence[0].field_path: o.fact.value for o in observations}
        self.assertEqual((facts['variant.color'], facts['price'], facts['stock']),
                         ('Black', {'amount': '49', 'currency': 'USD'}, True))
        self.assertEqual({(o.evidence[0].variant_id, o.evidence[0].response_sha256) for o in observations},
                         {('3035', page['sha256'])})

    def test_catalog_default_needs_no_browser_and_inert_radio_gives_no_offer(self):
        f = self.f
        with retailer(f.fixture.storage, f.url, registry=self.registry, script='// inert') as (inspect, reads):
            default = self.schedule.ledger.open_job('read-default', 'maintenance')
            self.assertEqual(read_catalog_listing(inspect, default, f.url, 'White').provider, 'firecrawl-v2')
            self.assertEqual(reads, [('firecrawl', f.url)])
            click = self.schedule.ledger.open_job('read-inert', 'maintenance')
            with self.assertRaisesRegex(ValueError, 'could not confirm'):
                read_catalog_listing(inspect, click, f.url, 'Black')

    def test_catalog_discovery_can_select_from_dropdown_at_first_seen_shop(self):
        schedule = self.schedule
        schedule.tools.search.side_effect = lambda objective, job: SearchReport(job.run_id, (
            ChannelAttempt(objective.channel, 'ok'),), (lead(URL),))
        schedule.roles.generate.side_effect = [
            SimpleNamespace(status='completed', value=SearchDecision(action='search', channel='serpapi', query='Q20i')),
            SimpleNamespace(status='completed', value=SearchDecision(action='inspect', listing_index=0, color='Black'))]
        with retailer(self.f.fixture.storage, URL, registry=self.registry, dropdown=True) as (schedule.runner.inspect_listing, reads):
            control(schedule.ledger, True)
            report = schedule.runner.run('discovery', force=True)
        self.assertEqual(report['stop_reason'], 'public listing inspected', report)
        receipt, = report['inspections']
        self.assertEqual((receipt['reader'], receipt['color'], receipt['sku'], receipt['price']['amount']),
                         ('playwright', 'Black', '3035', '49'))
        self.assertEqual(reads, [('firecrawl', URL), ('GET', '/products/soundcore-q20i'), ('GET', '/static/colors.js')])
