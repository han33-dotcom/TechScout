"""Final live verification checks (issue #70, T34).

Verify re-reads the finalists through the gate in the final phase, within the run's reserve and
each store's per-run cap, and shows only offers that re-read confirms. These checks cover:
- changed price, stock and purchasability;
- a variant that is no longer listed, and cached copies;
- bounded replacements, store caps, the time and call reserves;
- cancellation and errors;
- the evidence rules in app.verification, including the shared freshness policy for reused
  store policy pages.

Offline: the run tests' fake store readers, fake gate, stand-in rules, fake clock and temporary
SQLite database, plus the real gate over a scripted transport. Synthetic offers only; no store,
paid API or model is ever called.
"""
import dataclasses
from datetime import timedelta
from decimal import Decimal
import hashlib
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import httpx2

from app import runs, verification
from app.adapters import base
from app.adapters.base import PolicyDocument, PolicyFact
from app.assessment import Assessment, Check, Eligibility
from app.contracts import Offer
from app.runs import RunCaps
from app.sources import FetchOutcome, SourceGate, SourceLimiter

from test_runs import API, FAQ, PAGE, PUBLIC, T0, Body, FakeStore, RunCase, make_assess, make_offer

SEARCHED_BY = T0 + timedelta(seconds=3)  # the three searches' six requests take 0.5 s each
OUT_OF_STOCK = 'offer is out of stock online'
NOT_PURCHASABLE = 'offer cannot be bought'


def fetched(body: bytes, at, source_id='alpha', url=None) -> FetchOutcome:
    url = url or PAGE.format(source_id)
    return FetchOutcome(source_id, url, (url,), 1, 1, final_url=url, status_code=200, content_type='text/html',
                        body=body, response_sha256=hashlib.sha256(body).hexdigest(), observed_at=at)


def reply(outcome: FetchOutcome):
    return outcome.response_sha256, outcome.observed_at


def with_facts(offer: Offer, outcome: FetchOutcome, **facts) -> Offer:
    """The offer with online_stock or purchasable known, each cited by a record of `outcome`."""
    data = offer.model_dump(mode='json')
    for field, value in facts.items():
        record = base.make_evidence(outcome, adapter_version=f'{offer.source_id}/test', field_path=field,
                                    scope='variant', product_id=offer.variant.product_id,
                                    variant_id=offer.variant.variant_id, json_pointer=f'/0/{field}')
        data['evidence'].append(record.model_dump(mode='json'))
        data[field] = {'state': 'known', 'value': value, 'evidence_ids': [record.evidence_id]}
    return Offer.model_validate(data)


def with_branch_stock(offer: Offer, outcome: FetchOutcome, branch_id='b-1') -> Offer:
    """The offer with one branch's stock known, cited by a branch-scoped record of `outcome`."""
    path = f'pickup_stock.{branch_id}.stock'
    record = base.make_evidence(outcome, adapter_version=f'{offer.source_id}/test', field_path=path,
                                scope='variant', product_id=offer.variant.product_id,
                                variant_id=offer.variant.variant_id, json_pointer=f'/0/{branch_id}')
    record = record.model_copy(update={'scope': 'branch', 'branch_id': branch_id})
    data = offer.model_dump(mode='json')
    data['evidence'].append(record.model_dump(mode='json'))
    data['pickup_stock'] = [{'branch_id': branch_id, 'city': 'Beirut',
                             'stock': {'state': 'known', 'value': True, 'evidence_ids': [record.evidence_id]}}]
    return Offer.model_validate(data)


def stock_assess(request, offers):
    """The stand-in rules, plus: an offer known to be out of stock or not purchasable is rejected."""
    pairs = []
    for offer, assessment in make_assess(required=())(request, offers):
        fails = [Check(name=field, outcome='fail', reason=reason)
                 for field, reason in (('online_stock', OUT_OF_STOCK), ('purchasable', NOT_PURCHASABLE))
                 if getattr(offer, field).state == 'known' and getattr(offer, field).value is False]
        if fails:
            checks = [*assessment.eligibility.checks, *fails]
            eligibility = Eligibility(offer_id=offer.offer_id, outcome='fail', checks=checks)
            assessment = Assessment(offer_id=offer.offer_id, eligibility=eligibility, cost=assessment.cost)
        pairs.append((offer, assessment))
    return pairs


class StockStore(FakeStore):
    """Offers with online stock and purchasability known; `refacts` change them at a re-read."""

    def __init__(self, source_id, variants=(), *, facts=None, refacts=None, **options):
        super().__init__(source_id, variants, **options)
        self.facts, self.refacts = facts or {}, refacts or {}

    def _read(self, fetch, wanted=None):
        result = super()._read(fetch, wanted)
        table = {**self.facts, **self.refacts} if wanted else self.facts
        offers = []
        for offer in result.offers:
            page = next(e for e in offer.evidence if e.field_path == 'price')
            outcome = fetched(b'', page.observed_at, offer.source_id)  # its hash and time are replaced
            outcome = dataclasses.replace(outcome, response_sha256=page.response_sha256)
            offers.append(with_facts(offer, outcome, **table.get(offer.variant.variant_id, {})))
        return dataclasses.replace(result, offers=tuple(offers))


class GoneStore(FakeStore):
    """A store whose listing no longer has some variants when a finalist is re-read."""

    def __init__(self, source_id, variants=(), *, gone=(), **options):
        super().__init__(source_id, variants, **options)
        self.gone = set(gone)

    def _read(self, fetch, wanted=None):
        result = super()._read(fetch, wanted)
        if wanted:
            result = dataclasses.replace(result, offers=tuple(
                o for o in result.offers if o.variant.variant_id not in self.gone))
        return result


class CachingStore(FakeStore):
    """A reader that sends the re-read's requests but returns the copies it read at search time."""

    def search_candidates(self, fetch, query):
        result = super().search_candidates(fetch, query)
        self.cached = result.offers
        return result

    def fetch_offers(self, fetch, offer_ids, query):
        result = super().fetch_offers(fetch, offer_ids, query)
        return dataclasses.replace(result, offers=tuple(o for o in self.cached if o.offer_id in offer_ids))


class PriceGoneStore(FakeStore):
    """A store whose re-read no longer publishes a price."""

    def _read(self, fetch, wanted=None):
        if not wanted:
            return super()._read(fetch, wanted)
        listed, self.variants = self.variants, [(v, color, None) for v, color, _ in self.variants]
        try:
            return super()._read(fetch, wanted)
        finally:
            self.variants = listed


class OneAtATimeStore(FakeStore):
    """A reader without fetch_offers: verify re-reads its finalists one at a time."""
    fetch_offers = None


class VerificationRunTests(RunCase):
    def verify_data(self, run_id='run-1'):
        return self.action(run_id, 'stage:verify')['result']['data']

    def reasons(self, result):
        return {i.offer_id: i.reasons for i in result.unresolved + result.rejected}

    def test_each_finalist_is_re_read_in_the_final_phase(self):
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([(r.offer.offer_id, r.payable_total.amount) for r in result.recommendations],
                         [('beta:100:201', Decimal('990.00')), ('alpha:100:101', Decimal('1000.00'))])
        self.assertEqual(self.stores['beta'].calls, [('search_candidates', None),
                                                     ('fetch_offers', ('beta:100:201',))])
        self.assertEqual(self.stores['alpha'].calls, [('search_candidates', None),
                                                      ('fetch_offers', ('alpha:100:101',))])
        self.assertEqual(self.stores['gamma'].calls, [('search_candidates', None)])  # rejected for its color
        self.assertEqual(self.verify_data(), {'finalists': 2, 'reads': 2, 'rechecked': 2, 'changed': 0,
                                              'not_rechecked': 0, 'verified': 2})
        for rec in result.recommendations:  # every fact shown comes from the final re-read
            self.assertTrue(all(e.observed_at > SEARCHED_BY for e in rec.offer.evidence))
            self.assertEqual(self.storage.load_offer(rec.offer.offer_id), rec.offer)
        self.assertIn({'source_id': 'beta', 'targets': ['beta:100:201'], 'offers': 1, 'phase': 'verify'},
                      self.stored_arguments())
        self.assertEqual((result.status, result.stop_reason), ('complete', runs.STOP_NO_GAPS))
        self.assertTruthful(result)

    def test_reused_policy_pages_stand_within_the_freshness_window(self):
        # The stand-in rules need arrival and payment terms, which only the FAQ page settles.
        result = self.search()
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201', 'alpha:100:101'])
        for rec in result.recommendations:
            arrival = next(e for e in rec.offer.evidence if e.field_path == 'arrival')
            self.assertEqual(arrival.scope, 'source')
            self.assertLess(arrival.observed_at, SEARCHED_BY + timedelta(seconds=2))  # read in investigation
            price = next(e for e in rec.offer.evidence if e.field_path == 'price')
            self.assertGreater(price.observed_at, arrival.observed_at)  # re-read in the final phase
        self.assertTruthful(result)

    def test_a_price_change_reranks_the_shortlist(self):
        self.stores['beta'] = FakeStore('beta', [('201', 'Blue', '990.00')], reread_price='1020.00')
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([(r.offer.offer_id, r.payable_total.amount) for r in result.recommendations],
                         [('alpha:100:101', Decimal('1000.00')), ('beta:100:201', Decimal('1020.00'))])
        self.assertEqual(self.verify_data()['changed'], 1)
        self.assertEqual(self.storage.load_offer('beta:100:201').price.value.amount, Decimal('1020.00'))
        self.assertTruthful(result)

    def test_a_price_rise_over_the_budget_rejects_the_offer(self):
        self.stores['beta'] = FakeStore('beta', [('201', 'Blue', '990.00')], reread_price='1200.00')
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['alpha:100:101'])
        self.assertEqual(self.reasons(result)['beta:100:201'], ['total 1200.00 USD is over the budget'])
        self.assertIn('beta:100:201', {i.offer_id for i in result.rejected})
        self.assertEqual((result.status, self.verify_data()['changed']), ('complete', 1))
        self.assertTruthful(result)

    def test_an_offer_sold_out_at_the_re_check_is_replaced_by_the_next_finalist(self):
        in_stock = {'online_stock': True, 'purchasable': True}
        self.stores = {
            'alpha': StockStore('alpha', [('101', 'Black', '900.00'), ('102', 'Black', '910.00'),
                                          ('103', 'Blue', '920.00')],
                                facts={v: in_stock for v in ('101', '102', '103')},
                                refacts={'102': {'online_stock': False, 'purchasable': True}}),
            'beta': StockStore('beta', [('201', 'Blue', '950.00')], facts={'201': in_stock}),
            'gamma': StockStore('gamma', [('301', 'Blue', '960.00')], facts={'301': in_stock}),
        }
        result = self.search(assess=stock_assess)
        self.assertEqual([r.offer.offer_id for r in result.recommendations],
                         ['alpha:100:101', 'alpha:100:103', 'beta:100:201'])
        self.assertEqual(self.reasons(result)['alpha:100:102'], [OUT_OF_STOCK])
        # One read covers alpha's three finalists; the next store's finalist replaces the sold-out one.
        self.assertEqual(self.stores['alpha'].calls[1],
                         ('fetch_offers', ('alpha:100:101', 'alpha:100:102', 'alpha:100:103')))
        self.assertEqual(self.stores['gamma'].calls, [('search_candidates', None)])  # three already confirmed
        self.assertEqual(self.verify_data(), {'finalists': 5, 'reads': 2, 'rechecked': 4, 'changed': 1,
                                              'not_rechecked': 0, 'verified': 3})
        for rec in result.recommendations:
            stock = next(e for e in rec.offer.evidence if e.field_path == 'online_stock')
            self.assertGreater(stock.observed_at, SEARCHED_BY)
        self.assertTruthful(result)

    def test_an_offer_no_longer_purchasable_is_not_recommended(self):
        self.stores['beta'] = StockStore('beta', [('201', 'Blue', '990.00')],
                                         facts={'201': {'purchasable': True}},
                                         refacts={'201': {'purchasable': False}})
        result = self.search(assess=stock_assess)
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['alpha:100:101'])
        self.assertEqual(self.reasons(result)['beta:100:201'], [NOT_PURCHASABLE])
        self.assertTruthful(result)

    def test_the_re_read_copy_replaces_the_earlier_one_whatever_time_it_carries(self):
        # The wall clock steps back during beta's re-read, so the re-read copy carries an earlier
        # time than the search copy. The real merge rule keeps the latest observation of an offer,
        # so the earlier copy must be gone, not merely outvoted.
        from app.identity import deduplicate
        reads = []

        def step_back_on_beta_re_read(url):
            if url == API.format('beta'):
                reads.append(url)
                if len(reads) == 2:
                    self.clock.wall -= timedelta(seconds=2.5)

        def merge_then_judge(request, offers):
            return make_assess(required=())(request, deduplicate(offers).offers)

        self.gate.on_request = step_back_on_beta_re_read
        result = self.search(assess=merge_then_judge)
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201', 'alpha:100:101'])
        beta = result.recommendations[0].offer
        searched_beta = T0 + timedelta(seconds=2)  # beta's search page reply
        self.assertTrue(all(e.observed_at < searched_beta for e in beta.evidence))  # the re-read copy
        self.assertTruthful(result)

    def test_a_re_read_that_reopens_a_gap_says_so_in_the_stop_reason(self):
        self.stores['beta'] = PriceGoneStore('beta', [('201', 'Blue', '990.00')])
        result = self.search(assess=make_assess(required=()))
        self.assertEqual(self.action('run-1', 'stage:investigate')['result']['data']['stop_reason'],
                         runs.STOP_NO_GAPS)  # investigation itself had settled everything
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['alpha:100:101'])
        self.assertEqual(self.reasons(result)['beta:100:201'], ['price is not known'])
        self.assertEqual((result.status, result.stop_reason), ('complete', runs.STOP_VERIFY_GAPS))
        self.assertTruthful(result)

    def test_a_variant_gone_at_the_re_check_is_withheld(self):
        self.stores['beta'] = GoneStore('beta', [('201', 'Blue', '990.00')], gone={'201'})
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['alpha:100:101'])
        self.assertEqual(self.reasons(result)['beta:100:201'], [runs.REASON_RECHECK_FAILED])
        self.assertEqual(result.status, 'complete')
        self.assertTruthful(result)

    def test_a_cached_copy_never_counts_as_a_fresh_re_read(self):
        # alpha's reader serves an observation from before the run; beta's sends the re-read's
        # requests but returns the copy it read at search time.
        self.stores['alpha'] = FakeStore('alpha', [('101', 'Black', '1000.00')],
                                         observed_before=T0 - timedelta(days=1))
        self.stores['beta'] = CachingStore('beta', [('201', 'Blue', '990.00')])
        result = self.search(assess=make_assess(required=()))
        self.assertEqual((result.status, result.recommendations), ('abstained', []))
        self.assertEqual({k: v for k, v in self.reasons(result).items() if k != 'gamma:100:301'},
                         {'alpha:100:101': [runs.REASON_STALE], 'beta:100:201': [runs.REASON_RECHECK_FAILED]})
        self.assertEqual(self.verify_data()['reads'], 2)
        self.assertTruthful(result)

    def test_a_finalist_with_an_unknown_charge_takes_no_read_and_no_place(self):
        unknown_fee = {'kind': 'payment', 'state': 'unknown', 'reason': 'card fee is not published'}
        self.stores = {'alpha': FakeStore('alpha', [('101', 'Black', '900.00')], costs=[unknown_fee]),
                       'beta': FakeStore('beta', [('201', 'Blue', '990.00')]),
                       'gamma': FakeStore('gamma', [('301', 'Blue', '1050.00')])}
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201', 'gamma:100:301'])
        self.assertEqual(self.reasons(result)['alpha:100:101'], [runs.REASON_UNKNOWN_CHARGE])
        self.assertEqual(self.stores['alpha'].calls, [('search_candidates', None)])
        self.assertTruthful(result)

    def test_a_store_without_room_is_not_re_checked_and_its_cap_is_not_a_run_limit(self):
        self.write_registry(beta={'max_requests_per_run': 2})  # its search uses the whole cap
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['alpha:100:101'])
        self.assertEqual(self.reasons(result)['beta:100:201'], [runs.REASON_NOT_RECHECKED])
        self.assertEqual(self.stores['beta'].calls, [('search_candidates', None)])
        self.assertEqual((result.status, self.verify_data()['not_rechecked']), ('complete', 1))
        self.assertTruthful(result)

    def test_investigation_leaves_each_store_a_product_read_for_the_re_check(self):
        # alpha's second variant has no price, so a product re-read could settle it; with a cap of
        # four, that read would leave nothing for the final re-check of alpha's finalist.
        self.write_registry(alpha={'max_requests_per_run': 4})
        self.stores['alpha'] = FakeStore('alpha', [('101', 'Black', '1000.00'), ('102', 'Black', None)])
        result = self.search(assess=make_assess(required=()))
        self.assertEqual(result.stop_reason, runs.STOP_STORE_CAPS)
        self.assertNotIn(('fetch_offer', 'alpha:100:102'), self.stores['alpha'].calls)
        self.assertEqual(self.stores['alpha'].calls[-1], ('fetch_offers', ('alpha:100:101',)))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201', 'alpha:100:101'])
        self.assertEqual(result.status, 'complete')
        self.assertTruthful(result)

    def test_a_reader_without_fetch_offers_is_re_read_one_finalist_at_a_time(self):
        self.stores['alpha'] = OneAtATimeStore('alpha', [('101', 'Black', '1000.00'), ('102', 'Black', '1005.00')])
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations],
                         ['beta:100:201', 'alpha:100:101', 'alpha:100:102'])
        self.assertEqual(self.stores['alpha'].calls, [('search_candidates', None), ('fetch_offer', 'alpha:100:101'),
                                                      ('fetch_offer', 'alpha:100:102')])
        self.assertEqual(self.verify_data()['reads'], 3)
        self.assertTruthful(result)

    def test_no_re_read_fits_the_time_reserve(self):
        # gamma's search ends at 34 s, inside the 50 s before the reserve; a re-read is counted at
        # two full 15 s request timeouts, so none can end by 60 s.
        self.stores['gamma'] = FakeStore('gamma', [('301', 'Red', '950.00')],
                                         on_search=lambda: self.clock.advance(31))
        result = self.search(caps=RunCaps(seconds=60, reserve_seconds=10), assess=make_assess(required=()))
        self.assertEqual(result.source_coverage[2].status, 'ok')  # every search fitted
        self.assertEqual((result.status, result.stop_reason, result.recommendations),
                         ('partial', runs.STOP_TIME, []))
        self.assertEqual({k: v for k, v in self.reasons(result).items() if k != 'gamma:100:301'},
                         {'alpha:100:101': [runs.REASON_NOT_RECHECKED],
                          'beta:100:201': [runs.REASON_NOT_RECHECKED]})
        self.assertNotIn('fetch_offers', self.names())  # never started, so never half-sent
        self.assertTruthful(result)

    def test_the_time_reserve_cuts_the_second_re_read(self):
        reads = []

        def slow_beta(url):
            if url == API.format('beta'):
                reads.append(url)
                if len(reads) == 2:  # beta's final re-read takes most of the run
                    self.clock.advance(87)

        self.gate.on_request = slow_beta
        result = self.search(assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201'])
        self.assertEqual(self.reasons(result)['alpha:100:101'], [runs.REASON_NOT_RECHECKED])
        self.assertEqual((result.status, result.stop_reason), ('partial', runs.STOP_TIME))
        self.assertTruthful(result)

    def test_the_call_reserve_bounds_the_re_reads(self):
        result = self.search(caps=RunCaps(external_calls=9, reserve_calls=3), assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201'])
        self.assertEqual(self.reasons(result)['alpha:100:101'], [runs.REASON_NOT_RECHECKED])
        self.assertEqual((result.status, result.stop_reason), ('partial', runs.STOP_CALLS))
        self.assertEqual(result.usage.external_calls, 8)
        self.assertTruthful(result)

    def test_cancelling_during_the_re_check(self):
        cancel, reads = threading.Event(), []

        def cancel_on_beta_re_read(url):
            if url == API.format('beta'):
                reads.append(url)
                if len(reads) == 2:
                    cancel.set()

        self.gate.on_request = cancel_on_beta_re_read
        result = self.search(assess=make_assess(required=()), cancel=cancel)
        self.assertEqual((result.status, result.stop_reason, result.recommendations),
                         ('cancelled', runs.STOP_CANCELLED, []))
        self.assertEqual({k: v for k, v in self.reasons(result).items() if k != 'gamma:100:301'},
                         {'alpha:100:101': [runs.REASON_CANCELLED], 'beta:100:201': [runs.REASON_CANCELLED]})
        self.assertEqual(self.verify_data(), {'finalists': 2, 'reads': 1, 'rechecked': 1, 'changed': 0,
                                              'not_rechecked': 0, 'verified': 0})
        self.assertTruthful(result)

    def test_an_error_during_the_re_check_names_the_tool_and_withholds_everything(self):
        with mock.patch.object(self.stores['beta'], 'fetch_offers', side_effect=RuntimeError('reader broke')):
            with self.assertLogs('techscout.runs', 'ERROR'):
                result = self.search(assess=make_assess(required=()))
        self.assertEqual((result.status, result.stop_reason, result.recommendations),
                         ('error', runs.STOP_ERROR, []))
        failure = self.action('run-1', 'stage:results')['result']['data']['failure']
        self.assertEqual(failure, {'stage': 'verify', 'error': 'RuntimeError', 'tool': 'fetch_offers',
                                   'source_id': 'beta'})
        self.assertEqual(self.reasons(result)['alpha:100:101'], [runs.REASON_ERROR])
        self.assertTruthful(result)


class RealGateVerificationTests(RunCase):
    def setUp(self):
        super().setUp()
        for target in ('socket.getaddrinfo', 'socket.create_connection', 'socket.socket.connect'):
            patcher = mock.patch(target, side_effect=AssertionError(f'real network use: {target}'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def real_gate(self, handle):
        return SourceGate._for_tests(self.registry, transport=httpx2.MockTransport(handle),
                                     resolver=lambda host, port: [PUBLIC], clock=self.clock.monotonic,
                                     wall_clock=self.clock.now, sleep=self.clock.advance,
                                     limiter=SourceLimiter())

    def test_gate_retries_in_investigation_can_use_the_kept_read(self):
        # alpha's FAQ answers 503 twice: its read takes three attempts, so search 2 + FAQ 3 leaves
        # one request of the cap of six, too few for the final re-read. It fails safe: not shown.
        replies = {FAQ.format('alpha'): [503, 503, 200]}

        def handle(request):
            url = str(request.url)
            steps = replies.get(url, [200])
            status = steps.pop(0) if len(steps) > 1 else steps[0]
            json_reply = '/wp-json/' in url
            body = (b'[]' if json_reply else b'<html>ok</html>') if status == 200 else b''
            return httpx2.Response(status, stream=Body(body), headers=[
                ('Content-Type', 'application/json' if json_reply else 'text/html'),
                ('Content-Length', str(len(body)))])

        result = self.search(gate=self.real_gate(handle))  # the stand-in rules need the FAQ page
        metrics = self.action('run-1', 'stage:results')['result']['data']['metrics']
        self.assertEqual(metrics['calls_per_source'], {'alpha': 5, 'beta': 5, 'gamma': 2})
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201'])
        self.assertEqual({i.offer_id: i.reasons for i in result.unresolved},
                         {'alpha:100:101': [runs.REASON_NOT_RECHECKED]})
        self.assertEqual(result.status, 'complete')
        self.assertTruthful(result)

    def test_a_re_read_cut_short_by_the_store_cap_is_not_recommended(self):
        def handle(request):
            url = str(request.url)
            if url == PAGE.format('alpha'):  # moved, so each page read takes two requests
                return httpx2.Response(301, stream=Body(b''), headers=[
                    ('Location', FAQ.format('alpha')), ('Content-Length', '0')])
            json_reply = '/wp-json/' in url
            body = b'[]' if json_reply else b'<html>ok</html>'
            return httpx2.Response(200, stream=Body(body), headers=[
                ('Content-Type', 'application/json' if json_reply else 'text/html'),
                ('Content-Length', str(len(body)))])

        # alpha's search takes three requests, so its cap of five leaves two: the re-read's pre-check
        # passes, then the redirect hop of its page request is refused by the gate.
        self.write_registry(alpha={'max_requests_per_run': 5})
        result = self.search(gate=self.real_gate(handle), assess=make_assess(required=()))
        self.assertEqual([r.offer.offer_id for r in result.recommendations], ['beta:100:201'])
        self.assertEqual({i.offer_id: i.reasons for i in result.unresolved},
                         {'alpha:100:101': [runs.REASON_RECHECK_FAILED]})
        alpha = result.source_coverage[0]
        self.assertEqual((alpha.status, [e.code for e in alpha.errors]), ('partial', ['BUDGET_EXHAUSTED']))
        self.assertEqual(result.status, 'complete')
        self.assertTruthful(result)


class SelectionTests(unittest.TestCase):
    @staticmethod
    def ranked(*ids):
        return [(SimpleNamespace(offer_id=i), None) for i in ids]

    def test_rank_is_the_shortlist_order(self):
        def pair(offer_id, amount):
            total = SimpleNamespace(amount=Decimal(amount))
            return SimpleNamespace(offer_id=offer_id), SimpleNamespace(cost=SimpleNamespace(verified_total=total))

        pairs = [pair('b', '10'), pair('c', '9'), pair('a', '10')]
        self.assertEqual([o.offer_id for o, _ in verification.rank(pairs)], ['c', 'a', 'b'])

    def test_next_target_and_left_unchecked(self):
        ranked = self.ranked('a', 'b', 'c', 'd', 'e')
        target = lambda confirmed, settled: getattr(  # noqa: E731
            verification.next_target(ranked, confirmed, settled, 3), 'offer_id', None)
        self.assertEqual(target(set(), set()), 'a')
        self.assertEqual(target({'a'}, {'a'}), 'b')
        self.assertEqual(target({'a'}, {'a', 'b'}), 'c')  # b was read and not confirmed, or skipped
        self.assertEqual(target({'a', 'c'}, {'a', 'b', 'c'}), 'd')
        self.assertIsNone(target({'a', 'b', 'c'}, {'a', 'b', 'c'}))  # three confirmed rank above d
        self.assertIsNone(target({'a'}, {'a', 'b', 'c', 'd', 'e'}))
        self.assertEqual(verification.left_unchecked(ranked, {'a', 'c'}, 3), ['b', 'd', 'e'])
        self.assertEqual(verification.left_unchecked(ranked, {'a', 'b', 'c'}, 3), [])
        self.assertEqual(verification.left_unchecked(ranked, {'b', 'c', 'd'}, 3), ['a'])


class RecheckProblemTests(unittest.TestCase):
    """app.verification.recheck_problem on synthetic offers."""

    def setUp(self):
        self.search = fetched(b'page at search', T0 + timedelta(seconds=1))
        self.final = fetched(b'page at the re-check', T0 + timedelta(seconds=9))
        faq = fetched(b'faq page', T0 + timedelta(seconds=4), url=FAQ.format('alpha'))
        facts = (PolicyFact('delivery_time', {'zone': 'Beirut'}, 'Within Beirut: delivery takes 1 working day'),
                 PolicyFact('payment', {'methods': ['cash']}, 'We only accept cash on delivery'))
        self.policy = PolicyDocument('alpha', 'faq', faq.origin_url, faq.final_url, faq.observed_at,
                                     faq.response_sha256, 'alpha/test', facts)
        self.faq = faq
        self.run = {reply(self.search), reply(self.final), reply(faq)}

    def problem(self, offer, *, run=None, final=None, policies=None, now=T0 + timedelta(seconds=10)):
        return verification.recheck_problem(
            offer, run_replies=self.run if run is None else run,
            final_replies={reply(self.final)} if final is None else final,
            policy_replies={reply(self.faq)} if policies is None else policies, now=now)

    def test_decisive_facts_must_cite_the_final_re_read(self):
        re_read = make_offer(self.final, '100', '101')
        self.assertIsNone(self.problem(re_read))
        # Decisive records are judged by reply membership alone, never by the clock.
        self.assertIsNone(self.problem(re_read, now=T0 - timedelta(hours=1)))
        self.assertEqual(self.problem(make_offer(self.search, '100', '101')), verification.NOT_CURRENT)
        stale = make_offer(fetched(b'cached', T0 - timedelta(days=1)), '100', '101')
        self.assertEqual(self.problem(stale), verification.STALE)
        for field in ('online_stock', 'purchasable'):
            with self.subTest(field=field):
                self.assertIsNone(self.problem(with_facts(re_read, self.final, **{field: True})))
                self.assertEqual(self.problem(with_facts(re_read, self.search, **{field: True})),
                                 verification.NOT_CURRENT)

    def test_branch_stock_must_cite_the_final_re_read(self):
        re_read = make_offer(self.final, '100', '101')
        fresh = with_branch_stock(re_read, self.final)
        old = with_branch_stock(re_read, self.search)
        self.assertIn(fresh.pickup_stock[0].stock.evidence_ids[0], verification.decisive_evidence_ids(fresh))
        self.assertIsNone(self.problem(fresh))
        self.assertEqual(self.problem(old), verification.NOT_CURRENT)

    def test_policy_records_follow_the_shared_reuse_window(self):
        offer = FakeStore('alpha').apply_policy(make_offer(self.final, '100', '101'), self.policy)
        self.assertEqual({e.field_path for e in offer.evidence if e.scope == 'source'},
                         {'arrival', 'payment_basis'})
        self.assertIsNone(self.problem(offer))
        # The wall clock stepped back after the FAQ was read: never an observation from the future.
        self.assertIsNone(self.problem(offer, now=T0 + timedelta(seconds=2)))
        self.assertIsNone(self.problem(offer, now=T0 - timedelta(hours=1)))
        self.assertIsNone(self.problem(offer, now=T0 + timedelta(days=6)))
        self.assertEqual(self.problem(offer, now=T0 + timedelta(days=8)), verification.NOT_CURRENT)
        # a policy record from a reply of this run that was not one of its policy pages
        self.assertEqual(self.problem(offer, policies=set()), verification.NOT_CURRENT)

    def test_the_policy_view_is_a_runtime_policy_observation(self):
        offer = FakeStore('alpha').apply_policy(make_offer(self.final, '100', '101'), self.policy)
        record = next(e for e in offer.evidence if e.field_path == 'arrival')
        view = verification.policy_view(record)
        self.assertEqual((view.schema_version, view.field_path, view.origin, view.run, view.scope),
                         (2, 'policy.arrival', 'runtime', None, 'source'))
        self.assertEqual((view.evidence_id, view.response_sha256, view.observed_at),
                         (record.evidence_id, record.response_sha256, record.observed_at))


if __name__ == '__main__':
    unittest.main()
