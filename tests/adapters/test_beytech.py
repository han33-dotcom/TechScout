"""Beytech reader on minimized captures (MVP 05/06).

Four actual color variants with distinct SKUs; Apple from the Brand attribute, 256 GB from the
'M Size' option, and the warranty length from the product name; its '1 Sim' leaves the SIM type
unknown until the store confirms what it means.
"""
from decimal import Decimal
import unittest

from app.adapters.beytech import ADAPTER

from . import (BEY_API, BEY_EXCHANGE, BEY_PAGE, BEY_WARRANTY, EN_DASH, FakeFetch, ReaderTestCase, by_id,
               evidence, query, routes)

VARIANTS = ['139978', '139979', '139980', '140008']


class BeytechTests(ReaderTestCase):
    def search(self, fetch=None, q=None):
        fetch = fetch or FakeFetch(routes('beytech'))
        result = ADAPTER.search_candidates(fetch, q or query())
        self.assertContract(result)
        return fetch, result

    def test_four_priced_variants_with_distinct_skus(self):
        _, result = self.search()
        self.assertEqual(result.tool_result.status, 'ok')
        self.assertEqual([o.variant.variant_id for o in result.offers], VARIANTS)
        for offer in result.offers:
            self.assertEqual((offer.price.value.amount, offer.price.value.currency),
                             (Decimal('1079.00'), 'USD'))
            self.assertEqual((offer.purchasable.value, offer.online_stock.value), (True, True))
        self.assertEqual([o.variant.raw_attributes['sku'] for o in result.offers], [
            'Apple | iPhone 17 Black', 'Apple | iPhone 17 Green', 'Apple | iPhone 17 Lavender',
            'Apple | iPhone 17 Blue'])
        self.assertEqual([o.variant.color.value for o in result.offers],
                         ['Black', 'Green', 'Lavender', 'Blue'])
        api = by_id(result)['140008']  # the API's variation record
        self.assertEqual((api.raw_price, api.price_fractional_digits), ('107900', 2))

    def test_identity_from_brand_attribute_size_option_and_name(self):
        _, result = self.search()
        offer = by_id(result)['139979']
        variant = offer.variant
        self.assertEqual(variant.manufacturer.value, 'Apple')
        makers = evidence(offer, variant.manufacturer.evidence_ids)
        self.assertEqual(makers[0].json_pointer, '/1/attributes/2/terms/0/name')  # the Brand attribute
        self.assertEqual({e.scope for e in makers}, {'product'})
        self.assertEqual((variant.model.value, variant.storage_gb.value), ('iPhone 17', 256))
        storage = evidence(offer, variant.storage_gb.evidence_ids)
        self.assertEqual([(e.json_pointer, e.excerpt) for e in storage], [
            (None, 'variation 139979 attribute_m-size 256 GB'), ('/1/variations/1/attributes/0/value', None)])
        self.assertEqual((variant.sim.state, variant.sim.reason),
                         ('unknown', 'SIM type in the product name is not confirmed'))  # '1 Sim'
        [sim] = evidence(offer, variant.sim.evidence_ids)
        self.assertEqual((sim.scope, sim.variant_id, sim.json_pointer), ('variant', '139979', '/1/name'))
        warranty = offer.warranty
        self.assertEqual((warranty.months.value, warranty.provider.reason, warranty.territory.reason),
                         (24, 'warranty provider is not published', 'warranty territory is not published'))
        [months] = evidence(offer, warranty.months.evidence_ids)
        self.assertEqual((months.scope, months.json_pointer), ('product', '/1/name'))
        # The JSON-LD brand ('Electronics') is noise and appears nowhere in the facts.
        self.assertNotIn('Electronics', offer.model_dump_json(exclude={'variant': {'raw_attributes'}}))
        self.assertEqual(variant.raw_attributes['product_name'],
                         f'Apple | iPhone 17 {EN_DASH} 1 Sim {EN_DASH} 2 Years Warranty')

    def test_nothing_else_is_published(self):
        _, result = self.search()
        offer = result.offers[0]
        self.assertEqual((offer.arrival.state, offer.arrival.reason),
                         ('unknown', 'delivery time is not published'))
        self.assertEqual([(c.kind, c.state, c.reason) for c in offer.costs], [
            ('tax', 'unknown', 'VAT treatment is not published'),
            ('delivery', 'unknown', 'delivery charge is not published')])
        self.assertEqual(offer.payment_basis.reason,
                         'store accepts cash and card; price terms per method are not published')
        self.assertEqual(offer.condition.reason, 'condition is not published')

    def test_policy_pages_settle_nothing(self):
        fetch, result = self.search()
        warranty, exchange = ADAPTER.fetch_policy(fetch, 'warranty'), ADAPTER.fetch_policy(fetch, 'exchange')
        self.assertEqual([(f.topic, f.value) for f in warranty.policies[0].facts],
                         [('warranty', {'covers': 'repairs only', 'invoice_required': True})])
        # Exchanges within 5 days, but the page lists mobiles as non-returnable.
        self.assertEqual([(f.topic, f.value) for f in exchange.policies[0].facts],
                         [('returns', {'days': 5, 'excludes_mobiles': True})])
        for offer in result.offers:
            after = ADAPTER.apply_policy(offer, warranty.policies[0])
            self.assertEqual(ADAPTER.apply_policy(after, exchange.policies[0]), offer)
        self.assertEqual(ADAPTER.policy_topics, {'warranty': (), 'exchange': ()})  # never offered to a run

    def test_search_and_both_policies_use_the_whole_per_run_cap(self):
        fetch = FakeFetch(routes('beytech'), cap=4)
        self.search(fetch)
        ADAPTER.fetch_policy(fetch, 'warranty')
        ADAPTER.fetch_policy(fetch, 'exchange')
        self.assertEqual(fetch.urls(), [BEY_API, BEY_PAGE, BEY_WARRANTY, BEY_EXCHANGE])
        # A fifth request is refused by the gate; the reader reports it and invents nothing.
        refused = ADAPTER.fetch_offer(fetch, 'beytech:139968:139978', query())
        self.assertEqual((refused.tool_result.status, [e.code for e in refused.tool_result.errors]),
                         ('error', ['BUDGET_EXHAUSTED']))
        self.assertEqual((refused.offers, len(fetch.calls)), ((), 5))


if __name__ == '__main__':
    unittest.main()
