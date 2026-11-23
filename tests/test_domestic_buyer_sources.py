"""Buyer listing reads use a real source gate and an offline retailer transport."""
from datetime import timedelta
from hashlib import sha256
import json
import unittest

import httpx2

from app import contracts_v2 as v2
from app.contracts import SourcePolicy
from app.corpus_contracts import DocumentCapture, ResourceKey
from app.domestic_buyer_sources import DomesticBuyerSources, _Listing, _RUNTIME, _route_id
from app.domestic_catalog import ListingProfile, dispatch_representation, extract_listing
from app.domestic_retailer_profiles import COLLECTION, COLLECTION_DISPATCH, extract_dispatch_membership
from app.sources import SourceGate, SourceLimiter
import test_discovery as fixtures
from test_corpus import START


class DomesticBuyerSourceTests(unittest.TestCase):
    def setUp(self):
        fixtures.DiscoveryTests.setUp(self)
        self.url = fixtures.DiscoveryTests.seller(self).url
        owner = self.directory.get('store-0')
        self.directory.upsert(owner.model_copy(update={'role': 'domestic', 'updated_at': START + timedelta(seconds=1)}))
        self.new_url = self.url.replace('/laptop', '/another-model')
        self.origin = self.url.split('/products/')[0]
        self.request = fixtures.request(fulfillment={'destination': {'country': 'LB', 'city': 'Beirut'}})
        self.run = v2.RunReference(run_id=self.job.run_id, run_revision=1, request_id=self.request.request_id,
                                   request_revision=self.request.revision)
        self.config['sources'][0]['endpoint_capabilities'] = [dict(
            capability_id='product', version='1', host='store-0.example', operation='product',
            expect='html', path=['products', {'kind': 'token', 'max_length': 40}])]
        self.registry.write_text(json.dumps(self.config), encoding='utf-8')
        self.sequence = []
        self.now = START + timedelta(hours=1)
        def reply(request):
            self.seen.append(str(request.url))
            body = self.sequence.pop(0)
            return httpx2.Response(200, headers={'content-type': 'text/html', 'content-length': str(len(body))},
                                   stream=httpx2.ByteStream(body))
        self.gate = SourceGate._for_tests(self.registry, resolver=lambda *_: ['8.8.8.8'],
                                          wall_clock=lambda: self.now, limiter=SourceLimiter(),
                                          transport=httpx2.MockTransport(reply))
        self.sources = DomesticBuyerSources(self.storage, self.gate, self.directory)
        self.storage.save_source(SourcePolicy.model_validate(self.config['sources'][0]))
        self.product = dict({'@type': 'Product', 'sku': 'X-16', 'productID': 'X', 'url': self.new_url,
                             'brand': {'@type': 'Brand', 'name': 'Lenovo'}, 'model': 'ThinkPad',
                             'name': 'ThinkPad', 'category': 'laptop', 'color': 'black',
                             'additionalProperty': [{'@type': 'PropertyValue', 'name': 'RAM', 'value': '16 GB'}]},
                            offers={'@type': 'Offer', 'url': self.new_url, 'price': '100.75', 'priceCurrency': 'USD',
                                    'availability': 'https://schema.org/InStock',
                                    'itemCondition': 'https://schema.org/NewCondition'})

    @staticmethod
    def page(product):
        return ('<script type="application/ld+json">' + json.dumps(product) + '</script>').encode()

    def read(self, *, room=60, url=None, **kwargs):
        return self.sources.read(self.request, url or self.new_url, self.job, self.run,
                                 room=lambda: room, **kwargs)

    def test_new_product_at_reviewed_store_is_read_before_any_seed_or_web_search(self):
        self.sequence.append(self.page(self.product))
        result = self.read()
        self.assertEqual((result.status, result.source_id), ('ok', 'store-0'))
        self.assertEqual(self.seen, [self.new_url])
        offer, = result.offers
        self.assertEqual((offer.seller.seller_id, offer.source_product_id, offer.source_variant_id),
                         ('store-0', 'X', 'X-16'))
        self.assertEqual(str(offer.price.value.amount), '100.75')
        self.assertTrue(offer.stock.value)
        self.assertEqual(offer.route.origin_country, 'ZZ')
        self.assertEqual(offer.route.item_eligible.state, 'unknown')
        self.assertEqual([c.state for c in offer.costs], ['unknown', 'unknown'])
        self.assertEqual(offer.purchasable.state, 'unknown')
        self.assertTrue(offer.evidence)
        self.assertTrue(all(e.run == self.run and (e.response_sha256, e.observed_at) in result.replies
                            for e in offer.evidence))
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][0], 1)

    def test_foreign_dispatch_remains_distinct_from_a_delivery_destination(self):
        self.product['offers']['shippingDetails'] = {
            '@type': 'OfferShippingDetails',
            'shippingOrigin': {'@type': 'DefinedRegion', 'addressCountry': 'US'},
            'shippingDestination': {'@type': 'DefinedRegion', 'addressCountry': 'LB'},
            'shippingRate': {'@type': 'MonetaryAmount', 'value': '5.40', 'currency': 'USD'}}
        self.sequence.append(self.page(self.product))
        offer, = self.read().offers
        self.assertEqual(offer.route.origin_country, 'US')
        self.assertTrue(offer.route.item_eligible.value)
        self.assertEqual({e.field_path for e in offer.evidence if e.scope == 'route'},
                         {'route.origin_country', 'route.item_eligible', 'costs.order.shipping'})
        shipping, = (cost for cost in offer.costs if cost.kind == 'shipping')
        self.assertEqual(str(shipping.amount.amount), '5.40')

    def seed_laptop(self):
        descriptor = _Listing('store-0', 'store-0', 'store-0', 'store-0', (), self.new_url,
                              'X', 'X-16', 'laptop', 'Lenovo', 'ThinkPad')
        old_body = self.page(self.product)
        destination = self.request.fulfillment.destination
        capture = DocumentCapture(key=ResourceKey(source_id='store-0', url=self.new_url,
            representation='domestic-listing/laptop/store-0/store-0',
            product_id='X', variant_id='X-16', route_id=_route_id(destination)), final_url=self.new_url,
            visibility='public', origin='bootstrap', observed_at=START, checked_at=START, body=old_body,
            relevant_content=old_body, processing=_RUNTIME)
        self.corpus.ingest(capture, lambda page: extract_listing(page, descriptor, destination=destination))
        return old_body

    def test_pickup_only_one_branch_and_no_delivery_stock_substitution(self):
        node = self.product['offers']
        node.update(availableDeliveryMethod='https://schema.org/OnSitePickup',
                    availableAtOrFrom={'@type':'Store', 'branchCode':'hamra',
                                       'address':{'addressCountry':'LB','addressLocality':'Beirut'}})
        self.request = fixtures.request(fulfillment=dict(mode='pickup', branch_id='hamra',
                                                        destination=dict(country='LB',city='Beirut')))
        self.sequence.append(self.page(self.product))
        result = self.read()
        offer, = result.offers
        self.assertIsInstance(offer.route, v2.PickupRoute)
        self.assertEqual((offer.route.branch_id, offer.stock.value), ('hamra', True))
        receipts = [e for e in offer.evidence if e.evidence_id in offer.stock.evidence_ids]
        self.assertTrue(all(e.branch_id == 'hamra' and e.scope == 'branch' and e.run == self.run for e in receipts))
        self.assertIn('"branchCode": "hamra"', [e.excerpt for e in receipts])
        for mutation in ('different branch','online only'):
            if mutation == 'different branch':
                node['availableAtOrFrom']['branchCode'] = 'other'
            else:
                del node['availableDeliveryMethod']
            self.sequence.append(self.page(self.product))
            self.assertEqual(self.read().offers, ())

    def test_published_destination_range_is_an_estimate_and_missing_tax_stays_unknown(self):
        from app.domestic_buyer import domestic_assessment
        node = self.product['offers']
        node.update(shippingDetails=dict(shippingOrigin=dict(addressCountry='LB'),
                    shippingDestination=dict(addressCountry='LB',addressLocality='Beirut'),
                    shippingRate=dict(currency='USD',minValue='2',maxValue='5')),
                    priceSpecification=dict(valueAddedTaxIncluded=True))
        self.sequence.append(self.page(self.product))
        read = self.read()
        offer, = read.offers
        estimate = read.estimates[offer.offer_id]
        assessed = domestic_assessment(self.request, offer, self.now, estimates=estimate)
        self.assertEqual((assessed.cost.status,str(assessed.cost.lower.amount),str(assessed.cost.upper.amount)),
                         ('estimated','102.75','105.75'))
        del node['priceSpecification']
        self.sequence.append(self.page(self.product))
        read = self.read()
        offer, = read.offers
        self.assertEqual(domestic_assessment(self.request, offer, self.now,
                         estimates=read.estimates[offer.offer_id]).cost.status, 'incomplete')

    def test_bare_product_link_with_one_seed_cannot_select_among_multiple_live_skus(self):
        self.seed_laptop()
        self.request = fixtures.request(intent={'kind':'product_link', 'url':self.new_url})
        historical, = self.sources.lookup(self.request)
        other = dict(self.product, sku='X-32')
        # The other SKU is still a choice when it lacks a price or enough facts to build an offer.
        other.pop('brand')
        other.pop('offers')
        for earlier in (None, historical):
            with self.subTest(held_catalog_sku=bool(earlier)):
                self.sequence.append(self.page([self.product, other]))
                result = self.read(earlier=earlier)
                self.assertEqual((result.status, result.offers), ('failed', ()))
                self.assertIn('Select a variant', result.reason)
        self.sequence.append(self.page(self.product) +
            b'<form class="variations_form" data-product_id="X" data-product_variations="false"></form>')
        self.assertIn('Select a variant', self.read(earlier=historical).reason)

    def test_current_sku_refresh_never_reuses_an_earlier_price_or_stock(self):
        old_body = self.seed_laptop()
        historical, = self.sources.lookup(self.request)
        self.assertTrue(all(e.run is None for e in historical.evidence))
        self.assertEqual(self.seen, [])
        del self.product['offers']['price']
        self.product['offers']['availability'] = 'https://schema.org/OutOfStock'
        self.sequence.append(self.page(self.product))
        current = self.read(earlier=historical)
        offer, = current.offers
        self.assertEqual(offer.price.state, 'unknown')
        self.assertFalse(offer.stock.value)
        self.assertEqual(offer.source_variant_id, historical.source_variant_id)
        self.assertNotIn((sha256(old_body).hexdigest(), START), current.replies)
        self.assertTrue(all(e.run == self.run for e in offer.evidence))

    def test_unknown_shop_policy_revocation_and_deadline_are_resolved_before_transfer(self):
        self.assertEqual(self.read(url='https://new-shop.example/products/a').status, 'unconfigured')
        self.assertEqual(self.read(url=self.origin + '/press/new-store').status, 'out_of_scope')
        self.assertEqual(self.read(room=2).status, 'refused')
        self.config['live_release']['admitted_source_ids'] = []
        self.registry.write_text(json.dumps(self.config), encoding='utf-8')
        self.assertEqual(self.read().status, 'out_of_scope')
        self.assertEqual(self.seen, [])

    def test_collection_dispatch_requires_selected_item_in_this_runs_second_reply(self):
        base = 'https://wincell.store/products/offline-example'
        variant_url = base + '?variant=42'
        policy = dict(self.config['sources'][0], id='wincell', name='WinCell', origin='https://wincell.store',
                      allowed_endpoints=[variant_url, COLLECTION], endpoint_capabilities=[])
        self.config['sources'].append(policy)
        self.config['live_release']['admitted_source_ids'].append('wincell')
        self.registry.write_text(json.dumps(self.config), encoding='utf-8')
        self.storage.save_source(SourcePolicy.model_validate(policy))
        record = self.directory.get('store-0').model_dump(mode='json')
        record.update(source_id='wincell', seller_id='wincell')
        record['entry'].update(retailer_id='wincell', name='WinCell', origin='https://wincell.store', categories=['audio'])
        self.directory.upsert(type(self.directory.get('store-0')).model_validate(record))
        item = ListingProfile(source_id='wincell', retailer_id='wincell', seller_id='wincell', seller_name='WinCell',
            seller_aliases=(), listing_url=variant_url, product_id='41', sku='42', category='audio',
            can_revisit=True, can_enumerate=False, capability=None)
        product = self.page({'@type':'ProductGroup', 'productGroupID':'41', 'url':base, 'brand':{'name':'Soundcore'},
            'hasVariant':[{'@type':'Product', 'name':'Anker Soundcore H30i Wireless On-Ear Headphones - Black',
                           'offers':{'@type':'Offer', 'url':variant_url, 'price':'90', 'priceCurrency':'USD',
                                     'availability':'https://schema.org/InStock'}}]})
        collection = (f'<h2>{COLLECTION_DISPATCH}</h2><article class="card"><a href="/products/offline-example">Item</a>'
                      '<form><input name="id" value="42"><input name="product-id" value="41"></form></article>').encode()
        for url, body, rep, extract in ((variant_url, product, item.representation, extract_listing),
                (COLLECTION, collection, dispatch_representation(item), extract_dispatch_membership)):
            capture = DocumentCapture(key=ResourceKey(source_id='wincell', url=url, representation=rep,
                product_id='41', variant_id='42'), final_url=url, visibility='public', origin='bootstrap',
                observed_at=START, checked_at=START, body=body, relevant_content=body, processing=_RUNTIME)
            self.corpus.ingest(capture, lambda page: extract(page, item))
        self.request = fixtures.request(intent={'kind':'product_link','url':variant_url}, requirements={'category':'audio'})
        self.sequence.extend((product, collection))
        result = self.read(url=variant_url)
        offer, = result.offers
        self.assertEqual(offer.route.origin_country, 'LB')
        self.assertEqual(len(result.replies), 2)
        self.assertEqual(len(result.dispatch_replies), 1)
        self.assertEqual(self.seen, [variant_url, COLLECTION])
        self.assertEqual(offer.route.item_eligible.state, 'unknown')
        self.assertTrue(all(e.run == self.run for e in offer.evidence))
        self.sequence.extend((product, collection.replace(b'value="42"', b'value="43"')))
        refreshed = self.read(url=variant_url, earlier=offer)
        self.assertEqual(refreshed.offers[0].route.origin_country, 'ZZ')
        self.assertEqual(refreshed.dispatch_replies, frozenset())

    def test_related_product_and_unselected_multi_sku_link_do_not_verify(self):
        unrelated = dict(self.product, sku='Y-16', url=self.url)
        self.sequence.append(self.page(unrelated))
        self.assertEqual(self.read().status, 'failed')
        self.request = self.request.model_copy(update={'intent': v2.LinkIntent(kind='product_link', url=self.new_url)})
        other = dict(self.product, sku='Y-16')
        self.sequence.append(self.page([self.product, other]))
        self.assertEqual(self.read().status, 'failed')
