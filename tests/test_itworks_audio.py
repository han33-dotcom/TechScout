"""Normal buyer API/worker with synthetic shop pages and a synthetic OpenRouter."""
from decimal import Decimal
from html import escape
import json

import httpx2

from app.adapters.itworks_audio import FAQ, ORIGIN, SELLER, SOURCE
from app.directory import import_domestic_listings
from app.domestic_buyer_sources import DomesticBuyerSources
from app.source_registry import DEFAULT_REGISTRY
from app.sources import SourceGate, SourceLimiter
from test_buyer_flow import FlowCase
from test_domestic_buyer import Router, Search


TITLE = 'Studio 123 Wireless On-Ear Headphones – Red'
HANDLE = 'studio-123-headphones'
URL = ORIGIN + '/products/' + HANDLE
VARIANT = 'gid://shopify/ProductVariant/42'


def script(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


class ITWorksAudio(FlowCase):
    def setUp(self):
        super().setUp()
        self.openrouter = Router()
        self.reads = []
        self.changed_stock = False
        self.delivery_fee = '2'
        self.listed_merchant = SELLER
        self.allowed_color = 'red'
        import_domestic_listings(self.directory)
        gate = SourceGate._for_tests(DEFAULT_REGISTRY, resolver=lambda *_: ['1.1.1.1'], limiter=SourceLimiter(),
                                     transport=httpx2.MockTransport(self.respond))
        self.buyer = self.deps()
        self.buyer.domestic_sources = DomesticBuyerSources(self.storage, gate, self.directory)
        self.buyer.research.workers.domestic_tools = Search(outage=True)

    def respond(self, request):
        url = str(request.url)
        self.reads.append(url)
        if url == URL:
            amount = '20.55' if self.reads.count(URL) == 1 else '21.55'
            line = dict(merchandiseId=VARIANT, quantity=1, selectedVariant=dict(id=VARIANT, availableForSale=True,
                title=TITLE, product=dict(title=TITLE,handle=HANDLE), selectedOptions=[],
                price=dict(currencyCode='USD',amount=amount)))
            form = escape(json.dumps(dict(action='LinesAdd',inputs=dict(lines=[line]))), quote=True)
            stock = 'OutOfStock' if self.changed_stock and self.reads.count(URL) > 1 else 'InStock'
            product = dict(name=TITLE, brand=dict(name='Studio'), itemCondition='https://schema.org/NewCondition',
                offers=dict(url=URL, seller=dict(name=self.listed_merchant), price=amount, priceCurrency='USD',
                    availability='https://schema.org/'+stock, shippingDetails={'@type':'OfferShippingDetails',
                    'shippingDestination':{'@type':'DefinedRegion','addressCountry':'LB'},
                    'shippingRate':{'@type':'MonetaryAmount','value':'2','currency':'USD'}}))
            body = (script({'@type':'Product',**product}) + script({'@type':'Store','name':SELLER,'url':ORIGIN,
                    'address':{'addressCountry':'LB','streetAddress':'Fixture street'}})
                    + f'<form action="/cart" method="post"><input type="hidden" name="cartFormInput" value="{form}">'
                      '<button type="submit">Add to Cart</button></form>')
        elif url == FAQ:
            answers = {'Do you deliver, and where?':'Yes — we deliver anywhere in Lebanon. Orders are usually with you within 3 days.',
                       'How much does delivery cost?':f"Delivery is a flat ${self.delivery_fee} anywhere in Lebanon — it's added as a separate line at checkout, so the price you see on the product is the price of the product.",
                       'What payment methods do you accept?':'All prices are in USD and include tax (TTC).'}
            body = script({'@type':'FAQPage','mainEntity':[dict(name=k,acceptedAnswer=dict(text=v)) for k,v in answers.items()]})
        elif request.url.path == '/search' and request.url.params.get('q') == 'Studio 123':
            body = f'<a href="/products/{HANDLE}">{TITLE}</a>'
        else:
            body = '<html></html>'
        raw = body.encode()
        return httpx2.Response(200, headers={'content-type':'text/html','content-length':str(len(raw))},
                               stream=httpx2.ByteStream(raw))

    def submit(self):
        _, run = self.browse(self.buyer, intent=dict(kind='exact_model',model='Studio 123'), allowed_colors=[self.allowed_color],
            requirements=dict(category='audio'), fulfillment=dict(mode='delivery',destination=dict(city='Beirut')))
        return run

    def test_payable_total_uses_final_price_and_actual_merchant(self):
        run = self.submit()
        self.assertEqual(run.status, 'complete', (run.stop_reason, run.source_outcomes))
        verified = run.recommendations[0].primary
        self.assertEqual(verified.cost.total.amount, Decimal('23.55'))
        self.assertEqual(verified.offer.route.origin_country, 'ZZ')
        self.assertEqual((verified.offer.source_id, str(verified.offer.listing_url)), (SOURCE, URL))
        self.assertEqual((self.reads.count(URL), self.reads.count(FAQ)), (2, 2))

    def test_final_stock_change_abstains(self):
        self.changed_stock = True
        run = self.submit()
        self.assertFalse(run.recommendations)
        self.assertTrue(run.uncertain)

    def test_conflicting_policy_rate_is_unknown(self):
        self.delivery_fee = '8'
        run = self.submit()
        self.assertFalse(run.recommendations)
        shipping = next(c for c in run.uncertain[0].offer.costs if c.kind == 'shipping')
        self.assertEqual(shipping.state, 'unknown')

    def test_other_merchant_cannot_borrow_store_domicile(self):
        self.listed_merchant = 'Other Marketplace Merchant'
        self.assertFalse(self.submit().recommendations)

    def test_different_requested_color_cannot_qualify(self):
        self.allowed_color = 'green'
        self.assertFalse(self.submit().recommendations)
