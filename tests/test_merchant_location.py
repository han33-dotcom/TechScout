"""A buyer can associate a seller with independently observed premises in this run."""
from hashlib import sha256

import test_buyer_flow as flow
from test_listing_browser import retailer
from test_shopping_agent import Decisions, development_tools


PRODUCT = 'https://shop.example/products/headset'
CONTACT = 'https://shop.example/stores'
LISTING = '<h1>Logitech H111</h1><p>Sold by Fixture Audio. USD 13.00. New. In stock.</p>'
ADDRESS = '<h1>Fixture Audio</h1><p>Showroom: Ashrafieh, Beirut, Lebanon</p>'


class LocationChoices(Decisions):
    def __init__(self, pages, associate):
        super().__init__([f'Inspect selected listing {url}' for url in pages] + ['Present'])
        self.associate = associate

    def answer(self, role, data):
        if 'page' not in data:
            return super().answer(role, data)
        context = data['seller_evidence']
        if data['url'] == CONTACT:
            return dict(fits=False, seller_location=dict(merchant_quote='Fixture Audio',
                address_quote='Ashrafieh, Beirut, Lebanon',
                listing_indices=[0] if self.associate and context['listings'] else []))
        return dict(fits=True, offer=dict(merchant_address_quote=(context['locations'][0]['address_quote']
                if self.associate and context['locations'] else ''), title='Logitech H111', brand='Logitech', seller='Fixture Audio',
            price=dict(amount='13.00', currency='USD'), price_quote='USD 13.00', currency_quote='USD',
            condition='NewCondition', condition_quote='New', stock='InStock', stock_quote='In stock'),
            use_location_index=0 if self.associate and context['locations'] else None)


class MerchantLocationTests(flow.FlowCase):
    def check_pages(self, pages, associate=True):
        self.openrouter = LocationChoices(pages, associate)
        deps = development_tools(self.deps(), 'H111')
        with retailer(self.storage, PRODUCT, html=LISTING, links={CONTACT: ADDRESS}) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Logitech H111'),
                refinement_note='Check ' + ' and '.join(pages))
        self.assertEqual(reads, [('firecrawl', url) for url in pages])
        return run

    def assert_sources(self, run):
        item, = run.uncertain
        offer = item.offer
        self.assertEqual(offer.domestic_seller(), 'listing')
        self.assertEqual(str(offer.price.value.amount), '13.00')
        self.assertTrue(offer.stock.value)
        for receipt in offer.evidence:
            contact = receipt.field_path in ('seller.market', 'seller.location_identity')
            body, url = (ADDRESS, CONTACT) if contact else (LISTING, PRODUCT)
            self.assertIn(receipt.excerpt, body)
            self.assertEqual(str(receipt.final_url), url)
            self.assertEqual(receipt.response_sha256, sha256(body.encode()).hexdigest())
        return offer

    def test_product_then_contact_preserves_both_captures(self):
        offer = self.assert_sources(self.check_pages([PRODUCT, CONTACT]))
        for field, value in [('source_id', 'other-shop'), ('run', None), ('response_sha256', 'a' * 64)]:
            with self.subTest(field=field):
                changed = tuple(e.model_copy(update={field: value}) if e.field_path == 'seller.location_identity' else e
                                for e in offer.evidence)
                self.assertIsNone(offer.model_copy(update={'evidence': changed}).domestic_seller())

    def test_agent_can_choose_an_already_read_address(self):
        self.assert_sources(self.check_pages([CONTACT, PRODUCT]))

    def test_shared_host_alone_does_not_link_two_pages(self):
        run = self.check_pages([PRODUCT, CONTACT], associate=False)
        self.assertFalse(run.uncertain or run.recommendations)

    def check_deferred_product(self, item_fits):
        class Buyer(LocationChoices):
            def __init__(self):
                super().__init__([PRODUCT, CONTACT], True)
                self.sequence = iter(['Inspect selected listing ' + PRODUCT, 'Inspect selected listing ' + CONTACT,
                                      'Resume the captured listing', 'Present'])

            def answer(self, role, data):
                if 'page' not in data or data['url'] == CONTACT:
                    return super().answer(role, data)
                response = super().answer(role, data)
                has_location = bool(data['seller_evidence']['locations'])
                # The first assessment conflates the missing premises with the item check, as in the live run.
                response.pop('fits')
                response.pop('use_location_index', None)
                response['offer'].pop('merchant_address_quote')
                response.update(item_meets_buyer_requirements=has_location and item_fits,
                                reason='Product fit evaluated with available merchant evidence.')
                return response

        self.openrouter = Buyer()
        deps = development_tools(self.deps(), 'H111')
        with retailer(self.storage, PRODUCT, html=LISTING, links={CONTACT: ADDRESS}) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='exact_model', model='Logitech H111'), refinement_note='Check ' + PRODUCT + ' and ' + CONTACT)
        self.assertEqual(reads, [('firecrawl', PRODUCT), ('firecrawl', CONTACT)])
        return run

    def test_later_premises_allow_fresh_product_judgment_on_the_original_capture(self):
        self.assert_sources(self.check_deferred_product(True))

    def test_matching_merchant_cannot_promote_an_excluded_item(self):
        run = self.check_deferred_product(False)
        self.assertFalse(run.uncertain or run.recommendations)
