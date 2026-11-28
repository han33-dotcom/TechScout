"""Shared-checker fixes found when reviewing the scout before issue #272. Offline: synthetic listings only.

- A "new" that is marketing ("the new MacBook Air", "all-new", "new arrivals") is not proof of new condition.
- A shop-wide policy statement ("all our products are brand new") proves new condition in the listed scope.
- "+" models (Redmi Note 14 Pro+, Galaxy S24+, Tab S9 FE+) are not an exact match for the base model.
- A requested RAM figure stated only in the listing's own description still counts, unless that
  description lists several configurations.
"""
from decimal import Decimal
import unittest

from app.scout.evidence import Evidence
from app.scout.storefronts import Product, Variant
from app.scout.verify import check_offers, identity_conflicts, says_new, storage_values, tier_words
from tests.test_scout import NOW, SHOPS, claim, final, page_obs, product_obs

UNSTATED = 'new condition is not stated by the shop'


def laptop(*, title='Apple MacBook Air 13.6" M4 256GB Midnight', description='', options=None, variants=None):
    variants = variants or [Variant('11', 'Midnight', options or {'Color': 'Midnight'}, Decimal('1049.00'),
                                    currency='USD', available=True)]
    return Product('alpha', 'Alpha Shop', 'shopify', '1', title, 'https://alpha.example/products/mba',
                   'shopify_product_js', 'https://alpha.example/products/mba.js', 'h' * 64, NOW,
                   description=description, variants=variants)


REQ = dict(text='Price of a new 13-inch MacBook Air M4 with 16 GB RAM and 256 GB?')


class NewConditionTests(unittest.TestCase):
    def test_marketing_new_in_a_description_is_not_new_condition(self):
        for quote in ('new', 'The new MacBook Air', 'all-new design', 'new arrivals'):
            ev = Evidence()
            p = product_obs(ev, laptop(description='The new MacBook Air with M4. All-new design. Shop new arrivals.'))
            offer = check_offers(final([claim(p.obs_id, condition_quote=quote)]), ev, SHOPS, REQ)[0]
            self.assertEqual(offer.condition, 'unknown', quote)
            self.assertIn(UNSTATED, offer.gaps, quote)

    def test_condition_wording_in_the_listing_is_new_condition(self):
        for description, quote in (('Brand New, sealed in box.', 'Brand New'), ('Condition: New', 'Condition: New'),
                                   ('Factory sealed with Apple warranty.', 'Factory sealed')):
            ev = Evidence()
            p = product_obs(ev, laptop(description=description))
            offer = check_offers(final([claim(p.obs_id, condition_quote=quote)]), ev, SHOPS, REQ)[0]
            self.assertEqual(offer.condition, 'new', quote)

    def test_a_bare_new_counts_only_as_the_listings_own_option_value(self):
        ev = Evidence()
        p = product_obs(ev, laptop(options={'Color': 'Midnight', 'Condition': 'New'}))
        self.assertEqual(check_offers(final([claim(p.obs_id, condition_quote='New')]), ev, SHOPS, REQ)[0].condition,
                         'new')
        ev = Evidence()
        p = product_obs(ev, laptop(title='New Apple MacBook Air 13.6" M4 256GB Midnight'))
        self.assertEqual(check_offers(final([claim(p.obs_id, condition_quote='New')]), ev, SHOPS, REQ)[0].condition,
                         'unknown')   # "New" in a title is not a condition value

    def test_a_shop_wide_policy_statement_counts_in_the_listed_scope(self):
        ev = Evidence()
        p = product_obs(ev, laptop())
        policy = page_obs(ev, 'alpha', 'All our products are brand new and sealed.', url='https://alpha.example/pages/faq')
        blog = page_obs(ev, 'alpha', 'Brand new arrivals this week', url='https://alpha.example/blogs/news')
        ok = check_offers(final([claim(p.obs_id, condition_quote='brand new and sealed', condition_obs=policy.obs_id)]),
                          ev, SHOPS, REQ)[0]
        self.assertEqual(ok.condition, 'new')
        no = check_offers(final([claim(p.obs_id, condition_quote='Brand new arrivals', condition_obs=blog.obs_id)]),
                          ev, SHOPS, REQ)[0]
        self.assertEqual(no.condition, 'unknown')

    def test_says_new(self):
        self.assertTrue(says_new('Brand New'))
        self.assertTrue(says_new('CONDITION:New'))
        self.assertFalse(says_new('Used - like new'))
        self.assertFalse(says_new('the newest iPhone'))
        self.assertFalse(says_new(None))


class PlusModelTests(unittest.TestCase):
    def test_a_plus_model_is_not_an_exact_match_for_the_base_model(self):
        for request, listing in (('Xiaomi Redmi Note 14 Pro 8/256 GB', 'Xiaomi Redmi Note 14 Pro+ 5G 8GB/256GB'),
                                 ('Samsung Galaxy S24 256GB', 'Samsung Galaxy S24+ 256GB'),
                                 ('Samsung Galaxy Tab S9 FE Wi-Fi 128 GB', 'Samsung Galaxy Tab S9 FE+ Wi-Fi 128GB')):
            self.assertTrue(identity_conflicts(request, listing, 'exact'), listing)

    def test_the_plus_model_requested_by_name_matches(self):
        self.assertEqual(identity_conflicts('Galaxy S24+ 256GB', 'Samsung Galaxy S24+ 256GB Onyx Black', 'exact'), [])
        self.assertEqual(identity_conflicts('Redmi Note 14 Pro Plus 256GB', 'Redmi Note 14 Pro+ 8/256GB', 'exact'), [])

    def test_memory_plus_storage_is_not_a_plus_model(self):
        self.assertNotIn('plus', tier_words('Galaxy A56 8GB+256GB'))
        self.assertNotIn('plus', tier_words('Redmi Pad 2 8+256GB'))
        self.assertEqual(storage_values('Galaxy A56 8GB+256GB'), {8, 256})
        self.assertEqual(storage_values('Samsung Galaxy S24+ 256GB'), {256})   # not 24 GB of RAM


class DescribedMemoryTests(unittest.TestCase):
    def test_ram_stated_in_the_listings_own_description_counts(self):
        self.assertEqual(identity_conflicts(REQ['text'], 'Apple MacBook Air 13.6" M4 256GB Midnight', 'exact',
                                            '16GB unified memory, 256GB SSD, 13.6-inch Liquid Retina display.'), [])

    def test_a_description_listing_several_configurations_proves_none(self):
        self.assertTrue(identity_conflicts(REQ['text'], 'Apple MacBook Air 13.6" M4 256GB Midnight', 'exact',
                                           'Available with 16GB, 24GB or 32GB unified memory.'))

    def test_the_checker_keeps_an_exact_match_when_ram_is_only_in_the_description(self):
        ev = Evidence()
        p = product_obs(ev, laptop(description='16GB unified memory, 256GB SSD. Brand New.'))
        offer = check_offers(final([claim(p.obs_id, condition_quote='Brand New')]), ev, SHOPS, REQ)[0]
        self.assertEqual((offer.identity, offer.tier, offer.complete), ('exact', 'best', True))


if __name__ == '__main__':
    unittest.main()
