"""Checkpoint 2: buyer API and worker, with offline model and Firecrawl transports."""
from functools import partial
import json
import httpx2
from unittest.mock import patch

from app.agent_inspection import inspect_public_listing
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.onboarding import ActivationRegistry
from app.sources import SourceGate
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
            if html is None:
                raise TimeoutError('fixture failure')
            return dict(success=True, data=dict(rawHtml=html, metadata=dict(url=URL, sourceURL=URL,
                        statusCode=200, contentType='text/html', provider=dict(creditsCost=1))))
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch('app.firecrawl.post_json', side_effect=provider) as calls):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Soundcore Q20i', wording=WORDING),
                allowed_colors=['black'], fulfillment=dict(destination=dict(city='Batroun')))
        self.assertEqual(calls.call_count, 3, self.trace(run))
        payloads = [call.args[2] for call in calls.call_args_list]
        self.assertNotIn('actions', payloads[0])
        for payload in payloads[1:]:
            self.assertEqual(payload['url'], URL)
            self.assertEqual(payload['actions'][0], dict(type='click',
                             selector='input[type="radio"][name="color"][value="Black"]'))
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

    def test_after_a_click_is_denied_agent_follows_the_published_option_url(self):
        black_url = URL + '?variant=black'
        self.openrouter = BuyerDecisions(['Inspect', 'Select Black', f'Inspect selected listing {black_url}', 'Present'])
        deps = development_tools(self.deps(), 'Q20i')
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage),
            environ=dict(FIRECRAWL_API_KEY='fixture', FIRECRAWL_SCRAPE_USD_PER_CREDIT='.001'))

        def provider(endpoint, key, payload, **_):
            url = payload['url']
            if 'actions' in payload:
                httpx2.Response(403, request=httpx2.Request('POST', endpoint)).raise_for_status()
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
        self.assertEqual([c.args[2]['url'] for c in calls.call_args_list], [URL, URL, black_url])
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
