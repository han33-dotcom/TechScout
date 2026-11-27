"""Checkpoint 2: buyer API and worker, with offline model and Firecrawl transports."""
from functools import partial
from datetime import datetime, timezone
from hashlib import sha256
import json
from unittest.mock import patch

from app.agent_inspection import inspect_public_listing
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.onboarding import ActivationRegistry
from app.firecrawl import ManagedPage
from app.sources import FetchOutcome, SourceGate
from app.onboarding import source_id_for
from test_buyer_flow import FlowCase
from test_domestic_buyer import lead
from test_shopping_agent import Decisions, development_tools


URL = 'https://new-shop.example/products/soundcore-q20i'
WORDING = "I'm looking for new black Soundcore Q20i headphones delivered to Batroun."


def listing(color, price):
    product = dict(name='Soundcore Q20i Hybrid ANC Headphones', sku=f'Q20i-{color}', brand='Soundcore',
        url=URL, color=color, offers=dict(priceCurrency='USD', price=price, url=URL,
            seller=dict(name='Fixture Audio', address=dict(addressLocality='Beirut', addressCountry='LB')),
            availability='https://schema.org/InStock', itemCondition='https://schema.org/NewCondition'))
    product['@type'] = 'Product'
    product['offers']['@type'] = 'Offer'
    radios = ''.join(f'<input type="radio" name="color" value="{value}"'
                     + (' checked' if value == color else '') + '>' for value in ('White', 'Black'))
    return f'<script type="application/ld+json">{json.dumps(product)}</script>{radios}<p>USD {price}</p>'


class BuyerDecisions(Decisions):
    def answer(self, role, data):
        if 'lines' in data:
            lines = data['lines']
            return dict(title_line=lines.index('Soundcore Q20i Hybrid ANC Headphones'), brand='Soundcore',
                        merchant='Fixture Audio', merchant_line=lines.index('Sold by Fixture Audio'),
                        address_line=lines.index('Fixture Audio, Beirut, Lebanon'),
                        price_line=next(i for i, line in enumerate(lines) if line.startswith('USD ')),
                        condition_line=lines.index('Condition: New'), stock_line=lines.index('In stock'))
        if 'selected' in data:
            assert data['request']['intent']['wording'] == WORDING
            fits = data['selected']['color'] == 'Black'
            return dict(selected_item_fits=fits,
                        merchant_address_line=None if data['selected']['sku'].startswith('Q20i-') else 0,
                        reason='The requested black Q20i is selected.' if fits else 'The selected color is white.')
        return super().answer(role, data)


class PublicListingBuyerTests(FlowCase):
    def redirected_journey(self, queried, landed, color):
        self.openrouter = BuyerDecisions(['Inspect', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage),
            environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.001'))
        html = listing(color, '48.00').replace(URL, landed).replace('Fixture Audio', 'Landing Audio')
        reply = dict(success=True, data=dict(rawHtml=html, metadata=dict(url=landed, sourceURL=queried,
            contentType='text/html', statusCode=200, provider=dict(creditsCost=1))))
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', return_value=reply) as send):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                refinement_note='Check ' + queried, allowed_colors=['black'])
        self.assertEqual([c.args[2]['url'] for c in send.call_args_list], [queried])
        return run

    def test_tracking_removed_uses_the_captured_page_in_one_read(self):
        queried = URL + '?srsltid=test'
        run = self.redirected_journey(queried, URL, 'Black')
        item, = run.uncertain
        self.assertEqual(str(item.offer.listing_url), URL)
        self.assertTrue(all((str(e.origin_url), str(e.final_url)) == (queried, URL) for e in item.offer.evidence))

    def test_changed_shop_credits_the_landing_seller_and_selected_variant(self):
        landed = 'https://landing-shop.example/item/q20i?variant=black'
        run = self.redirected_journey(URL, landed, 'Black')
        item, = run.uncertain
        self.assertEqual(item.offer.source_id, source_id_for('landing-shop.example'))
        self.assertEqual(item.offer.seller.name, 'Landing Audio')
        self.assertEqual(item.offer.domestic_seller(), 'listing')
        self.assertEqual(str(item.offer.listing_url), landed)
        self.assertEqual(str(item.offer.price.value.amount), '48.00')
        self.assertEqual(item.cost.status, 'incomplete')

    def test_redirect_to_wrong_variant_does_not_supply_requested_price(self):
        run = self.redirected_journey(URL + '?variant=black', URL + '?variant=white', 'White')
        self.assertFalse(run.uncertain or run.recommendations)

    def test_second_landing_preserves_first_offer_and_reuses_existing_destination_lead(self):
        tracked = URL + '?srsltid=test'
        other = 'https://second-shop.example/headphones/black'
        self.openrouter = BuyerDecisions(['Inspect selected listing ' + URL, 'Search serpapi',
                                          'Inspect selected listing ' + tracked, 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        deps.research.workers.domestic_tools.search = lambda query, job, alternatives: SearchReport(
            job.run_id, (ChannelAttempt('serpapi', 'ok'),), (lead(tracked), lead(other)))
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage),
            environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.001'))

        def provider(endpoint, key, payload, **_):
            queried = payload['url']
            landed = other if queried == tracked else URL
            html = listing('Black', '48.00' if landed == other else '50.00').replace(URL, landed)
            if landed == other:
                html = html.replace('Fixture Audio', 'Second Audio')
            return dict(success=True, data=dict(rawHtml=html, metadata=dict(url=landed, sourceURL=queried,
                contentType='text/html', statusCode=200, provider=dict(creditsCost=1))))

        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', side_effect=provider) as send):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                refinement_note='Check ' + URL, allowed_colors=['black'])
        self.assertEqual([c.args[2]['url'] for c in send.call_args_list], [URL, tracked])
        self.assertEqual({(str(i.offer.listing_url), i.offer.seller.name, str(i.offer.price.value.amount))
                          for i in run.uncertain}, {(URL, 'Fixture Audio', '50.00'), (other, 'Second Audio', '48.00')})

    def journey(self, last, *, make_page=listing):
        self.openrouter = BuyerDecisions(['Search serpapi', 'Inspect', 'Select Black', 'Recheck', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        search = deps.research.workers.domestic_tools
        search.search = lambda query, job, alternatives: SearchReport(
            job.run_id, (ChannelAttempt('serpapi', 'ok'),), (lead(URL),))
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage),
            environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.001'))
        replies = iter([make_page('White', '39.00'), make_page('Black', '49.00'), last])

        def provider(endpoint, key, payload, **_):
            html = next(replies)
            return dict(success=True, data=dict(rawHtml=html, metadata=dict(url=URL, sourceURL=URL,
                        statusCode=200, contentType='text/html', provider=dict(creditsCost=1))))

        def browser(job, url, control, read):
            html = next(replies)
            if html is None:
                raise TimeoutError('fixture failure')
            body = html.encode()
            return ManagedPage(FetchOutcome(source_id_for('new-shop.example'), url, (url,), 1, 1,
                status_code=200, content_type='text/html', final_url=url, body=body,
                observed_at=datetime.now(timezone.utc), response_sha256=sha256(body).hexdigest()), 'playwright', None)
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.agent_inspection.select_listing', side_effect=browser) as clicks,
              patch('app.firecrawl.post_json', side_effect=provider) as calls):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                allowed_colors=['black'], fulfillment=dict(destination=dict(city='Batroun')))
        self.assertEqual(calls.call_count, 1, self.trace(run))
        payloads = [call.args[2] for call in calls.call_args_list]
        self.assertNotIn('actions', payloads[0])
        self.assertEqual([(call.args[1], call.args[2][0].value) for call in clicks.call_args_list],
                         [(URL, 'Black'), (URL, 'Black')])
        self.assertEqual(self.trace(run)['actions'][1]['status'], 'no_match')
        self.assertFalse(any(u.offer.variant.color.value == 'White' for snap in self.published for u in snap.uncertain))
        return run

    def test_first_seen_search_selection_and_failed_recheck_keeps_original_receipt(self):
        run = self.journey(None)
        self.assertEqual(run.status, 'partial', self.trace(run))
        item, = run.uncertain
        self.assertEqual(str(item.offer.listing_url), URL)
        self.assertEqual(str(item.offer.price.value.amount), '49.00')
        self.assertEqual(item.offer.variant.color.value, 'Black')
        self.assertEqual(item.cost.status, 'incomplete')
        self.assertFalse(run.recommendations)
        self.assertIn('further listing read failed', run.stop_reason)
        initial = next(s.uncertain[0] for s in self.published if s.uncertain)
        self.assertEqual(initial, item)

    def test_successful_recheck_returning_wrong_selection_withdraws_the_partial(self):
        run = self.journey(listing('White', '39.00'))
        self.assertEqual(run.status, 'abstained', self.trace(run))
        self.assertTrue(any(s.uncertain for s in self.published))
        self.assertFalse(run.uncertain or run.recommendations)

    def test_shop_with_only_visible_text_and_no_product_feed_or_sku(self):
        def page(color, price):
            controls = ''.join(f'<input type="radio" name="color" value="{value}"'
                               + (' checked' if color == value else '') + '>' for value in ('Black', 'White'))
            return ('<h1>Soundcore Q20i Hybrid ANC Headphones</h1><p>Sold by Fixture Audio</p>'
                    '<p>Fixture Audio, Beirut, Lebanon</p><p>Condition: New</p><p>In stock</p>'
                    f'<p>USD {price}</p><div hidden><p>USD 10.00</p></div>{controls}')
        run = self.journey(None, make_page=page)
        self.assertEqual(run.status, 'partial', self.trace(run))
        item, = run.uncertain
        self.assertEqual(str(item.offer.price.value.amount), '49.00')
        self.assertEqual(item.offer.variant.color.value, 'Black')
        self.assertEqual(next(e.excerpt for e in item.offer.evidence if e.field_path == 'price'), 'USD 49.00')

    def test_a_contradictory_display_price_withdraws_the_structured_price(self):
        run = self.journey(listing('Black', '49.00') + '<span data-variant-price>USD 72.00</span>')
        self.assertFalse(run.uncertain or run.recommendations)
        self.assertIn('could not confirm', run.stop_reason)

    def test_agent_can_choose_the_published_option_url_while_playwright_is_available(self):
        black_url = URL + '?variant=black'
        self.openrouter = BuyerDecisions(['Inspect', f'Inspect selected listing {black_url}', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage),
            environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.001'))

        def provider(endpoint, key, payload, **_):
            url = payload['url']
            self.assertNotIn('actions', payload)
            html = (listing('Black', '49.00') if url == black_url else listing('White', '39.00')
                    + '<a class="swatch" href="?variant=black">Black</a>')
            return dict(success=True, data=dict(rawHtml=html, metadata=dict(url=url, sourceURL=url,
                        contentType='text/html', statusCode=200, provider=dict(creditsCost=1))))

        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', side_effect=provider) as calls):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                refinement_note='Check ' + URL, allowed_colors=['black'])
        self.assertEqual([c.args[2]['url'] for c in calls.call_args_list], [URL, black_url])
        choices = [json.loads(r['body']['messages'][1]['content']).get('available', [])
                   for r in self.openrouter.requests]
        menu = next(m for m in choices if any(black_url in action for action in m))
        self.assertTrue(any(action.startswith('Select Black') for action in menu))
        self.assertNotIn('actions', calls.call_args_list[-1].args[2])
        self.assertEqual(run.status, 'partial', self.trace(run))
        item, = run.uncertain
        self.assertEqual(str(item.offer.listing_url), black_url)
        self.assertEqual(item.offer.variant.color.value, 'Black')
        self.assertEqual(str(item.offer.price.value.amount), '49.00')

    def test_identical_recheck_renews_the_receipt_without_redeciding_fit(self):
        run = self.journey(listing('Black', '49.00'))
        self.assertEqual(run.status, 'partial', self.trace(run))
        matches = [json.loads(r['body']['messages'][1]['content']) for r in self.openrouter.requests]
        self.assertEqual([m['selected']['color'] for m in matches if 'selected' in m], ['White', 'Black'])
        first = next(s.uncertain[0].offer for s in self.published if s.uncertain)
        self.assertGreater(run.uncertain[0].offer.evidence[0].observed_at, first.evidence[0].observed_at)

    def test_changed_price_replaces_prior_price_and_receipt(self):
        run = self.journey(listing('Black', '52.00'))
        self.assertEqual(str(run.uncertain[0].offer.price.value.amount), '52.00')
        seen = [s.uncertain[0].offer for s in self.published if s.uncertain]
        self.assertNotEqual(seen[0].evidence[0].response_sha256, seen[-1].evidence[0].response_sha256)
