"""One capture must suffice for both agents to read a published option or explain its absence."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest import TestCase

from app.page_observation import PageDecision
from fixture_page_agent import fixture_read
from app.agent_inspection import InspectionChoice
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.maintenance import control
from app.maintenance_discovery import SearchDecision
from app.onboarding import ActivationRegistry
from app.selected_listing import ConflictingSelection, ListingDocument, Match, PageReading
import test_buyer_flow as flow
import test_listing_browser as browser
import test_maintenance as maintenance
from test_domestic_buyer import lead
from test_public_listing_buyer import BuyerDecisions, URL, WORDING
from test_shopping_agent import development_tools


def records(colors, url=URL, seller='Fixture Audio'):
    products = []
    for color in colors:
        offer = dict(priceCurrency='USD', price='40.00' if color == 'Black' else '28.00',
                     url=url + '?variant=' + color.lower(), availability='OutOfStock' if color == 'Black' else 'InStock',
                     seller=dict(name=seller, address=dict(addressCountry='LB')), itemCondition='NewCondition')
        products.append(dict(name='Soundcore Q20i - ' + color, sku='sku-' + color.lower(), brand='Soundcore',
                             offers={'@type': 'Offer', **offer}, **{'@type': 'Product'}))
    return '<script type="application/ld+json">' + json.dumps(products) + '</script>'


TABLE = ('<header>Sold by Fixture Audio</header><aside>Fixture Audio, Beirut, Lebanon</aside><table>'
         '<tr><td>Soundcore Q20i - Navy</td><td>Navy</td><td>USD 28.00</td><td>In stock</td></tr>'
         '<tr><td>Soundcore Q20i - Black</td><td>Black</td><td>USD 44.00</td><td>Out of stock</td></tr></table>')


def read_evidence(data):
    if rows := data['published_offers']:
        for i, row in enumerate(rows):
            if 'Black' in row['title']:
                return PageReading(offer_index=i, color='Black')
        return PageReading(reason='Only Blue and White are listed. Black is unverified.')
    lines = data['lines']
    return PageReading(title_line=lines.index('Soundcore Q20i - Black'), price_line=lines.index('USD 44.00'),
                       color_line=lines.index('Black'), color='Black', brand='Soundcore', merchant='Fixture Audio',
                       merchant_line=lines.index('Sold by Fixture Audio'), address_line=lines.index('Fixture Audio, Beirut, Lebanon'),
                       stock_line=lines.index('Out of stock'))


class Decisions(BuyerDecisions):
    def answer(self, role, data):
        if 'page' in data:
            return fixture_read(data, read_evidence).model_dump(mode='json')
        return super().answer(role, data)


class BuyerEvidenceTests(flow.FlowCase):
    def test_clarification_reaches_the_model_matching_a_page_with_multiple_products(self):
        self.openrouter = Decisions(['Ask'])
        deps = development_tools(self.deps(), 'Q20i')
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
            intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING))
        answer = 'Only the black version please. Check ' + URL
        reply = self.client.post(f'/api/runs/{run.run.run_id}/clarify', headers=self.headers,
            json=dict(run=run.run.model_dump(), clarification_id=run.clarification.clarification_id,
                      answer=answer))
        self.assertEqual(reply.status_code, 202)
        self.openrouter = Decisions(['Inspect', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        with browser.retailer(self.storage, URL, html=records(['Navy', 'Black'])) as (deps.inspect_listing, _):
            self.work(deps)
        payloads = [json.loads(r['body']['messages'][1]['content']) for r in self.openrouter.requests]
        reading = next(p for p in payloads if 'page' in p)
        self.assertEqual(reading['objective']['intent']['wording'], WORDING)
        self.assertEqual(reading['feedback'][0]['answer'], answer)

    def inspect(self, html):
        self.openrouter = Decisions(['Inspect', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        with browser.retailer(self.storage, URL, html=html) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                refinement_note='Check ' + URL, allowed_colors=['black'])
        self.assertEqual(reads, [('firecrawl', URL)])
        return run

    def test_agent_uses_one_published_offer_and_its_own_stock(self):
        run = self.inspect(records(['Navy', 'Black', 'Beige']))
        item, = run.uncertain
        self.assertEqual((item.offer.source_variant_id, item.offer.stock.value, str(item.offer.price.value.amount)),
                         ('sku-black', False, '40.00'))
        self.assertEqual(str(item.offer.listing_url), URL + '?variant=black')
        self.assertEqual(item.offer.domestic_seller(), 'listing')
        self.assertTrue(all(str(e.final_url) == URL for e in item.offer.evidence))
        payloads = [json.loads(request['body']['messages'][1]['content']) for request in self.openrouter.requests]
        self.assertFalse(any('selected' in payload for payload in payloads))
        self.assertFalse(run.recommendations)

    def test_wrong_colors_explained_without_inventing_a_black_offer(self):
        run = self.inspect(records(['Blue', 'White']))
        self.assertIn('Black is unverified', run.stop_reason)
        self.assertFalse(run.uncertain or run.recommendations)

    def test_missing_merchant_keeps_observed_item_price_and_stock_in_the_explanation(self):
        run = self.inspect(records(['Blue', 'Black'], seller=''))
        self.assertEqual(run.status, 'abstained')
        self.assertIn('USD 40.00 (out of stock)', run.stop_reason)
        self.assertFalse(run.uncertain or run.recommendations)

    def test_unfamiliar_table_keeps_text_row_price_and_stock_together(self):
        item, = self.inspect(TABLE).uncertain
        self.assertEqual((item.offer.variant.color.value, str(item.offer.price.value.amount), item.offer.stock.value),
                         ('Black', '44.00', False))


class CatalogEvidenceTests(TestCase):
    def setUp(self):
        self.schedule = maintenance.ScheduleTests()
        self.schedule.setUp()
        self.addCleanup(self.schedule.doCleanups)
        self.f = self.schedule.fixture

    def inspect(self, html):
        schedule = self.schedule
        schedule.tools.search.side_effect = lambda objective, job: SearchReport(
            job.run_id, (ChannelAttempt(objective.channel, 'ok'),), (lead(URL),))
        choices = iter([SearchDecision(action='search', channel='serpapi', query='Q20i'),
                        SearchDecision(action='inspect', listing_index=0, color='Black')])
        def decide(prompt, data, schema, **kwargs):
            choice = (next(choices) if schema is SearchDecision else
                      fixture_read(data, read_evidence) if schema is PageDecision else InspectionChoice(action_index=0))
            return SimpleNamespace(status='completed', value=choice)
        schedule.roles.generate.side_effect = decide
        with browser.retailer(self.f.fixture.storage, URL, html=html) as (schedule.runner.inspect_listing, reads):
            control(schedule.ledger, True)
            report = schedule.runner.run('discovery', force=True)
        self.assertEqual(reads, [('firecrawl', URL)])
        return report

    def test_discovery_records_agents_chosen_variant(self):
        item, = self.inspect(records(['Navy', 'Black']))['inspections']
        self.assertEqual((item['sku'], item['stock'], item['price']['amount'], item['offer_url']),
                         ('sku-black', 'OutOfStock', '40.00', URL + '?variant=black'))

    def test_missing_variant_is_explained(self):
        report = self.inspect(records(['Blue', 'White']))
        self.assertIn('Black is unverified', report['stop_reason'])
        self.assertFalse(report['inspections'])

    def test_catalog_can_use_visible_table_as_evidence(self):
        item, = self.inspect(TABLE)['inspections']
        self.assertEqual((item['stock'], item['price']['amount'], item['color']), ('OutOfStock', '44.00', 'Black'))

    def test_scheduled_refresh_persists_the_chosen_offer_at_the_seed_sku(self):
        f, runner = self.f, self.schedule.runner
        seed = f.manifest.seeds[0].model_copy(update={'sku': 'sku-black', 'selection_color': 'Black'})
        runner.manifest = f.manifest.model_copy(update={'seeds': (seed,)})
        def decide(prompt, data, schema, **kwargs):
            return SimpleNamespace(status='completed', value=fixture_read(data, read_evidence) if schema is PageDecision else InspectionChoice(action_index=0))
        self.schedule.roles.generate.side_effect = decide
        html = records(['Navy', 'Black'], url=f.url, seller='one ltd')
        with browser.retailer(f.fixture.storage, f.url, registry=ActivationRegistry(
                f.fixture.storage, base=f.fixture.registry), html=html) as (runner.inspect_listing, reads):
            control(self.schedule.ledger, True)
            report = runner.run('offers', force=True)
        page = report['pages'][f.url]
        self.assertEqual(page['status'], 'new', page)
        self.assertIn('price', page['changed_fields'])
        self.assertEqual(page['decisions'][-1]['selected']['sku'], 'sku-black')
        self.assertEqual(reads, [('firecrawl', f.url)])

    def test_text_reader_cannot_mix_citations_from_different_table_rows(self):
        doc = ListingDocument(TABLE)
        when = datetime.now(timezone.utc)
        reading = read_evidence(doc.evidence(URL, when)).model_copy(update={'price_line': doc.lines.index('USD 28.00')})
        with self.assertRaises(ConflictingSelection):
            doc.interpret(reading, URL, when)

    def test_parent_title_cannot_override_a_sibling_offer_color(self):
        offers = [dict(url=URL + '?attribute_pa_color=' + color, sku=color, price='61', priceCurrency='USD',
                       **{'@type': 'Offer'}) for color in ('blue', 'white')]
        html = json.dumps(dict(name='Q20i Black Headphones', color='Black', offers=offers, **{'@type': 'Product'}))
        doc = ListingDocument('<script type="application/ld+json">' + html + '</script>')
        with self.assertRaisesRegex(ValueError, 'color'):
            doc.interpret(PageReading(offer_index=0, color='Black'), URL, datetime.now(timezone.utc))
