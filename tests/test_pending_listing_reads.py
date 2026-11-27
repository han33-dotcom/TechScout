"""Slow Firecrawl response body through the normal request API, worker and offer store."""
from decimal import Decimal
from functools import partial
import json
from threading import Event
from unittest.mock import patch
from unittest import TestCase

import httpx2

from app.agent_inspection import inspect_public_listing
from app.domestic_discovery import ChannelAttempt, SearchReport
from app.firecrawl import Firecrawl
from app.onboarding import ActivationRegistry
from app.pending_listing_reads import PendingListingReads
from app.provider_http import request_json
from app.sources import SourceGate
from fixture_page_agent import fixture_read
from test_buyer_flow import FlowCase
from test_domestic_buyer import lead
from test_public_listing_buyer import URL, listing
from test_shopping_agent import Decisions, development_tools


class TransferLifetimeTests(TestCase):
    def test_closing_during_transfer_discards_the_body_and_releases_worker(self):
        headers_received, continue_body = Event(), Event()

        class Phase:
            stop = Event()
            cancel = stop.set

        class Body(httpx2.SyncByteStream):
            def __iter__(self):
                headers_received.set()
                if not continue_body.wait(5):
                    raise AssertionError('Buyer did not close the transfer')
                yield b'{}'

        def read(job, url, browser):
            response = httpx2.Response(200, headers={'content-type': 'application/json'}, stream=Body())
            return request_json('GET', url, headers={}, transport=httpx2.MockTransport(lambda _: response))

        reads = PendingListingReads(lambda: 0, capacity=1)
        transfer = reads.start(0, URL, Phase(), read)
        try:
            self.assertTrue(headers_received.wait(5))
            with self.assertRaisesRegex(ValueError, 'reader slots'):
                reads.start(1, URL + '/other', Phase(), read)
            events = reads.close()
            self.assertEqual(events[-1]['stage'], 'unconsumed_at_close')
        finally:
            continue_body.set()
        with self.assertRaises(InterruptedError):
            transfer.future.result(timeout=5)


class PendingReadTests(FlowCase):
    def test_slow_body_returns_after_second_seller_card_is_saved(self):
        slow = 'https://slow-shop.example/products/h111'
        fast = 'https://fast-shop.example/products/h111'
        wording = 'Compare two new in-stock Logitech H111 wired 3.5 mm stereo headsets before delivery.'
        release_body = Event()
        slow_views = []

        class Buyer(Decisions):
            def answer(self, role, data):
                if 'page' in data:
                    if data['url'] == slow:
                        slow_views.append(data['page'])
                        if len(slow_views) == 1:
                            raise httpx2.ReadTimeout('Interrupted fixture model response')
                    decision = fixture_read(data)
                    fits = 'Logitech H111 wired 3.5 mm stereo headset' in decision.offer.title
                    return decision.model_copy(update={'fits': fits}).model_dump(mode='json')
                menu = data['available']
                cards = data['checked_listings']
                if cards and len(cards) == 1:
                    assert cards[0]['url'] == fast
                    release_body.set()
                    return dict(action_index=menu.index('Wait for the next pending listing transfer'))
                if len(cards) == 2:
                    low, high = sorted(cards, key=lambda c: Decimal(c['listed_price']['amount']))
                    difference = Decimal(high['listed_price']['amount']) - Decimal(low['listed_price']['amount'])
                    answer = (f"{low['seller']} USD {low['listed_price']['amount']} {low['url']} is USD {difference} "
                              f"lower than {high['seller']} USD {high['listed_price']['amount']} {high['url']}.")
                    return dict(action_index=next(i for i, a in enumerate(menu) if a.startswith('Present')), answer=answer)
                return super().answer(role, data)

        class Transfer(httpx2.SyncByteStream):
            def __init__(self, url, body):
                self.url, self.body = url, body

            def __iter__(self):
                yield self.body[:10]
                if self.url == slow and not release_body.wait(15):
                    raise TimeoutError('The second seller was never reviewed')
                yield self.body[10:]

        def provider(request):
            url = json.loads(request.content)['url']
            price, seller = ('18.00', 'Slow Audio') if url == slow else ('21.00', 'Fast Audio')
            html = (listing('Black', price).replace(URL, url).replace('Fixture Audio', seller)
                    .replace('Soundcore Q20i Hybrid ANC Headphones', 'Logitech H111 wired 3.5 mm stereo headset')
                    .replace('Soundcore', 'Logitech').replace('Q20i-Black', 'H111'))
            body = dict(success=True, data=dict(rawHtml=html, metadata=dict(url=url, sourceURL=url,
                statusCode=200, contentType='text/html', scrapeId=seller, provider=dict(creditsCost=1))))
            return httpx2.Response(200, headers={'content-type': 'application/json'},
                                  stream=Transfer(url, json.dumps(body).encode()))

        self.openrouter = Buyer(['Search serpapi', 'Inspect selected listing ' + slow, 'Inspect selected listing ' + fast])
        deps = development_tools(self.deps(), 'H111')
        deps.research.workers.domestic_tools.search = lambda query, job, alternatives: SearchReport(
            job.run_id, (ChannelAttempt('serpapi', 'ok'),), (lead(slow), lead(fast)))
        deps.inspect_listing = partial(inspect_public_listing, registry=ActivationRegistry(self.storage))
        with (patch('app.agent_inspection.SourceGate', side_effect=lambda scope:
                    SourceGate._for_tests(scope, resolver=lambda *_: ['8.8.8.8'])),
              patch.object(Firecrawl, 'from_env', side_effect=lambda gate, env:
                           Firecrawl('fixture', gate, usd_per_credit='included', transport=httpx2.MockTransport(provider)))):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='H111', wording=wording))
        self.assertEqual({(str(i.offer.listing_url), i.offer.seller.name, i.offer.price.value.amount)
                          for i in run.uncertain},
                         {(slow, 'Slow Audio', Decimal('18.00')), (fast, 'Fast Audio', Decimal('21.00'))})
        self.assertTrue(all(i.offer.stock.value and i.offer.condition.value == 'new' for i in run.uncertain))
        self.assertEqual(run.explanation.text, f'Slow Audio USD 18.00 {slow} is USD 3.00 lower than Fast Audio USD 21.00 {fast}.')
        events = [(e['url'], e['stage']) for e in self.trace(run)['listing_transfers']]
        self.assertLess(events.index((slow, 'response_headers')), events.index((fast, 'review_finished')))
        self.assertLess(events.index((fast, 'review_finished')), events.index((slow, 'body_complete')))
        self.assertLess(events.index((slow, 'job_visible')), events.index((slow, 'capture_delivered')))
        self.assertEqual(events.count((slow, 'scrape_dispatched')), 1)
        self.assertEqual(events.count((slow, 'retry_page_view')), 1)
        self.assertEqual(len(slow_views), 2)
        self.assertEqual(slow_views[0], slow_views[1])
