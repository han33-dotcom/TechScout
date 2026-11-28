"""Regression tests for the confirmed findings of the 28 Nov independent review of app/scout.

Each test pins one way the scout could have shown a buyer an unsupported or false fact, or read
something it must not. Offline only: synthetic shops and pages, no network, no model calls.
"""
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest

import httpx2

from app.scout.agent import _shrink, unsupported_amounts
from app.scout.evidence import Evidence, contains_quote
from app.scout.pages import snippets
from app.scout.storefronts import _woo_variant, money, product_from_html
from app.scout.verify import check_offers, hard_budget
from app.scout.web import FetchError, SafeWeb
from tests.test_scout import (GAMMA, NOW, PUBLIC, REQ, SHOPS, claim, final, make_product, page_obs,
                              product_obs)


class ReviewRegressionTests(unittest.TestCase):
    def test_budget_needs_a_currency_amount(self):
        for text in ('New iPhone 15 Pro Max 256GB delivered to Beirut', 'delivered within 3 days',
                     'a laptop with up to 1TB', 'AirPods Max under 2 years warranty'):
            self.assertIsNone(hard_budget(text), text)
        self.assertEqual(hard_budget('iPhone 15 Pro Max 256GB under 1300 USD'), Decimal('1300.00'))
        self.assertEqual(hard_budget('budget is $1,000 delivered'), Decimal('1000.00'))
        ev = Evidence()
        p = product_obs(ev, make_product(title='Apple iPhone 15 Pro Max 256GB', price='1199.00'))
        out = check_offers(final([claim(p.obs_id)]), ev, SHOPS,
                           dict(REQ, text='New iPhone 15 Pro Max 256GB delivered to Batroun'))
        self.assertEqual(out[0].tier, 'best')

    def test_summary_amounts_must_belong_to_a_checked_card(self):
        ev = Evidence()
        a = product_obs(ev, make_product(price='1049.00'))
        b = product_obs(ev, make_product(price='999.00', shop='beta'))
        offers = check_offers(final([claim(a.obs_id), claim(b.obs_id)]), ev, SHOPS, REQ)
        self.assertEqual(unsupported_amounts('Alpha has it for $1049 and Beta for $999.', offers, REQ), [])
        self.assertEqual(unsupported_amounts('Beta is $50 cheaper than Alpha.', offers, REQ), [])
        self.assertTrue(unsupported_amounts('Alpha adds $50 delivery.', offers, REQ))
        self.assertTrue(unsupported_amounts('Alpha has free delivery.', offers, REQ))
        self.assertTrue(unsupported_amounts('About 95,000,000 LBP at Alpha.', offers, REQ))
        # Conditional free-delivery wording describes a policy, so it is not flagged by itself; an amount
        # that no card or quoted fee rule backs still is.
        self.assertEqual(unsupported_amounts('Free delivery on orders over a minimum amount.', offers, REQ), [])
        self.assertTrue(unsupported_amounts('Free delivery on orders over $2000 applies.', offers, REQ))

    def test_free_delivery_needs_the_threshold_met_and_fees_need_currency_and_region(self):
        ev = Evidence()
        p = product_obs(ev, make_product())
        t = page_obs(ev, 'alpha', 'Free delivery on orders over $100. Delivery fee: $3 inside Beirut, $5 outside '
                                  'Beirut. Delivery within 2 days for $5.')
        cases = [dict(delivery_quote='Free delivery on orders over $100', delivery_fee_usd='0'),
                 dict(delivery_quote='Delivery fee: $3 inside Beirut, $5 outside Beirut', delivery_fee_usd='3'),
                 dict(delivery_quote='Delivery within 2 days for $5', delivery_fee_usd='2')]
        for case in cases:
            out = check_offers(final([claim(p.obs_id, delivery_obs=t.obs_id, **case)]), ev, SHOPS, REQ)
            self.assertIsNone(out[0].total, case)
        quote = 'Delivery fee: $3 inside Beirut, $5 outside Beirut'
        ok = check_offers(final([claim(p.obs_id, delivery_obs=t.obs_id, delivery_fee_usd='5', delivery_quote=quote)]),
                          ev, SHOPS, REQ)
        self.assertEqual(ok[0].total, Decimal('47.00'))
        none = check_offers(final([claim(p.obs_id, delivery_obs=t.obs_id, delivery_fee_usd='5', delivery_quote=quote)]),
                            ev, SHOPS, dict(REQ, destination=None))
        self.assertIsNone(none[0].total)

    def test_deadlines_are_never_met_by_an_estimate(self):
        ev = Evidence()
        p = product_obs(ev, make_product())
        t = page_obs(ev, 'alpha', 'Outside Beirut: $5, delivered in 2-4 business days. All prices include VAT.')
        req = dict(requirement='Delivered by Friday', status='met', evidence_obs=t.obs_id,
                   quote='delivered in 2-4 business days')
        out = check_offers(final([claim(p.obs_id, delivery_obs=t.obs_id, delivery_quote='Outside Beirut: $5',
                                        delivery_fee_usd='5', vat_quote='All prices include VAT',
                                        requirements=[req])]), ev, SHOPS, REQ)
        self.assertFalse(out[0].complete)
        self.assertTrue(any('not guaranteed' in g for g in out[0].gaps))

    def test_page_price_must_be_one_currency_amount(self):
        ev = Evidence()
        t = page_obs(ev, 'alpha', 'Lenovo IdeaPad 5 16GB 512GB Price: $699 Was: $799', url='https://alpha.example/l5')
        for quote in ('Lenovo IdeaPad 5 16GB', 'Price: $699 Was: $799'):
            c = claim(t.obs_id, variant_id='-', price_quote=quote, condition='unknown', condition_quote=None)
            self.assertEqual(check_offers(final([c]), ev, SHOPS, REQ), [], quote)
        c = claim(t.obs_id, variant_id='-', price_quote='Price: $699', condition='unknown', condition_quote=None)
        self.assertEqual(check_offers(final([c]), ev, SHOPS, REQ)[0].price, Decimal('699.00'))

    def test_published_variant_condition_wins(self):
        ev = Evidence()
        product = make_product()
        product.variants[0].condition = 'RefurbishedCondition'
        p = product_obs(ev, product)
        out = check_offers(final([claim(p.obs_id)]), ev, SHOPS, REQ)
        self.assertEqual((out[0].condition, out[0].tier), ('refurbished', 'rejected'))
        offers = [{'@type': 'Offer', 'price': '500', 'priceCurrency': 'USD', 'sku': 'N',
                   'itemCondition': 'https://schema.org/NewCondition'},
                  {'@type': 'Offer', 'price': '400', 'priceCurrency': 'USD', 'sku': 'R',
                   'itemCondition': 'https://schema.org/RefurbishedCondition'}]
        html = ('<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'name': 'Phone', 'offers': offers})
                + '</script>')
        page = SimpleNamespace(text=lambda: html, final_url='https://gamma.example/p/phone', sha256='s', observed_at=NOW)
        parsed_product, _ = product_from_html(page, GAMMA)
        self.assertIsNone(parsed_product.condition)
        self.assertEqual([v.condition for v in parsed_product.variants], ['NewCondition', 'RefurbishedCondition'])

    def test_vat_excluded_currency_unknown_zero_price_and_pickup(self):
        ev = Evidence()
        p = product_obs(ev, make_product())
        t = page_obs(ev, 'alpha', 'Outside Beirut: $5. Prices exclude VAT.')
        out = check_offers(final([claim(p.obs_id, delivery_obs=t.obs_id, delivery_quote='Outside Beirut: $5',
                                        delivery_fee_usd='5', vat_quote='Prices exclude VAT')]), ev, SHOPS, REQ)
        self.assertFalse(out[0].complete)
        self.assertTrue(any('VAT is added' in g for g in out[0].gaps))
        no_currency = make_product()
        no_currency.variants[0].currency = None
        q = product_obs(ev, no_currency)
        out = check_offers(final([claim(q.obs_id, delivery_obs=t.obs_id, delivery_quote='Outside Beirut: $5',
                                        delivery_fee_usd='5')]), ev, SHOPS, REQ)
        self.assertIsNone(out[0].total)
        self.assertIsNone(money('0.00'))
        pickup = check_offers(final([claim(p.obs_id)]), ev, SHOPS, dict(REQ, mode='pickup', destination='Beirut'))
        self.assertFalse(pickup[0].complete)

    def test_woocommerce_backorder_is_not_in_stock(self):
        v = _woo_variant(dict(id=1, name='X', prices=dict(price='100', currency_code='USD', currency_minor_unit=0),
                              is_in_stock=True, stock_availability={'text': 'Available on backorder',
                                                                    'class': 'available-on-backorder'}), {})
        self.assertIsNone(v.available)

    def test_trimmed_turns_keep_observation_and_variant_ids(self):
        variant = dict(variant_id='11', price='42.00', available=True, title='Black')
        details = [dict(obs='P1', kind='product', title='Phone', shop='Alpha', variants=[variant] * 3,
                        description='d' * 4000)]
        data = dict(turns=[dict(turn=1, results=[dict(call=dict(tool='search'), result=dict(
            hits=[dict(title='x' * 300)] * 15, details=details))])])
        _shrink(data, 600)
        text = json.dumps(data)
        self.assertIn('"P1"', text)
        self.assertIn('"11"', text)

    def test_snippet_quotes_match_the_capture(self):
        text = 'Delivery (less than 15kg):\nInside Beirut: 3$\nOutside Beirut: 5$'
        quote = snippets(text, ['Outside'])[0]['text'][-35:]
        self.assertTrue(contains_quote(text, quote))

    def test_off_site_redirects_and_hidden_cart_paths_are_refused(self):
        def handler(request):
            if request.url.path == '/robots.txt':
                return httpx2.Response(404)
            if request.url.path == '/moved':
                return httpx2.Response(302, headers={'location': 'https://other.example/products/x'})
            return httpx2.Response(200, text='ok', headers={'content-type': 'text/html'})
        w = SafeWeb(transport=httpx2.MockTransport(handler), resolver=lambda h, p: [PUBLIC], sleep=lambda s: None,
                    min_interval_s=0)
        with self.assertRaises(FetchError) as ctx:
            w.get('https://alpha.example/moved')
        self.assertEqual(ctx.exception.code, 'OFF_SITE')
        for url in ('https://alpha.example/%2563art', 'https://alpha.example/a/../cart',
                    'https://alpha.example/?add-to-cart=12', 'https://alpha.example/shop?wc-ajax=add_to_cart',
                    'https://alpha.example/x%2fcart'):
            with self.assertRaises(FetchError, msg=url):
                w.get(url)


class ShopTermsSpotCheckTests(unittest.TestCase):
    """Wording found in the real shop-terms extraction on 28 Nov (before the held-out freeze)."""

    def test_conditional_or_regional_free_delivery_is_never_a_zero_fee(self):
        from app.scout.verify import fee_problem
        price = Decimal('42.00')
        for quote, destination in [
                ('Free shipping on your first order', 'Beirut'),                       # Karizma
                ('Free delivery may apply to promotional campaigns or specific order values.', 'Beirut'),  # CompuOne
                ('Spend $1,500.00 more to reach free shipping!', 'Beirut'),            # Macrotronics cart text
                ('Free delivery in beirut', 'Tripoli'),                                # Mediatech
                ('Free standard shipping all over Beirut', 'Jounieh')]:                # I Fix LB
            self.assertIsNotNone(fee_problem(quote, Decimal('0'), None, price, destination), quote)
        self.assertIsNone(fee_problem('Free delivery in beirut', Decimal('0'), None, price, 'Hamra, Beirut'))
        self.assertIsNone(fee_problem('Free delivery all over Lebanon', Decimal('0'), None, price, 'Tripoli'))
        flat = 'Flat $4 anywhere in Lebanon; free on orders of $500 or more'
        self.assertIsNotNone(fee_problem(flat, Decimal('0'), Decimal('500'), price, 'Tripoli'))
        self.assertIsNone(fee_problem(flat, Decimal('0'), Decimal('500'), Decimal('600'), 'Tripoli'))
        self.assertIsNone(fee_problem(flat, Decimal('4'), None, price, 'Tripoli'))


class PreparedIndexTests(unittest.TestCase):
    """Found by the held-out run: whole-index scoring in Python stalled parallel searches."""

    def test_prepared_index_ranks_like_the_plain_score(self):
        from app.scout.catalog import PreparedIndex, score
        rows = [dict(title='Silicone Case for Samsung Galaxy S24 Ultra', url='u1'),
                dict(title='Samsung Galaxy S24 Ultra 12GB 256GB', url='u2', skus=['SM-S928B']),
                dict(title='Logitech H390 USB Computer Headset', url='u3'),
                dict(title='Apple iPad A16 11 inch 128GB Blue', url='u4')] * 50
        index = PreparedIndex(rows)
        for query in ('galaxy s24 ultra', 'logitech h390 usb', 'sm s928b', 'ipad a16 128gb'):
            fast = index.best(query, 3)
            plain = sorted(rows, key=lambda r: -score(query, r['title'], ' '.join(r.get('skus') or [])))
            self.assertEqual(fast[0][1]['url'], plain[0]['url'], query)
        self.assertEqual(index.best('nothing matches this', 3), [])


class HeldOutFindingTests(unittest.TestCase):
    """Fixes after the locked held-out run (28 Nov): each pins a graded finding (B05, B07, B11, C06)."""

    def test_ram_storage_pairs_are_read(self):
        from app.scout.verify import identity_conflicts, storage_values
        self.assertEqual(storage_values('13.6" 8/256gb / M2 / Midnight'), {8, 256})
        self.assertEqual(storage_values('Galaxy A56 8GB+256GB'), {8, 256})
        self.assertEqual(identity_conflicts('MacBook Air M2 13-inch 8 GB RAM 256 GB SSD Midnight',
                                            'MacBook Air 13.6" 8/256gb / M2 / Midnight', 'exact'), [])

    def test_tax_included_with_shipping_at_checkout_is_included(self):
        from app.scout.verify import vat_status
        self.assertEqual(vat_status('Tax included. Shipping calculated at checkout.', True), 'included')
        self.assertEqual(vat_status('Taxes and shipping calculated at checkout.', True), 'excluded')

    def test_unconfirmed_identity_requirement_is_not_an_exact_match(self):
        ev = Evidence()
        p = product_obs(ev, make_product(title='Apple iPad A16 128GB Blue'))
        req = dict(requirement='Wi-Fi only (not cellular)', status='unknown', evidence_obs=None, quote=None)
        out = check_offers(final([claim(p.obs_id, requirements=[req])]), ev, SHOPS,
                           dict(REQ, text='new blue iPad A16 Wi-Fi 128GB delivered to Batroun'))
        self.assertEqual((out[0].identity, out[0].tier), ('near', 'alternative'))

    def test_preorder_on_the_product_is_not_stock(self):
        ev = Evidence()
        product = make_product(title='iPhone 15 Pro 128GB')
        p = product_obs(ev, product)
        page_obs(ev, 'alpha', 'Menu | Home | -%Pre-order iPhone 15 Pro 128GB $1,080 | Add to cart',
                 url='https://alpha.example/products/x')
        out = check_offers(final([claim(p.obs_id)]), ev, SHOPS, REQ)
        self.assertIsNone(out[0].in_stock)
        self.assertTrue(any('pre-order' in g.lower() for g in out[0].gaps))

    def test_condition_from_another_models_spec_block_does_not_count(self):
        ev = Evidence()
        product = make_product(title='MacBook Air M2', options={'Config': '8/256gb Midnight'})
        product.variants.append(type(product.variants[0])('12', '16/512gb Silver', {'Config': '16/512gb Silver'},
                                                          Decimal('999'), currency='USD', available=True))
        product.description = 'SSD 256GB - Silver | MLXY3 | CONDITION:New'
        p = product_obs(ev, product)
        out = check_offers(final([claim(p.obs_id, condition_quote='CONDITION:New')]), ev, SHOPS, REQ)
        self.assertEqual(out[0].condition, 'unknown')

    def test_fallback_names_unchecked_leads_instead_of_claiming_none(self):
        from app.scout.agent import fallback_final
        f = fallback_final(REQ, Evidence(), [dict(shop='ITWorks', title='Logitech H390 USB headset', url='https://x')])
        self.assertIn('ITWorks: Logitech H390', f.message)
        self.assertIn('unverified', f.message)
        self.assertEqual(f.offers, [])


class SlowShopTests(unittest.TestCase):
    def test_a_slow_shop_is_dropped_not_waited_for(self):
        import time
        from app.scout import catalog
        from tests.test_scout import ALPHA, BETA, web
        search = catalog.ShopSearch(web(), [ALPHA, BETA])
        real = search._one

        def slow(shop, *args):
            if shop.shop_id == 'beta':
                time.sleep(2)
            return real(shop, *args)
        search._one = slow
        old = catalog.SEARCH_SECONDS
        catalog.SEARCH_SECONDS = 0.5
        self.addCleanup(setattr, catalog, 'SEARCH_SECONDS', old)
        started = time.monotonic()
        report = search.search('sony wh-ch520')
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(report.failures.get('beta'), 'SLOW')
        self.assertTrue(any(h.shop_id == 'alpha' for h in report.hits))


if __name__ == '__main__':
    unittest.main()
