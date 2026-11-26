"""Which seller facts decide a verified domestic recommendation (#75 diagnosis).

One synthetic seller publishes every decisive fact for one phone configuration as
schema.org structured data. The normal domestic controller reads its page through the
source gate and the owned API/worker, and verifies a payable total. Removing one fact turns
the same run into an honest abstention that names it. Changing the delivery city also loses the
fee, because a shipping rate is read only for a matching destination. The same facts stated as
store-wide prose and a plain add-to-cart button are not item evidence under the current rules.
See docs/abstention-diagnosis-2026-11-24.md. Since #185 the seller rule replaces dispatch origin:
the reviewed first-party seller qualifies whatever its warehouse, so origin is not an ablation.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import httpx2

from app.contracts import SourcePolicy
from app.directory import Directory, DirectoryRecord
from app.domestic_buyer_sources import DomesticBuyerSources
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_domestic_buyer import Router, Search

ORIGIN = 'https://cedar.example'
URL = ORIGIN + '/products/demo-phone-x-256'
INTENT = dict(kind='exact_model', model='Demo Phone X')
REQUIREMENTS = dict(category='phone', storage_gb=256)
FULFILLMENT = dict(mode='delivery', destination=dict(city='Beirut'))

# Every fact the fixed rules need for a delivered, new phone with a verified total.
PAGE = {
    '@context': 'https://schema.org', '@type': 'Product', 'url': URL, 'name': 'Demo Phone X 256 GB',
    'model': 'Demo Phone X', 'brand': {'@type': 'Brand', 'name': 'Demo'}, 'productID': 'demo-phone-x',
    'sku': 'DPX-256-BLK', 'mpn': 'DPX-256', 'category': 'Mobile phones',
    'additionalProperty': [
        {'@type': 'PropertyValue', 'name': 'Storage', 'value': '256 GB'},
        {'@type': 'PropertyValue', 'name': 'Purchasable', 'value': True},
    ],
    'offers': {
        '@type': 'Offer', 'url': URL, 'price': '899.00', 'priceCurrency': 'USD',
        'availability': 'https://schema.org/InStock',
        'itemCondition': 'https://schema.org/NewCondition',
        'priceSpecification': {'@type': 'UnitPriceSpecification', 'price': '899.00', 'priceCurrency': 'USD',
                               'valueAddedTaxIncluded': True},
        'shippingDetails': {
            '@type': 'OfferShippingDetails',
            'shippingOrigin': {'@type': 'DefinedRegion', 'addressCountry': 'LB'},
            'shippingDestination': {'@type': 'DefinedRegion', 'addressCountry': 'LB', 'addressLocality': 'Beirut'},
            'shippingRate': {'@type': 'MonetaryAmount', 'value': '3.00', 'currency': 'USD'},
        },
    },
}
# What real Lebanese pages typically say instead: true statements, but store-wide prose
# and an add-to-cart button that the structured reader does not bind to the SKU or route.
STORE_WIDE = ('<p>All our products are brand new and sealed.</p>'
              '<p>We ship from Beirut to all of Lebanon for $3. Prices include VAT.</p>'
              '<form action="/cart/add"><input type="hidden" name="id" value="DPX-256-BLK">'
              '<button type="submit">Add to cart</button></form>')


def without(*path):
    def change(page):
        node = page
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1])
    return change


def drop_purchasable(page):
    page['additionalProperty'] = [p for p in page['additionalProperty'] if p['name'] != 'Purchasable']


def other_city(page):
    page['offers']['shippingDetails']['shippingDestination']['addressLocality'] = 'Tripoli'


def prose_only(page):
    for key in ('itemCondition', 'priceSpecification', 'shippingDetails'):
        page['offers'].pop(key)
    drop_purchasable(page)


# Each removal, and a phrase the buyer's uncertain alternative must then carry.
ABLATIONS = {
    'condition': (without('offers', 'itemCondition'), 'condition'),
    'purchasable': (drop_purchasable, 'Purchase availability'),
    'stock': (without('offers', 'availability'), 'Stock lacks a current response'),
    # A rate is read only for a matching destination, so this also loses the fee.
    'delivery to the buyer city': (other_city, 'item delivery eligibility'),
    'delivery fee': (without('offers', 'shippingDetails', 'shippingRate'), 'costs.order.shipping'),
    'tax treatment': (without('offers', 'priceSpecification'), 'costs.order.tax'),
}


class DecisiveFacts(FlowCase):
    def setUp(self):
        super().setUp()
        self.openrouter = Router()
        self.page, self.prose = deepcopy(PAGE), ''
        registry = Path(self.traces).parent / 'decisive-sources.json'
        directory = Directory(self.storage, registry_path=registry)
        now = datetime.now(timezone.utc) - timedelta(hours=1)
        policy = SourcePolicy.model_validate(dict(
            id='cedar', name='Cedar', origin=ORIGIN, status='active', reviewed_at=now.date(),
            admission=dict(basis='Offline fixture', scope='Exact page', evidence=[URL], reviewed_at=now),
            allowed_endpoints=[URL], limits=dict(requests_per_minute=120, concurrency=2, max_requests_per_run=6)))
        self.storage.save_source(policy)
        directory.upsert(DirectoryRecord(
            entry=dict(retailer_id='cedar', name='Cedar', origin=ORIGIN, markets=['LB'], categories=['phone'],
                       discovery_status='discovered'),
            role='domestic', background_collection=True, updated_at=now, source_id='cedar',
            seller_identity='retailer', seller_id='cedar', source_country='LB', product_urls=[URL]))
        registry.write_text(json.dumps(dict(sources=[policy.model_dump(mode='json')],
                                            live_release=dict(admitted_source_ids=['cedar']))))
        self.reads = []

        def reply(request):
            self.reads.append(str(request.url))
            body = ('<html><body><script type="application/ld+json">' + json.dumps(self.page) + '</script>'
                    + self.prose + '</body></html>').encode()
            return httpx2.Response(200, headers={'content-type': 'text/html', 'content-length': str(len(body))},
                                   stream=httpx2.ByteStream(body))
        gate = SourceGate._for_tests(registry, resolver=lambda *_: ['1.1.1.1'], limiter=SourceLimiter(),
                                     transport=httpx2.MockTransport(reply))
        self.buyer = self.deps()
        self.buyer.domestic_sources = DomesticBuyerSources(self.storage, gate, directory)
        self.buyer.research.workers.directory = directory
        # No second seller exists, and public search is down: one seller must stand on its own facts.
        self.buyer.research.workers.domestic_tools = Search(outage=True)

    def submit(self, request_id, fulfillment=FULFILLMENT):
        # Each submission is an independent buyer session against a cold corpus.
        self.client.cookies.clear()
        session = self.client.get('/api/session').json()
        self.headers['X-CSRF-Token'] = session['csrf_token']
        _, run = self.browse(self.buyer, request_id=request_id, intent=INTENT,
                             requirements=REQUIREMENTS, fulfillment=fulfillment)
        return run

    def test_reviewed_seller_serving_tripoli_qualifies_whatever_its_warehouse(self):
        """Scenario 10 (#185) through the real page reader. The earlier rule needed shippingOrigin LB."""
        for warehouse in (None, 'US'):
            with self.subTest(warehouse=warehouse):
                self.page = deepcopy(PAGE)
                other_city(self.page)
                details = self.page['offers']['shippingDetails']
                if warehouse:
                    details['shippingOrigin']['addressCountry'] = warehouse
                else:
                    details.pop('shippingOrigin')
                run = self.submit(f'tripoli-{warehouse}', dict(mode='delivery', destination=dict(city='Tripoli')))
                self.assertEqual(run.status, 'complete', run.stop_reason)
                verified = run.recommendations[0].primary
                offer = verified.offer
                self.assertEqual((offer.seller.name, offer.seller.first_party, offer.domestic_seller()),
                                 ('Cedar', True, 'first_party'))
                self.assertEqual((offer.route.origin_country, offer.route.destination.city,
                                  offer.route.item_eligible.value), (warehouse or 'ZZ', 'Tripoli', True))
                # USD 899 listed (VAT included) plus the USD 3 published Tripoli fee, counted once.
                shipping, = [c for c in offer.costs if c.kind == 'shipping']
                self.assertEqual((offer.price.value.amount, shipping.amount.amount), (Decimal('899.00'), Decimal('3.00')))
                self.assertEqual(verified.cost.total.amount, Decimal('902.00'))
                self.assertFalse(any(c.kind in ('duty', 'customs_handling') for c in offer.costs))

    def test_published_decisive_facts_verify_a_payable_total(self):
        run = self.submit('all-facts')
        self.assertEqual(run.status, 'complete', run.stop_reason)
        self.assertEqual(run.coverage.qualifying, 1)
        self.assertIn('1 offer(s) qualified', run.stop_reason)
        self.assertIn('two-seller comparison', run.stop_reason)
        group, = run.recommendations
        verified = group.primary
        offer = verified.offer
        # USD 899 with VAT included, plus the USD 3 published fee for delivery to Beirut.
        self.assertEqual(verified.cost.total.amount, Decimal('902.00'))
        self.assertEqual(verified.cost.total.currency, 'USD')
        self.assertEqual((offer.condition.value, offer.purchasable.value, offer.stock.value), ('new', True, True))
        self.assertEqual((offer.route.origin_country, offer.route.item_eligible.value), ('LB', True))
        self.assertEqual({c.kind: c.state for c in offer.costs}, {'tax': 'included', 'shipping': 'additional'})
        # Every decisive receipt comes from a response the gate returned in this run.
        self.assertEqual(verified.verification.run, run.run)
        self.assertTrue(all(e.run == run.run for e in offer.evidence))
        self.assertIn(URL, self.reads)

    def test_each_missing_fact_leaves_an_honest_unresolved_case(self):
        for name, (change, phrase) in ABLATIONS.items():
            with self.subTest(missing=name):
                self.page = deepcopy(PAGE)
                change(self.page)
                run = self.submit('missing-' + name.replace(' ', '-'))
                self.assertEqual(run.status, 'abstained', run.stop_reason)
                self.assertEqual((run.recommendations, run.coverage.qualifying), ((), 0))
                alternative, = run.uncertain
                self.assertTrue(any(phrase in reason for reason in alternative.reasons),
                                (name, alternative.reasons))

    def test_store_wide_prose_is_not_item_evidence(self):
        # Current rule: a store-wide sentence is not bound to this SKU or delivery route.
        self.page = deepcopy(PAGE)
        prose_only(self.page)
        self.prose = STORE_WIDE
        run = self.submit('store-wide-prose')
        self.assertEqual(run.status, 'abstained', run.stop_reason)
        alternative, = run.uncertain
        offer = alternative.offer
        self.assertEqual((offer.condition.state, offer.purchasable.state), ('unknown', 'unknown'))
        self.assertEqual((offer.route.origin_country, offer.route.item_eligible.state), ('ZZ', 'unknown'))
        self.assertTrue(all(c.state == 'unknown' for c in offer.costs))
        # Price and stock stay structured, so this is a fact gap, not a failed read.
        self.assertEqual((offer.price.state, offer.stock.value), ('known', True))
