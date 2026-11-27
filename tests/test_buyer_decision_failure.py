"""Ordinary worker regressions for an unavailable decision after a retailer read."""
from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import patch

from app.firecrawl import ManagedPage
from app.shopping_agent import Decision, ShoppingAgent
from app.sources import FetchOutcome
from fixture_page_agent import fixture_read
from test_buyer_flow import FlowCase
from test_public_listing_buyer import URL, WORDING, listing
from test_shopping_agent import Decisions, development_tools


class FailureTests(FlowCase):
    def forced_failure(self, pages, *, partial=False):
        navigation = ['Inspect'] + ['Recheck'] * (len(pages) - 1)

        class Buyer(Decisions):
            def answer(self, role, data):
                if 'page' in data:
                    if partial:
                        return dict(fits=False, findings=[dict(statement='The pen is included.', quote='pen included')],
                                    reason='The page gives no price for the bundle.')
                    answer = fixture_read(data)
                    if answer.offer.color == 'White':
                        answer = answer.model_copy(update={'reason': 'Only the white variant is listed.'})
                    return answer.model_dump(mode='json')
                return super().answer(role, data)

        target = 'https://new-shop.example/tablets/pen-bundle' if partial else URL
        wording = 'Does this tablet include a pen and what is the price?' if partial else WORDING
        self.openrouter = Buyer(navigation)
        deps = development_tools(self.deps(), 'Q20i')
        bodies = iter(pages)
        def read(job, url, browser=None):
            html = next(bodies)
            if html is None:
                raise TimeoutError('fixture read timed out')
            body = html.encode()
            return ManagedPage(FetchOutcome('fixture', url, (url,), 1, 0,
                final_url=url, status_code=200, content_type='text/html', body=body,
                observed_at=datetime.now(timezone.utc), response_sha256=sha256(body).hexdigest()), 'firecrawl', None)
        deps.inspect_listing = read
        original = ShoppingAgent.generate
        def decide(agent, instructions, data, schema, by, **kw):
            if schema is Decision and len(agent.trace['actions']) >= len(navigation):
                return None
            return original(agent, instructions, data, schema, by, **kw)
        with patch.object(ShoppingAgent, 'generate', decide):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='tablet' if partial else 'audio'),
                intent=dict(kind='product_link', url=target, wording=wording), allowed_colors=[] if partial else ['black'])
        return run

    def test_priced_card_and_receipts_survive_model_failure_and_unreadable_refresh(self):
        run = self.forced_failure([listing('Black', '49.00'), None])
        offer, = [item.offer for item in run.uncertain]
        self.assertEqual(run.explanation.source, 'fallback')
        for value in (offer.seller.name, str(offer.listing_url), 'USD 49.00', 'in online stock'):
            self.assertIn(value, run.explanation.text)
        self.assertIn(offer.price.evidence_ids[0], run.explanation.evidence_ids)

    def test_partial_finding_reaches_answer_without_a_card(self):
        run = self.forced_failure(['<p>pen included</p>'], partial=True)
        self.assertFalse(run.uncertain)
        target = str(run.request.intent.url)
        for value in ('The pen is included.', 'no price', target):
            self.assertIn(value, run.explanation.text)
        review = self.trace(run)['checked_pages'][target]
        self.assertEqual(review['findings'][0]['quote'], 'pen included')
        self.assertEqual(review['findings'][0]['sha256'], review['sha256'])

    def test_failed_first_read_never_claims_availability(self):
        run = self.forced_failure([None])
        self.assertFalse(run.uncertain or run.recommendations)
        self.assertIn('could not read the retailer listing', run.explanation.text)
        self.assertIn(URL, run.explanation.text)

    def test_new_wrong_variant_removes_old_price_from_card_and_fallback(self):
        run = self.forced_failure([listing('Black', '49.00'), listing('White', '39.00')])
        self.assertFalse(run.uncertain or run.recommendations)
        self.assertIn('white variant', run.explanation.text)
        self.assertNotIn('49.00', run.explanation.text)
