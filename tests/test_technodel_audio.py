"""Offline price, identity and route boundaries for the public Technodel purchase component."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest

from app import contracts_v2 as v2
from app.adapters import technodel_audio as shop
from app.headset_identity import identify_headset


class WiredHeadsetTests(unittest.TestCase):
    def setUp(self):
        self.slug = 'studio-wired-x10-headset-black-123-456789'
        self.url = shop.ORIGIN + '/product/' + self.slug
        title = 'Studio Wired X10 Headset (Black) 123-456789'
        self.listing = dict(slug=self.slug, sku='TEST', title=title, brand='Studio', variants=[],
            category={'name':'Headsets'}, isVisible=True, priceDisplay='price', inStock=True, stock=2,
            displayPrice=19, currency='USD', specs=[dict(label=k,value=v) for k,v in
                [('Brand','Studio'),('Connectivity','Wired'),('Colour','Black')]])
        self.offer = {'seller': {'name': shop.SELLER,'url': shop.ORIGIN}, 'url':self.url, 'price':19,
            'priceCurrency':'USD', 'availability':'https://schema.org/InStock',
            'itemCondition':'https://schema.org/NewCondition', 'shippingDetails':{'@type':'OfferShippingDetails',
            'shippingDestination':{'@type':'DefinedRegion','addressCountry':'LB'},
            'shippingRate':{'@type':'MonetaryAmount','value':'4','currency':'USD'}}}
        self.product = {'@type':'Product','category':'Headsets','brand':{'name':'Studio'},'name':title,
                        'sku':'TEST','url':self.url,'offers':self.offer}
        self.store = {'@type':['ComputerStore','ElectronicsStore'], 'name':shop.SELLER,'url':shop.ORIGIN,
                      'address':{'addressLocality':'Fixture city','addressCountry':'LB'}}
        self.terms = {'What payment methods do you accept?':'We accept Cash on Delivery (COD), Wish Money transfer, '
                        'and Crypto (USDT, BTC, ETH). No hidden fees — what you see is what you pay.',
                      'What are the shipping costs?':'Delivery is a flat $4 anywhere in Lebanon — Beirut, South, '
                        'North, Bekaa and Mount Lebanon alike — and free on orders of $500 or more.',
                      'How long does shipping take within Lebanon?':'We deliver to all Lebanese regions. Same-day '
                        'delivery to Beirut, Sour, Tripoli and surroundings. Every other region: 1–2 days, never more.'}

    def document(self, url, nodes, component=''):
        html = ''.join('<script type="application/ld+json">'+json.dumps(node)+'</script>' for node in nodes) + component
        return SimpleNamespace(body=html.encode(), final_url=url, origin_url=url, status='ok',
            response_sha256=sha256(html.encode()).hexdigest(), observed_at=datetime.now(timezone.utc))

    def read(self, category='audio'):
        data = '1:' + json.dumps(['$','$L1',None,dict(product=self.listing)],separators=(',',':'))
        component = '<script>self.__next_f.push('+json.dumps([1,data])+')</script>'
        policy = {'@type':'FAQPage','mainEntity':[{'name':q,'acceptedAnswer':{'text':a}} for q,a in self.terms.items()]}
        destination = v2.DeliveryDestination(country='LB',city='Beirut')
        return shop.read_offer(self.document(self.url,[self.product,self.store],component),
            self.document(shop.FAQ,[policy]), SimpleNamespace(fulfillment=SimpleNamespace(destination=destination), requirements=SimpleNamespace(category=category)),
            SimpleNamespace(entry=SimpleNamespace(name=shop.SELLER,retailer_id=shop.SOURCE),seller_id=shop.SOURCE),
            v2.RunReference(run_id='run',request_id='request',request_revision=1,run_revision=1),'route')

    def test_search_returns_only_matching_bounded_product_links(self):
        html = f'<a href="/product/{self.slug}">headset</a><a href="/product/wrong-x100-123-456789">other</a>'
        self.assertEqual(shop.product_links(html.encode(),'Studio X10'), (self.url,))
        self.assertFalse(shop.product_links(html.encode(),'Studio X10 Pro'))

    def test_laptop_search_chooses_a_linked_category_match_then_page_proves_specs(self):
        cards = [
            dict(slug='fixture-32gb-drive', title='32GB USB drive', category={'name':'Storage'}),
            dict(slug='fixture-32gb-book', title='Fixture Book 32GB 1TB SSD', category={'name':'Laptops'}),
            dict(slug='absent-32gb-book', title='32GB 1TB SSD', category={'name':'Laptops'}),
        ]
        frame = '7:' + json.dumps([['$','component',None,dict(products=cards)]], separators=(',', ':'))
        html = ('<a href="/product/fixture-32gb-drive"></a><a href="/product/fixture-32gb-book"></a>'
                '<script>self.__next_f.push(' + json.dumps([1, frame]) + ')</script>')
        self.assertEqual(shop.listing_links(html.encode(), '32GB', '32GB RAM 1TB SSD', 'laptop'),
                         (shop.ORIGIN + '/product/fixture-32gb-book',))
        self.listing.update(title='Studio Book', category={'name':'Laptops'}, specs=[
            dict(label='RAM',value='16GB'), dict(label='Storage',value='1TB'), dict(label='Storage type',value='SSD')])
        self.product.update(name='Studio Book', category='Laptops')
        offer = self.read('laptop')
        self.assertEqual(offer.variant.specs.ram_gb.value, 16)
        self.assertEqual(offer.variant.specs.storage_gb.value, 1024)
        self.assertEqual(offer.variant.specs.storage_type.value, 'SSD')
        self.assertEqual(offer.costs[0].state, 'unknown')
        self.assertIsNone(self.read('monitor'))
        self.listing['title'] = self.product['name'] = 'Studio Book, 8GB RAM, 512GB SSD'
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.read('laptop')
        self.listing['specs'] = []
        self.assertEqual(self.read('laptop').variant.specs.storage_gb.value, 512)
        self.product['sku'] = 'other'
        self.assertIsNone(self.read('laptop'))

    def test_priced_selection_and_two_shipping_receipts(self):
        offer = self.read()
        self.assertEqual(offer.price.value.amount, Decimal(19))
        self.assertEqual(offer.costs[1].amount.amount, Decimal(4))
        self.assertEqual(len(offer.costs[1].evidence_ids), 2)
        self.assertTrue(offer.domestic_merchant_receipts())
        self.assertEqual(offer.variant.variant_id, 'technodel:TEST')

    def test_fractional_listing_keeps_the_price_and_requires_checkout_evidence(self):
        self.offer['price'] = '18.75'
        self.listing['displayPrice'] = '18.75'
        result = self.read()
        self.assertEqual(result.price.value.amount, Decimal('18.75'))
        self.assertEqual(next(cost.state for cost in result.costs if cost.kind == 'tax'), 'unknown')
        self.listing['displayPrice'] = '19'
        self.assertIsNone(self.read())
        self.offer['price'] = self.listing['displayPrice'] = '18.751'
        self.assertIsNone(self.read())

    def test_changed_checkout_price_and_unselected_variants_fail_closed(self):
        self.listing['displayPrice'] = 20
        self.assertIsNone(self.read())
        self.listing.update(displayPrice=19,variants=[{'sku':'OTHER'}])
        self.assertIsNone(self.read())

    def test_headset_first_title_checks_the_finish_and_keeps_tax_unknown(self):
        title = 'Studio Wired Audio Jack Headset X10 (Black) 123-456789'
        self.product['name'] = self.listing['title'] = title
        offer = self.read()
        self.assertEqual((offer.variant.product.model, offer.variant.color.value), ('Studio X10', 'Black'))
        self.assertEqual(offer.costs[0].state, 'unknown')
        self.listing['specs'][-1]['value'] = 'White'
        self.assertIsNone(self.read())

    def test_monitor_label_units_receipt_and_contrasting_minima(self):
        from app.eligibility import check_full_product
        from test_buyer_interpretation import form
        self.listing.update(category={'name':'Monitors'}, title='Fixture display')
        self.product.update(category='Monitors', name='Fixture display')
        for label in ('Refresh rate', 'Refresh Rate'):
            self.listing['specs'] = [dict(label=label, value='165Hz')]
            offer = self.read('monitor')
            fact = offer.variant.specs.refresh_hz
            self.assertEqual(fact.value, 165)
            receipt, = [e for e in offer.evidence if e.evidence_id in fact.evidence_ids]
            self.assertEqual((receipt.variant_id, receipt.excerpt), ('TEST', label + ': 165Hz'))
            for minimum, expected in ((144, 'pass'), (180, 'fail')):
                checks = check_full_product(form(f'A monitor with at least {minimum} Hz.', 'monitor'), offer).checks
                self.assertEqual(next(c.outcome for c in checks if c.name == 'refresh_hz'), expected)
        self.listing['specs'].append(dict(label='Refresh rate', value='240Hz'))
        self.assertEqual(self.read('monitor').variant.specs.refresh_hz.state, 'unknown')
        self.listing['specs'] = [dict(label='Refresh Rate', value='165 kHz')]
        self.assertEqual(self.read('monitor').variant.specs.refresh_hz.state, 'unknown')

    def test_out_of_stock_or_different_actual_merchant_cannot_qualify(self):
        self.listing['inStock'] = False
        self.assertIsNone(self.read())
        self.listing['inStock'] = True
        self.offer['seller']['name'] = 'Marketplace seller'
        self.assertIsNone(self.read())

    def test_conflicting_rate_and_missing_payment_terms_remain_unknown(self):
        self.offer['shippingDetails']['shippingRate']['value'] = 0
        del self.terms['What payment methods do you accept?']
        self.assertEqual([c.state for c in self.read().costs], ['unknown','unknown'])

    def test_model_alone_cannot_identify_hardware(self):
        self.assertIsNone(identify_headset('Logitech','H340','black'))
        self.assertIsNone(identify_headset('Logitech','H340','black',connector='usb-c'))
        self.assertIsNone(identify_headset('Logitech','H340 Pro','black',part='981-000475'))
        self.assertIsNone(identify_headset('Logitech','H340','white',part='981-000475'))
        self.assertEqual(identify_headset('Logitech','H340','Black',connector='usb-a'),
                         identify_headset('Logitech','H340','black',part='981-000475'))
