"""Class Cellulars reader on minimized captures (MVP 05/06).

Four actual color variants share one SKU; the price comes from the page and the Store API's
minor units; the product page's 14-30 day estimate conflicts with the FAQ's delivery times.
"""
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.adapters.class_cellulars import ADAPTER
from app.contracts import ArrivalWindow

from . import (CLASS_API, CLASS_FAQ, CLASS_PAGE, FakeFetch, ReaderTestCase, by_id, evidence, fixture, query,
               routes)

VARIANTS = ['20648', '19116', '19117', '18398']
UTC = timezone.utc
ESTIMATE = 'Estimated delivery time 14-30 days'


def window(earliest: datetime, latest: datetime) -> ArrivalWindow:
    return ArrivalWindow(earliest=earliest, latest=latest, basis='estimate')


# The page is observed at 19:03:48Z on Saturday 14 November (21:03 in Beirut); the FAQ a second later.
PAGE_WINDOW = window(datetime(2026, 11, 27, 21, tzinfo=UTC), datetime(2026, 12, 14, 21, 59, 59, 999999, UTC))
BEIRUT_WINDOW = window(datetime(2026, 11, 15, 21, tzinfo=UTC),
                       datetime(2026, 11, 16, 21, 59, 59, 999999, UTC))
OUTSIDE_WINDOW = window(datetime(2026, 11, 14, 19, 3, 49, tzinfo=UTC),
                        datetime(2026, 11, 18, 21, 59, 59, 999999, UTC))


class ClassCellularsTests(ReaderTestCase):
    def search(self, q=None, table=None):
        fetch = FakeFetch(table or routes('class_cellulars'))
        result = ADAPTER.search_candidates(fetch, q or query())
        self.assertContract(result)
        return fetch, result

    def faq(self, fetch):
        [document] = ADAPTER.fetch_policy(fetch, 'faq').policies
        return document

    def test_four_actual_variants_with_exact_price_from_minor_units(self):
        fetch, result = self.search()
        self.assertEqual((result.tool_result.status, result.tool_result.errors), ('ok', []))
        self.assertEqual([o.offer_id for o in result.offers],
                         [f'class_cellulars:18392:{v}' for v in VARIANTS])
        self.assertEqual({k: result.tool_result.data[k] for k in ('variants_seen', 'variants_without_price')},
                         {'variants_seen': 4, 'variants_without_price': 0})
        for offer in result.offers:
            self.assertEqual(offer.price.state, 'known')
            self.assertEqual((offer.price.value.amount, offer.price.value.currency),
                             (Decimal('1030.00'), 'USD'))
            self.assertEqual(str(offer.price.value.amount), '1030.00')
            self.assertEqual(offer.seller_id, 'class_cellulars')
        offers = by_id(result)
        # The API's one variation record is 19116: its minor units confirm the page price.
        api_price = offers['19116']
        self.assertEqual((api_price.raw_price, api_price.price_fractional_digits), ('103000', 2))
        pointers = {e.json_pointer for e in evidence(api_price, api_price.price.evidence_ids)}
        self.assertLessEqual({'/0/prices/price', '/0/prices/currency_code'}, pointers)
        page_only = offers['18398']
        self.assertEqual((page_only.raw_price, page_only.price_fractional_digits), ('1030', 0))
        cited = evidence(page_only, page_only.price.evidence_ids)
        self.assertEqual([(e.scope, e.variant_id, e.json_pointer, e.excerpt) for e in cited], [
            ('variant', '18398', None, 'variation 18398 display_price 1030'),
            ('variant', '18398', '/1/prices/currency_code', None)])
        self.assertEqual(fetch.calls, [('class_cellulars', CLASS_API, 'json'),
                                       ('class_cellulars', CLASS_PAGE, 'html')])
        self.assertEqual([s.media_type for s in result.snapshots], ['application/json', 'text/html'])

    def test_shared_sku_stays_on_four_distinct_variants(self):
        _, result = self.search()
        self.assertEqual([o.variant.raw_attributes['sku'] for o in result.offers], ['IBX6XH-6-1-5'] * 4)
        self.assertEqual(len({o.variant.variant_id for o in result.offers}), 4)
        self.assertEqual([o.variant.color.value for o in result.offers],
                         ['Lavender', 'Blue', 'White', 'Black'])
        self.assertEqual(by_id(result)['19116'].variant.raw_attributes['permalink'],
                         'https://classcellulars.com/product/iphone-17/?attribute_pa_storage-capacity=256gb'
                         '&attribute_pa_color=blue&attribute_pa_sim-type=nano-sim-esim')

    def test_identity_facts_cite_where_they_come_from(self):
        _, result = self.search()
        offer = by_id(result)['18398']
        variant = offer.variant
        self.assertEqual((variant.manufacturer.value, variant.model.value, variant.storage_gb.value,
                          variant.sim.value), ('Apple', 'iPhone 17', 256, 'physical+esim'))
        # No brand attribute or brand list; the name says only 'iPhone 17'; the tag says 'Apple iphone'.
        [maker] = evidence(offer, variant.manufacturer.evidence_ids)
        self.assertEqual((maker.scope, maker.variant_id, maker.json_pointer),
                         ('product', None, '/1/tags/0/name'))
        [model] = evidence(offer, variant.model.evidence_ids)
        self.assertEqual((model.scope, model.json_pointer), ('product', '/1/name'))
        storage = evidence(offer, variant.storage_gb.evidence_ids)
        self.assertEqual([(e.scope, e.json_pointer, e.excerpt) for e in storage], [
            ('variant', None, 'variation 18398 attribute_pa_storage-capacity 256gb'),
            ('variant', '/1/variations/3/attributes/0/value', None)])
        self.assertEqual({e.scope for e in evidence(offer, variant.sim.evidence_ids)}, {'variant'})
        self.assertEqual((variant.region.state, variant.region.reason),
                         ('unknown', 'region is not published'))
        self.assertEqual((offer.condition.state, offer.condition.reason),
                         ('unknown', 'condition is not published'))
        self.assertEqual((offer.purchasable.value, offer.online_stock.value), (True, True))

    def test_product_page_statements(self):
        _, result = self.search()
        offer = by_id(result)['19117']
        tax, delivery = offer.costs
        self.assertEqual((tax.kind, tax.state), ('tax', 'included'))
        self.assertEqual([(e.scope, e.excerpt) for e in evidence(offer, tax.evidence_ids)], [
            ('product', '1,030.00 $ (Inc. VAT)'), ('product', 'JSON-LD offer valueAddedTaxIncluded true')])
        self.assertEqual((delivery.kind, delivery.state, delivery.amount.amount, delivery.amount.currency,
                          delivery.conditions),
                         ('delivery', 'additional', Decimal('0'), 'USD', ['min_subtotal:USD:50']))
        [banner] = evidence(offer, delivery.evidence_ids)
        self.assertEqual((banner.scope, banner.product_id, banner.excerpt),
                         ('source', None, 'Free Shipping On All Orders Over $50'))
        self.assertEqual((offer.arrival.state, offer.arrival.value), ('known', PAGE_WINDOW))
        [estimate] = evidence(offer, offer.arrival.evidence_ids)
        self.assertEqual((estimate.scope, estimate.excerpt), ('product', ESTIMATE))
        warranty = offer.warranty
        self.assertEqual((warranty.provider.value, warranty.months.value), ('Genuine Warranty Center', 18))
        self.assertEqual(warranty.territory.reason, 'warranty territory is not published')
        self.assertEqual((offer.payment_basis.state, offer.payment_basis.reason),
                         ('unknown', 'payment terms are not published on the product page'))

    def test_pickup_lists_configured_branches_with_unknown_stock_and_no_delivery_charge(self):
        beirut = [('mar-elias', 'Beirut'), ('abc-verdun', 'Beirut'), ('abc-achrafieh', 'Beirut')]
        cases = [(dict(branch_id='abc-verdun'), [('abc-verdun', 'Beirut')]),
                 (dict(city='Tyre'), [('tyre', 'Tyre')]),
                 (dict(), beirut),  # every configured branch in the city: the request names none
                 (dict(city=' beirut '), beirut),
                 (dict(city='Zahle'), []),  # no configured branch there: none is made up
                 (dict(branch_id='hamra'), []),  # not a configured branch
                 (dict(branch_id='tyre'), [])]  # a configured branch, but in another city
        for args, branches in cases:
            with self.subTest(**args):
                _, result = self.search(query(mode='pickup', **args))
                offer = result.offers[0]
                self.assertEqual([(s.branch_id, s.city) for s in offer.pickup_stock], branches)
                for stock in offer.pickup_stock:
                    self.assertEqual((stock.stock.state, stock.stock.reason),
                                     ('unknown', 'online stock does not show branch stock'))
                self.assertEqual([c.kind for c in offer.costs], ['tax'])
                self.assertEqual((offer.arrival.state, offer.arrival.reason),
                                 ('unknown', 'pickup readiness is not published'))
                self.assertEqual(offer.online_stock.value, True)  # still separate from the branch

    def test_faq_statements(self):
        fetch = FakeFetch(routes('class_cellulars'))
        result = ADAPTER.fetch_policy(fetch, 'faq')
        self.assertEqual(fetch.calls, [('class_cellulars', CLASS_FAQ, 'html')])
        self.assertEqual((result.tool_result.status, result.tool_result.data['policy_id'],
                          result.tool_result.data['facts']), ('ok', 'faq', 4))
        [document] = result.policies
        self.assertEqual([(f.topic, f.value) for f in document.facts], [
            ('delivery_time', {'zone': 'Beirut', 'min_days': 1, 'max_days': 1, 'unit': 'working'}),
            ('delivery_time', {'zone': 'outside Beirut', 'min_days': 0, 'max_days': 3, 'unit': 'working'}),
            ('payment', {'methods': ['cash'], 'only': True}),
            ('returns', {'days': 3})])
        self.assertEqual([f.excerpt for f in document.facts], [
            'Within Beirut: delivery takes 1 working day',
            'Outside Beirut: delivery takes up to 3 working days',
            'we only accept cash on delivery', 'request a return within 3 days'])
        self.assertEqual((document.url, document.response_sha256), (CLASS_FAQ, result.snapshots[0].sha256))
        self.assertEqual(ADAPTER.policy_topics, {'faq': ('payment_basis', 'arrival')})

    def test_faq_makes_the_beirut_estimate_a_conflict_and_sets_cash(self):
        fetch, result = self.search()
        document = self.faq(fetch)
        before = by_id(result)['20648']
        after = ADAPTER.apply_policy(before, document)
        self.assertEqual((after.arrival.state, after.arrival.alternatives, after.arrival.reason),
                         ('conflict', [PAGE_WINDOW, BEIRUT_WINDOW], 'delivery estimates conflict'))
        cited = evidence(after, after.arrival.evidence_ids)
        self.assertEqual([(e.scope, e.excerpt) for e in cited], [
            ('product', ESTIMATE), ('source', 'Within Beirut: delivery takes 1 working day')])
        self.assertEqual((after.payment_basis.state, after.payment_basis.value), ('known', 'cash'))
        [cash] = evidence(after, after.payment_basis.evidence_ids)
        self.assertEqual((cash.scope, str(cash.origin_url), cash.excerpt),
                         ('source', CLASS_FAQ, 'we only accept cash on delivery'))
        for name in ('price', 'variant', 'purchasable', 'online_stock', 'warranty', 'costs', 'condition'):
            self.assertEqual(getattr(after, name), getattr(before, name), name)
        self.assertEqual(ADAPTER.apply_policy(after, document), after)  # the same page twice changes nothing

    def test_faq_outside_beirut_pickup_and_missing_estimate(self):
        with self.subTest('outside Beirut'):
            fetch, result = self.search(query(city='Tyre'))
            after = ADAPTER.apply_policy(result.offers[0], self.faq(fetch))
            self.assertEqual((after.arrival.state, after.arrival.alternatives),
                             ('conflict', [PAGE_WINDOW, OUTSIDE_WINDOW]))
        with self.subTest('pickup keeps readiness unknown'):
            fetch, result = self.search(query(mode='pickup', branch_id='mar-elias'))
            after = ADAPTER.apply_policy(result.offers[0], self.faq(fetch))
            self.assertEqual((after.arrival.state, after.arrival.reason),
                             ('unknown', 'pickup readiness is not published'))
            # 'Cash on delivery' answers for online orders, not for paying at a branch.
            self.assertEqual((after.payment_basis.state, after.payment_basis.reason),
                             ('unknown', 'payment terms are not published on the product page'))
            self.assertEqual(after, result.offers[0])  # the FAQ changes nothing on a pickup offer
        with self.subTest('no estimate on the page'):
            page = fixture('class_cellulars/page.html').replace(f'<li>{ESTIMATE}</li>'.encode(), b'')
            fetch, result = self.search(table=routes('class_cellulars', {CLASS_PAGE: page}))
            offer = result.offers[0]
            self.assertEqual(offer.arrival.reason, 'delivery time is not published')
            after = ADAPTER.apply_policy(offer, self.faq(fetch))
            self.assertEqual((after.arrival.state, after.arrival.value), ('known', BEIRUT_WINDOW))

    def test_a_later_faq_read_joins_the_conflict(self):
        fetch, result = self.search()
        first = ADAPTER.apply_policy(result.offers[0], self.faq(fetch))
        later = ADAPTER.fetch_policy(FakeFetch(routes('class_cellulars'),
                                               observed_at=datetime(2026, 11, 16, 9, tzinfo=UTC)), 'faq')
        after = ADAPTER.apply_policy(first, later.policies[0])
        self.assertEqual(after.arrival.state, 'conflict')
        self.assertEqual(len(after.arrival.alternatives), 3)
        self.assertEqual(after.arrival.alternatives[:2], [PAGE_WINDOW, BEIRUT_WINDOW])
        self.assertEqual(len(after.arrival.evidence_ids), 3)

    def test_requests_stay_within_the_per_run_cap(self):
        fetch, _ = self.search()
        self.faq(fetch)
        self.assertEqual(fetch.urls(), [CLASS_API, CLASS_PAGE, CLASS_FAQ])
        self.assertLessEqual(len(fetch.calls), 4)


if __name__ == '__main__':
    unittest.main()
