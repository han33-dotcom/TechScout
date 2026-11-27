"""Real Chromium, with all retailer and Firecrawl responses supplied offline."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx2

from app.agent_inspection import InspectionChoice, inspect_public_listing, read_catalog_listing
from app.bootstrap import seed_target
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.maintenance import control
from app.maintenance_discovery import SearchDecision
from app.onboarding import ActivationRegistry
from app.selected_listing import ConflictingSelection, ListingDocument
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_domestic_buyer import lead
import test_maintenance as maintenance
from test_public_listing_buyer import BuyerDecisions, URL, WORDING, listing
from test_shopping_agent import Decisions, development_tools


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


def tablet(url):
    product = {'@type': 'Product', 'url': url, 'name': 'Atlas Tab', 'brand': 'Atlas', 'sku': 'tab-256',
               'color': 'Blue', 'additionalProperty': {'name': 'Storage', 'value': '256 GB'},
               'offers': {'@type': 'Offer', 'url': url, 'price': '499', 'priceCurrency': 'USD',
                          'availability': 'https://schema.org/InStock', 'itemCondition': 'https://schema.org/NewCondition',
                          'seller': {'name': 'Atlas Beirut', 'address': {'addressCountry': 'LB'}}}}
    return (f'<script type="application/ld+json">{json.dumps(product)}</script>'
            '<h1>Atlas Tab</h1><select name="storage"><option value="128 GB">128 GB</option>'
            '<option value="256 GB" selected>256 GB</option></select>'
            '<a class="variant" href="?sku=128">128 GB</a><span data-variant-price>USD 499</span>'
            '<script src="/static/colors.js"></script>')


TABLET_CHANGE = """document.querySelector('select').addEventListener('change', () => {
    const script = document.querySelector('script[type="application/ld+json"]');
    const product = JSON.parse(script.textContent);
    product.sku = 'tab-128';
    product.additionalProperty.value = '128 GB';
    product.offers.price = '349';
    script.textContent = JSON.stringify(product);
    document.querySelector('[data-variant-price]').textContent = 'USD 349';
});"""


class TabletDecisions(Decisions):
    def answer(self, role, data):
        if 'selected' in data:
            fits = data['selected']['selected_options'].get('storage') == '128 GB'
            return dict(selected_item_fits=fits, merchant_address_line=None, reason='Checked the selected storage.')
        return super().answer(role, data)


class SelectedCapacityTests(TestCase):
    def test_single_capacity_in_the_priced_title_can_confirm_an_observed_option(self):
        html = tablet(URL).replace('"additionalProperty"', '"unused"')
        now = datetime.now(timezone.utc)
        selected = ListingDocument(html.replace('"Atlas Tab"', '"Atlas Tab 256GB"')).selected(URL, now)
        self.assertEqual(selected.storage(), (256, '256 GB'))
        with self.assertRaises(ConflictingSelection):
            ListingDocument(html.replace('"Atlas Tab"', '"Atlas Tab 128GB or 256GB"')).selected(URL, now)


@contextmanager
def retailer(storage, url, *, registry=None, script=CHANGE, dropdown=False, html=None, first_read=None, links=None):
    reads = []
    body = storefront(url, dropdown) if html is None else html
    limiter = SourceLimiter()

    def provider(endpoint, key, payload, **kwargs):
        target = payload['url']
        assert 'actions' not in payload
        content = (body if first_read is None else first_read) if target == url else (links or {})[target]
        reads.append(('firecrawl', target))
        return dict(success=True, data=dict(rawHtml=content, metadata=dict(url=target, sourceURL=target,
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
    def test_buyer_can_finish_with_the_initial_firecrawl_receipt(self):
        self.openrouter = BuyerDecisions(['Inspect', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        with retailer(self.storage, URL, html=listing('Black', '49.00')) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', intent=dict(kind='exact_model', model='Q20i',
                wording=WORDING), refinement_note=URL, requirements=dict(category='audio'), allowed_colors=['Black'])
        item, = run.uncertain
        self.assertEqual(str(item.offer.price.value.amount), '49.00')
        self.assertEqual(reads, [('firecrawl', URL)])

    def test_buyer_can_select_storage_despite_a_published_link(self):
        self.openrouter = TabletDecisions(['Inspect', 'Select 128 GB', 'Present'])
        deps = development_tools(self.deps(), 'Atlas Tab')
        with retailer(self.storage, URL, html=tablet(URL), script=TABLET_CHANGE) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', intent=dict(kind='product_link', url=URL),
                requirements=dict(category='tablet', storage_gb=128), fulfillment=dict(destination=dict(city='Batroun')))
        self.assertEqual([r[0] for r in reads], ['firecrawl', 'GET', 'GET'])
        item, = run.uncertain
        self.assertEqual((item.offer.source_variant_id, item.offer.variant.specs.storage_gb.value,
                          str(item.offer.price.value.amount)), ('tab-128', 128, '349'))
        menus = [json.loads(r['body']['messages'][1]['content']).get('available', []) for r in self.openrouter.requests]
        self.assertFalse(any('Playwright' in s for s in menus[0]))
        self.assertTrue(any(any('?sku=128' in s for s in menu) and any('Select 128 GB' in s for s in menu)
                            for menu in menus))

    def test_buyer_can_render_an_empty_first_read_without_a_control(self):
        self.openrouter = BuyerDecisions(['Inspect', 'Render listing', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        with retailer(self.storage, URL, first_read='', html=listing('Black', '49.00')) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', intent=dict(kind='exact_model', model='Q20i',
                wording=WORDING), refinement_note=URL, requirements=dict(category='audio'), allowed_colors=['Black'])
        item, = run.uncertain
        self.assertEqual((item.offer.variant.color.value, str(item.offer.price.value.amount)), ('Black', '49.00'))
        self.assertEqual([r[0] for r in reads], ['firecrawl', 'GET'])

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

    def decisions(self, *prefixes):
        choices = iter(prefixes)
        def choose(prompt, data, schema, **kwargs):
            prefix = next(choices)
            if isinstance(prefix, SearchDecision):
                return SimpleNamespace(status='completed', value=prefix)
            self.assertIs(schema, InspectionChoice)
            index = next(i for i, label in enumerate(data['available']) if label.startswith(prefix))
            return SimpleNamespace(status='completed', value=InspectionChoice(action_index=index))
        self.schedule.roles.generate.side_effect = choose

    def test_manual_offer_job_persists_only_clicked_variant(self):
        f, runner = self.f, self.schedule.runner
        seed = f.manifest.seeds[0].model_copy(update={'sku': '3035', 'selection_color': 'Black'})
        runner.manifest = f.manifest.model_copy(update={'seeds': (seed,)})
        self.decisions('Select Black', 'Finish')
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
            self.decisions('Finish', 'Select Black')
            self.assertEqual(read_catalog_listing(inspect, default, f.url, objective='White', roles=self.schedule.roles).provider, 'firecrawl-v2')
            self.assertEqual(reads, [('firecrawl', f.url)])
            click = self.schedule.ledger.open_job('read-inert', 'maintenance')
            prior = read_catalog_listing(inspect, click, f.url, objective='Black', roles=self.schedule.roles)
            self.assertEqual(prior.provider, 'firecrawl-v2')
            self.assertEqual(reads[1:], [('firecrawl', f.url), ('GET', '/devices/selected'), ('GET', '/static/colors.js')])

    def test_refresh_agent_can_select_and_store_a_noncolor_variant(self):
        f, runner = self.f, self.schedule.runner
        seed = f.manifest.seeds[0].model_copy(update={'category': 'tablet', 'sku': 'tab-128', 'listing': None})
        runner.manifest = f.manifest.model_copy(update={'seeds': (seed,)})
        self.decisions('Select 128 GB', 'Finish')
        html = tablet(f.url)
        buttons = ''.join(f'<button type="button" data-option-name="storage" data-option-value="{value} GB" '
                          f'aria-pressed="{active}">{value} GB</button>' for value, active in [(128, 'false'), (256, 'true')])
        html = html[:html.index('<select')] + buttons + html.split('</select>')[1]
        script = TABLET_CHANGE.replace("document.querySelector('select').addEventListener('change', () => {",
            """document.querySelector('button').addEventListener('click', e => {
            document.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.target)));""")
        with retailer(f.fixture.storage, f.url, registry=self.registry, html=html,
                      script=script) as (runner.inspect_listing, reads):
            control(self.schedule.ledger, True)
            report = runner.run('offers', force=True)
        self.assertEqual(report['pages'][f.url]['status'], 'new', report)
        self.assertEqual([step['action'].split()[0] for step in report['pages'][f.url]['decisions']], ['Select', 'Finish'])
        state = f.fixture.corpus.state(seed_target(seed, f.fixture.corpus, f.fixture.registry)[0])
        facts = {o.evidence[0].field_path: o.fact.value for o in f.fixture.corpus.observations(state.document_id)}
        self.assertEqual((facts['variant.specs.storage_gb'], facts['price']['amount']), (128, '349'))
        self.assertEqual([r[0] for r in reads], ['firecrawl', 'GET', 'GET'])

    def test_catalog_can_render_a_page_without_options_or_price_in_first_read(self):
        f = self.f
        self.decisions('Render', 'Finish')
        product = tablet(f.url).split('<h1>')[0]
        html = product + '<span data-variant-price>Loading price</span><script src="/static/colors.js"></script>'
        script = "document.querySelector('[data-variant-price]').textContent='USD 499'"
        with retailer(f.fixture.storage, f.url, registry=self.registry, html=html, script=script) as (inspect, reads):
            page = read_catalog_listing(inspect, self.schedule.ledger.open_job('render-only', 'maintenance'), f.url,
                                        objective='Current price of tab-256', roles=self.schedule.roles)
        self.assertEqual(page.provider, 'playwright')
        self.assertIn(b'USD 499', page.outcome.body)
        self.assertEqual([r[0] for r in reads], ['firecrawl', 'GET', 'GET'])

    def test_catalog_can_choose_a_second_firecrawl_read_over_playwright(self):
        f = self.f
        link = f.url + '?variant=black'
        html = storefront(f.url, False) + '<a href="?variant=black">Black</a>'
        self.decisions('Read Black', 'Finish')
        with retailer(f.fixture.storage, f.url, registry=self.registry, html=html,
                      links={link: listing('Black', '49.00').replace(URL, link)}) as (inspect, reads):
            page = read_catalog_listing(inspect, self.schedule.ledger.open_job('catalog-link', 'maintenance'),
                                        f.url, objective='Find the black pair', roles=self.schedule.roles)
        self.assertEqual((page.provider, page.outcome.final_url), ('firecrawl-v2', link))
        self.assertEqual(reads, [('firecrawl', f.url), ('firecrawl', link)])

    def test_catalog_discovery_can_select_from_dropdown_at_first_seen_shop(self):
        schedule = self.schedule
        schedule.tools.search.side_effect = lambda objective, job: SearchReport(job.run_id, (
            ChannelAttempt(objective.channel, 'ok'),), (lead(URL),))
        self.decisions(SearchDecision(action='search', channel='serpapi', query='Q20i'),
                       SearchDecision(action='inspect', listing_index=0, color='Black'), 'Select Black', 'Finish')
        with retailer(self.f.fixture.storage, URL, registry=self.registry, dropdown=True) as (schedule.runner.inspect_listing, reads):
            control(schedule.ledger, True)
            report = schedule.runner.run('discovery', force=True)
        self.assertEqual(report['stop_reason'], 'public listing inspected', report)
        receipt, = report['inspections']
        self.assertEqual((receipt['reader'], receipt['color'], receipt['sku'], receipt['price']['amount']),
                         ('playwright', 'Black', '3035', '49'))
        self.assertEqual(reads, [('firecrawl', URL), ('GET', '/products/soundcore-q20i'), ('GET', '/static/colors.js')])
