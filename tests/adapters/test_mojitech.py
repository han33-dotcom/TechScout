"""Mojitech reader on minimized captures (MVP 05/06).

Twelve actual color and ESIM-option variants, all listed at zero and not purchasable today; the
ESIM option maps to a SIM type or, for 'ABM 2YEARS WARRANTY', to a warranty and no SIM type.
"""
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.adapters.mojitech import ADAPTER
from app.contracts import ArrivalWindow

from . import (MOJI_API, MOJI_PAGE, MOJI_REFUND, FakeFetch, ReaderTestCase, by_id, evidence, fixture, query,
               routes)

UTC = timezone.utc
ABM = {'91724', '91725'}
FOOTER = 'Delivery all over Lebanon Same Day Up To 72 hours (Working Days)'
# Observed 19:03:48Z on a Saturday: same day, up to three working days (Monday to Wednesday).
FOOTER_WINDOW = ArrivalWindow(earliest=datetime(2026, 11, 14, 19, 3, 48, tzinfo=UTC),
                              latest=datetime(2026, 11, 18, 21, 59, 59, 999999, tzinfo=UTC), basis='estimate')


class MojitechTests(ReaderTestCase):
    def search(self, q=None, table=None):
        fetch = FakeFetch(table or routes('mojitech'))
        result = ADAPTER.search_candidates(fetch, q or query())
        self.assertContract(result)
        return fetch, result

    def test_twelve_offers_none_priced_and_none_purchasable(self):
        fetch, result = self.search()
        self.assertEqual((result.tool_result.status, len(result.offers)), ('ok', 12))
        self.assertEqual({k: result.tool_result.data[k] for k in ('variants_seen', 'variants_without_price')},
                         {'variants_seen': 12, 'variants_without_price': 12})
        for offer in result.offers:
            self.assertEqual((offer.price.state, offer.price.reason), ('unknown', 'store lists a zero price'))
            self.assertEqual({e.scope for e in evidence(offer, offer.price.evidence_ids)}, {'variant'})
            self.assertEqual((offer.purchasable.state, offer.purchasable.value), ('known', False))
            self.assertEqual((offer.online_stock.state, offer.online_stock.value), ('known', True))
        offers = by_id(result)
        # 91724 is the API's variation record: '0' in minor unit 1 agrees with the page's 0.
        self.assertEqual((offers['91724'].raw_price, offers['91724'].price_fractional_digits), ('0', 1))
        self.assertEqual((offers['91707'].raw_price, offers['91707'].price_fractional_digits), ('0', 0))
        self.assertEqual(fetch.urls(), [MOJI_API, MOJI_PAGE])

    def test_options_keep_store_wording_and_map_sim_and_warranty(self):
        _, result = self.search()
        offers = by_id(result)
        self.assertEqual(offers['91724'].variant.color.value, 'bleu')  # canonical colors are identity's job
        self.assertEqual({o.variant.color.value for o in result.offers},
                         {'lavender', 'bleu', 'sage', 'white', 'black'})
        for vid, offer in offers.items():
            variant, warranty = offer.variant, offer.warranty
            self.assertEqual((variant.manufacturer.value, variant.model.value, variant.storage_gb.value),
                             ('Apple', 'iPhone 17', 256))
            # One size in the name applies to every variation, cited per variation at the name.
            [size] = evidence(offer, variant.storage_gb.evidence_ids)
            self.assertEqual((size.scope, size.variant_id, size.json_pointer), ('variant', vid, '/1/name'))
            if vid in ABM:
                self.assertEqual((variant.sim.state, variant.sim.reason),
                                 ('unknown', 'SIM type is not stated for this option'))
                self.assertTrue(variant.sim.evidence_ids)
                self.assertEqual((warranty.provider.value, warranty.months.value), ('ABM', 24))
                months = evidence(offer, warranty.months.evidence_ids)
                self.assertEqual({e.scope for e in months}, {'variant'})
            else:
                self.assertEqual(variant.sim.value, 'esim-only')
                self.assertEqual((warranty.provider.reason, warranty.months.reason),
                                 ('warranty provider is not published', 'warranty length is not published'))
            self.assertEqual(warranty.territory.reason, 'warranty territory is not published')
        self.assertEqual(offers['91708'].variant.raw_attributes['attributes'],
                         {'Color': 'lavender', 'ESIM': 'ESIM ACT TECH+'})

    def test_footer_statements(self):
        _, result = self.search()
        offer = result.offers[0]
        self.assertEqual((offer.arrival.state, offer.arrival.value), ('known', FOOTER_WINDOW))
        [footer] = evidence(offer, offer.arrival.evidence_ids)
        self.assertEqual((footer.scope, footer.product_id, footer.excerpt), ('source', None, FOOTER))
        self.assertEqual((offer.payment_basis.state, offer.payment_basis.reason),
                         ('unknown', 'store accepts cash and card; price terms per method are not published'))
        [payment] = evidence(offer, offer.payment_basis.evidence_ids)
        self.assertEqual(payment.excerpt, 'Payment Methods Cash on delivery or Credit cards')
        self.assertEqual([(c.kind, c.state, c.reason) for c in offer.costs], [
            ('tax', 'unknown', 'VAT treatment is not published'),
            ('delivery', 'unknown', 'delivery charge is not published')])

    def test_refund_page_statements_fill_only_an_unknown_arrival(self):
        fetch, result = self.search()
        policy = ADAPTER.fetch_policy(fetch, 'refund_returns')
        self.assertEqual(fetch.urls(), [MOJI_API, MOJI_PAGE, MOJI_REFUND])
        [document] = policy.policies
        self.assertEqual([(f.topic, f.value) for f in document.facts], [
            ('delivery_time', {'zone': 'Lebanon', 'min_days': 0, 'max_days': 3, 'unit': 'working'}),
            ('payment', {'methods': ['cash', 'card'], 'only': False}),
            ('returns', {'days': 5})])
        offer = result.offers[0]
        self.assertEqual(ADAPTER.apply_policy(offer, document), offer)  # already known from the footer
        page = fixture('mojitech/page.html').replace(b'Same Day Up To 72 hours', b'')
        fetch, result = self.search(table=routes('mojitech', {MOJI_PAGE: page}))
        offer = result.offers[0]
        self.assertEqual(offer.arrival.reason, 'delivery time is not published')
        document = ADAPTER.fetch_policy(fetch, 'refund_returns').policies[0]
        after = ADAPTER.apply_policy(offer, document)
        self.assertEqual(after.arrival.state, 'known')
        self.assertEqual(after.arrival.value.basis, 'estimate')
        self.assertEqual(after.payment_basis, offer.payment_basis)
        self.assertEqual(ADAPTER.policy_topics, {'refund_returns': ('arrival',)})

    def test_a_price_needs_no_more_decimals_than_the_currency(self):
        page = fixture('adversarial/too_many_decimals.page.html')
        _, result = self.search(table=routes('mojitech', {MOJI_PAGE: page}))
        offers = by_id(result)
        too_many = offers['91707']
        self.assertEqual((too_many.price.state, too_many.price.reason),
                         ('unknown', 'price has more decimals than the currency allows'))
        self.assertEqual((too_many.raw_price, too_many.price_fractional_digits), ('1100.55', 2))
        one_decimal = offers['91708']
        self.assertEqual((one_decimal.price.state, one_decimal.price.value.amount),
                         ('known', Decimal('1100.5')))
        self.assertEqual((one_decimal.raw_price, one_decimal.price_fractional_digits), ('1100.5', 1))
        self.assertEqual(result.tool_result.data['variants_without_price'], 11)


if __name__ == '__main__':
    unittest.main()
