"""Buyer contract v2 in the scout's checker (issue #273). Offline: synthetic listings, no network, no model.

Contract v2: prices are compared before VAT and delivery; an offer whose condition the shop does not
state still counts as a verified output, with that said plainly; used, refurbished and open-box offers
count only when the buyer opts in. The tests also pin the review findings on PR #269: stock that is
"in store only" is not online stock, and proof for a requirement must come from the offered variant.
"""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx2

from app.scout.agent import SCOPE_NOTE, RunConfig, ScoutAgent, with_contract_notes
from app.scout.catalog import ShopSearch
from app.scout.evidence import Evidence
from app.scout.shops import Shop
from app.scout.storefronts import Product, Variant, product_from_html
from app.scout.verify import accepts_used, check_offers
from app.scout.web import SafeWeb
from tests.test_scout import (ALPHA, BETA, GAMMA, NOW, PUBLIC, SHOPS, ScriptedProvider, claim, final, product_obs,
                              step, web)

REQ = dict(text='New black Sony WH-CH520 headphones')
UNSTATED = 'new condition is not stated by the shop'


def listing(*variants, title='Sony WH-CH520 Wireless Headphones', condition=None, description='', url_tail='x'):
    variants = variants or (Variant('11', 'Black', {'Color': 'Black'}, Decimal('42.00'), currency='USD', available=True),)
    return Product('alpha', 'Alpha Shop', 'shopify', '1', title, f'https://alpha.example/products/{url_tail}',
                   'shopify_product_js', f'https://alpha.example/products/{url_tail}.js', 'h' * 64, NOW,
                   condition=condition, description=description, variants=list(variants))


def unstated_claim(obs, **overrides):
    return claim(obs, condition='unknown', condition_quote=None, **overrides)


class PriceScopeTests(unittest.TestCase):
    def test_no_delivery_fee_or_vat_is_needed_for_a_verified_offer(self):
        ev = Evidence()
        p = product_obs(ev, listing(Variant('11', 'Black / Brand New', {'Color': 'Black', 'Condition': 'Brand New'},
                                            Decimal('42.00'), currency='USD', available=True)))
        offer = check_offers(final([claim(p.obs_id)]), ev, SHOPS, REQ)[0]   # listed scope is the default
        self.assertTrue(offer.complete)
        self.assertEqual((offer.price, offer.delivery_fee, offer.total), (Decimal('42.00'), None, None))
        self.assertEqual(offer.gaps, [])

    def test_the_old_rule_is_still_available_by_name_only(self):
        ev = Evidence()
        p = product_obs(ev, listing(Variant('11', 'Black / Brand New', {'Color': 'Black', 'Condition': 'Brand New'},
                                            Decimal('42.00'), currency='USD', available=True)))
        offer = check_offers(final([claim(p.obs_id)]), ev, SHOPS, dict(REQ, mode='delivery', destination='Batroun'),
                             scope='total')[0]
        self.assertFalse(offer.complete)   # contract v1 needed an evidenced delivery fee and VAT position

    def test_pickup_is_never_verified_from_online_stock(self):
        ev = Evidence()
        p = product_obs(ev, listing())
        offer = check_offers(final([unstated_claim(p.obs_id)]), ev, SHOPS, dict(REQ, mode='pickup'))[0]
        self.assertFalse(offer.complete)
        self.assertIn('pickup branch stock is not confirmed (online stock only)', offer.gaps)

    def test_in_store_only_is_not_online_stock(self):
        html = ('<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",'
                '"name":"Sony WH-CH520","offers":{"@type":"Offer","price":"42.00","priceCurrency":"USD",'
                '"availability":"https://schema.org/InStoreOnly"}}</script></head><body></body></html>')
        page = SafeWeb(transport=httpx2.MockTransport(lambda r: httpx2.Response(
            200, text=html if r.url.path == '/p' else 'none', headers={'content-type': 'text/html'})),
            resolver=lambda h, p: [PUBLIC], sleep=lambda s: None, min_interval_s=0, obey_robots=False,
            wall_clock=lambda: NOW).get('https://gamma.example/p', accept='html')
        product, _ = product_from_html(page, GAMMA)
        self.assertIsNone(product.variants[0].available)
        ev = Evidence()
        obs = product_obs(ev, product)
        offer = check_offers(final([unstated_claim(obs.obs_id, variant_id=product.variants[0].variant_id)]), ev,
                             dict(SHOPS, gamma=GAMMA), REQ)[0]
        self.assertFalse(offer.complete)


class ConditionTests(unittest.TestCase):
    def test_unstated_condition_still_counts_and_the_gap_is_named(self):
        ev = Evidence()
        p = product_obs(ev, listing())
        offer = check_offers(final([unstated_claim(p.obs_id)]), ev, SHOPS, REQ)[0]
        self.assertTrue(offer.complete)
        self.assertEqual((offer.tier, offer.condition, offer.gaps), ('best', 'unknown', [UNSTATED]))

    def test_an_offer_is_never_called_new_without_evidence_from_the_listing(self):
        ev = Evidence()
        p = product_obs(ev, listing())
        offer = check_offers(final([claim(p.obs_id, condition='new', condition_quote='Brand New')]), ev, SHOPS, REQ)[0]
        self.assertEqual(offer.condition, 'unknown')   # the quote is not in this listing
        self.assertIn(UNSTATED, offer.gaps)

    def test_a_published_condition_other_than_new_is_not_verified(self):
        ev = Evidence()
        p = product_obs(ev, listing(Variant('11', 'Black', {'Color': 'Black'}, Decimal('42.00'), currency='USD',
                                            available=True, condition='Excellent')))
        offer = check_offers(final([claim(p.obs_id, condition='new', condition_quote='Black')]), ev, SHOPS, REQ)[0]
        self.assertFalse(offer.complete)
        self.assertTrue(any('Excellent' in g for g in offer.gaps))

    def test_used_offers_need_the_buyers_opt_in_and_keep_their_label(self):
        ev = Evidence()
        p = product_obs(ev, listing(title='Sony WH-CH520 Wireless Headphones (Used)'))
        refused = check_offers(final([unstated_claim(p.obs_id)]), ev, SHOPS, REQ)[0]
        self.assertEqual((refused.tier, refused.condition, refused.complete), ('rejected', 'used', False))
        for text in ('Sony WH-CH520 in black, used is fine', 'Black Sony WH-CH520, open box or refurbished accepted',
                     'بدي سماعة Sony WH-CH520 مستعملة'):
            accepted = check_offers(final([unstated_claim(p.obs_id)]), ev, SHOPS, dict(text=text))[0]
            self.assertEqual((accepted.tier, accepted.condition, accepted.complete), ('best', 'used', True), text)

    def test_refusing_used_is_not_an_opt_in(self):
        for text in ('New Sony WH-CH520, not used', 'Sony WH-CH520, no refurbished please',
                     "I don't want a used one", 'Sony WH-CH520 brand new only'):
            self.assertFalse(accepts_used(text), text)
        for text in ('a used one is fine', 'second-hand is ok', 'refurbished works for me', 'mosta3mal mesh mechkle'):
            self.assertTrue(accepts_used(text), text)


class EvidenceBindingTests(unittest.TestCase):
    def test_a_quote_from_another_product_of_the_same_shop_proves_nothing(self):
        ev = Evidence()
        offered = product_obs(ev, listing(Variant('11', 'Default Title', {}, Decimal('42.00'), currency='USD',
                                                  available=True)))
        other = product_obs(ev, listing(title='JBL Tune 520BT Black', url_tail='y'))
        need = dict(requirement='black', status='met', evidence_obs=other.obs_id, quote='Black')
        offer = check_offers(final([unstated_claim(offered.obs_id, requirements=[need])]), ev, SHOPS, REQ)[0]
        self.assertEqual(offer.requirements[0]['status'], 'unknown')
        self.assertFalse(offer.complete)

    def test_a_quote_from_a_sibling_variant_proves_nothing(self):
        ev = Evidence()
        p = product_obs(ev, listing(
            Variant('11', 'Black', {'Color': 'Black'}, Decimal('60.00'), currency='USD', available=True),
            Variant('12', 'Blue', {'Color': 'Blue'}, Decimal('42.00'), currency='USD', available=True)))
        need = dict(requirement='black', status='met', evidence_obs=p.obs_id, quote='Black')
        blue = check_offers(final([unstated_claim(p.obs_id, variant_id='12', requirements=[need])]), ev, SHOPS, REQ)[0]
        self.assertEqual((blue.price, blue.requirements[0]['status'], blue.complete), (Decimal('42.00'), 'unknown', False))
        black = check_offers(final([unstated_claim(p.obs_id, variant_id='11', requirements=[need])]), ev, SHOPS, REQ)[0]
        self.assertEqual((black.price, black.requirements[0]['status'], black.complete), (Decimal('60.00'), 'met', True))


class AnswerWordingTests(unittest.TestCase):
    def test_the_answer_names_unstated_condition_and_the_price_scope(self):
        ev = Evidence()
        p = product_obs(ev, listing())
        offers = check_offers(final([unstated_claim(p.obs_id)]), ev, SHOPS, REQ)
        text = with_contract_notes('Alpha Shop has it for USD 42.00.', offers)
        self.assertIn("I couldn't verify these are new; the shop doesn't say: Alpha Shop.", text)
        self.assertIn(SCOPE_NOTE, text)
        self.assertEqual(text.count(SCOPE_NOTE), 1)
        # A message that already says it keeps one statement of the scope.
        again = with_contract_notes('USD 42.00. Prices are before VAT and delivery.', [])
        self.assertNotIn(SCOPE_NOTE, again)

    def test_the_loop_uses_the_listed_scope_by_default(self):
        w = web()
        fin = dict(status='offers', request_kind='exact', message='Alpha Shop has the black pair at USD 42.',
                   offers=[claim('P1')], question=None, budget_max_usd=None)
        agent = ScoutAgent(ScriptedProvider([
            step('open', [dict(tool='open', url='https://alpha.example/products/sony-ch520')]),
            step('done', final_=fin)]), web=w, search=ShopSearch(w, [ALPHA, BETA, GAMMA]), config=RunConfig(max_turns=4))
        self.assertEqual(agent.scope, 'listed')
        result = agent.run(dict(text='New black Sony WH-CH520'))
        best = result.offers[0]
        self.assertEqual((best.shop_id, best.price, best.total, best.complete), ('alpha', Decimal('42.00'), None, True))
        self.assertIn('before VAT and delivery', result.message)
        # No delivery or VAT page was read for the answer.
        self.assertFalse([c for c in result.tool_calls if c['tool'] in ('read', 'shop_info')])


class MerchantFeedTests(unittest.TestCase):
    FEED = ('<?xml version="1.0"?><rss xmlns:g="http://base.google.com/ns/1.0"><channel><item>'
            '<title>Sony WH-CH520 Black</title><link>https://feedshop.example/p/sony-wh-ch520</link>'
            '<g:price>42.00 USD</g:price><g:availability>in_stock</g:availability><g:condition>new</g:condition>'
            '</item></channel></rss>')
    SITEMAP = ('<urlset><url><loc>https://feedshop.example/product/sony-wh-ch520-black</loc></url></urlset>')

    def refresh(self, feed_status):
        def handler(request):
            path = request.url.path
            if path == '/robots.txt':
                return httpx2.Response(200, text='User-agent: *\nAllow: /\nSitemap: https://feedshop.example/sitemap.xml\n')
            if path == '/feed.xml':
                return httpx2.Response(feed_status, text=self.FEED, headers={'content-type': 'application/xml'})
            if path == '/sitemap.xml':
                return httpx2.Response(200, text=self.SITEMAP, headers={'content-type': 'application/xml'})
            return httpx2.Response(404)
        w = SafeWeb(transport=httpx2.MockTransport(handler), resolver=lambda h, p: [PUBLIC], sleep=lambda s: None,
                    min_interval_s=0)
        shop = Shop('feedshop', 'Feed Shop', 'https://feedshop.example', platform='html', currency='USD',
                    feed_url='https://feedshop.example/feed.xml', provenance='test')
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        search = ShopSearch(w, [shop], cache_dir=Path(tmp.name))
        return search.refresh(shop), search.search('sony wh-ch520').hits

    def test_an_html_shop_with_a_feed_is_indexed_from_the_feed(self):
        report, hits = self.refresh(200)
        self.assertEqual((report['status'], report['rows']), ('ok', 1))
        self.assertEqual([(h.url, h.price, h.available) for h in hits],
                         [('https://feedshop.example/p/sony-wh-ch520', Decimal('42.00'), True)])

    def test_an_unreadable_feed_falls_back_to_the_sitemap(self):
        report, hits = self.refresh(500)
        self.assertEqual((report['status'], report['rows']), ('ok', 1))
        self.assertEqual([(h.url, h.price) for h in hits], [('https://feedshop.example/product/sony-wh-ch520-black', None)])


if __name__ == '__main__':
    unittest.main()
