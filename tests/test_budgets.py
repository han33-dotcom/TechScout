"""Run budget checks (MVP 11, issue #16): caps, reserves, spend, cancellation and the gate protocol.

Offline: a fake clock everywhere. The gate checks drive the real SourceGate over a scripted
transport, with a scripted resolver and a fake sleep; tripwires fail any real socket or DNS use.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import httpx2

from app import sources
from app.contracts import BudgetUsage, Money
from app.runs import RunBudget, RunCaps
from app.sources import SourceGate, SourceLimiter

ROOT = Path(__file__).resolve().parent.parent
API = 'https://alpha.example/wp-json/wc/store/v1/products?slug=phone-x'
PAGE = 'https://alpha.example/product/phone-x/'
PUBLIC = '104.18.0.10'
CAPS_SECTION = {'seconds': 120, 'external_calls': 40, 'planner_turns': 6, 'spend_usd': '1.00',
                'reserve_seconds': 30, 'reserve_calls': 10, 'reserve_spend_usd': '0.25'}


class FakeClock:
    """Monotonic seconds and an aware wall clock that move together."""

    def __init__(self):
        self.mono = 1000.0
        self.wall = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

    def monotonic(self):
        return self.mono

    def now(self):
        return self.wall

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class RunCapsTests(unittest.TestCase):
    def test_defaults_are_the_issue_caps(self):
        caps = RunCaps()
        self.assertEqual((caps.seconds, caps.external_calls, caps.planner_turns, caps.spend),
                         (120.0, 40, 6, Decimal('1.00')))
        self.assertEqual((caps.reserve_seconds, caps.reserve_calls, caps.reserve_spend),
                         (30.0, 10, Decimal('0.25')))

    def test_caps_load_from_the_evaluation_config_section(self):
        caps = RunCaps.from_config(CAPS_SECTION)
        self.assertEqual(caps, RunCaps())
        self.assertIsInstance(caps.spend, Decimal)
        self.assertEqual(str(caps.reserve_spend), '0.25')

    @unittest.skipUnless((ROOT / 'config' / 'evaluation.json').exists(), 'evaluation config not present')
    def test_the_repository_evaluation_config_gives_the_default_caps(self):
        config = json.loads((ROOT / 'config' / 'evaluation.json').read_text(encoding='utf-8'))
        self.assertEqual(RunCaps.from_config(config['caps']), RunCaps())

    def test_invalid_caps_are_rejected(self):
        bad = {
            'float spend': dict(spend=1.0),
            'negative calls': dict(external_calls=-1),
            'bool turns': dict(planner_turns=True),
            'string seconds': dict(seconds='120'),
            'infinite seconds': dict(seconds=float('inf')),
            'NaN spend': dict(spend=Decimal('NaN')),
            'call reserve takes every call': dict(reserve_calls=40),
            'time reserve takes all the time': dict(reserve_seconds=120),
            'spend reserve over the cap': dict(reserve_spend=Decimal('1.01')),
            'no calls': dict(external_calls=0, reserve_calls=0),
        }
        for name, change in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                RunCaps(**change)
        sections = {
            'float money': {**CAPS_SECTION, 'spend_usd': 1.0},
            'missing key': {k: v for k, v in CAPS_SECTION.items() if k != 'reserve_calls'},
            'not a number': {**CAPS_SECTION, 'reserve_spend_usd': 'a quarter'},
        }
        for name, section in sections.items():
            with self.subTest(name), self.assertRaises(ValueError):
                RunCaps.from_config(section)

    def test_zero_turns_and_zero_spend_are_valid_baselines(self):
        caps = RunCaps(planner_turns=0, spend=Decimal('0'), reserve_spend=Decimal('0'))
        self.assertEqual((caps.planner_turns, caps.spend), (0, Decimal('0')))


class CallBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)

    def test_a_call_counts_when_reserved_and_is_kept_or_given_back(self):
        reservation = self.budget.reserve_call('alpha', 15.0)
        self.assertEqual(self.budget.calls_used(), 1)
        reservation.release()
        self.assertEqual((self.budget.calls_used(), self.budget.calls_used('alpha')), (0, 0))
        reservation = self.budget.reserve_call('alpha', 15.0)
        reservation.commit(0.25)
        self.assertEqual((self.budget.calls_used(), self.budget.calls_used('alpha')), (1, 1))
        self.assertEqual(self.budget.call_seconds, 0.25)
        with self.assertRaisesRegex(RuntimeError, 'settled once'):
            reservation.commit(0.0)
        with self.assertRaisesRegex(RuntimeError, 'settled once'):
            reservation.release()
        self.assertEqual(self.budget.calls_used(), 1)

    def test_the_call_reserve_is_not_extra_budget(self):
        granted = [self.budget.reserve_call('alpha', 15.0) for _ in range(31)]
        self.assertEqual(sum(r is not None for r in granted), 30)
        self.assertIsNone(granted[-1])
        for reservation in granted[:-1]:
            reservation.commit(0.1)
        self.budget.enter_final_phase()
        final = [self.budget.reserve_call('alpha', 15.0) for _ in range(11)]
        self.assertEqual(sum(r is not None for r in final), 10)
        self.assertEqual(self.budget.calls_used(), 40)
        self.assertEqual(self.budget.refusals, {'calls': 2})

    def test_the_deadline_holds_back_the_time_reserve_until_the_final_phase(self):
        self.assertIsNotNone(self.budget.reserve_call('alpha', 90.0))  # 1000 + 90 = the 1090 deadline
        self.assertIsNone(self.budget.reserve_call('alpha', 90.5))
        self.clock.advance(75)
        self.assertIsNotNone(self.budget.reserve_call('alpha', 15.0))
        self.assertIsNone(self.budget.reserve_call('alpha', 15.01))
        self.budget.enter_final_phase()
        self.assertTrue(self.budget.final_phase)
        self.assertIsNotNone(self.budget.reserve_call('alpha', 45.0))  # the full 120 seconds
        self.assertIsNone(self.budget.reserve_call('alpha', 45.5))
        self.assertEqual(self.budget.refusals, {'time': 3})

    def test_released_calls_never_use_up_the_budget(self):
        for _ in range(100):
            self.budget.reserve_call('alpha', 15.0).release()
        self.assertEqual(self.budget.usage().external_calls, 0)
        self.assertEqual(self.budget.refusals, {})

    def test_shortfall_reports_without_reserving(self):
        self.assertIsNone(self.budget.shortfall(30, 90.0))
        self.assertEqual(self.budget.shortfall(31, 15.0), 'calls')
        self.assertEqual(self.budget.shortfall(1, 90.5), 'time')
        self.assertEqual((self.budget.calls_used(), self.budget.refusals), (0, {}))
        self.budget.cancel()
        self.assertEqual(self.budget.shortfall(1, 1.0), 'cancelled')

    def test_cancellation_refuses_every_tool_turn_and_charge(self):
        held = self.budget.reserve_call('alpha', 15.0)
        self.budget.cancel()
        self.assertTrue(self.budget.cancelled)
        self.assertIsNone(self.budget.reserve_call('alpha', 15.0))
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        self.assertFalse(self.budget.count_planner_turn())
        self.assertEqual(self.budget.refusals, {'cancelled': 3})
        held.release()  # a request in flight still settles; nothing new starts
        self.assertEqual(self.budget.calls_used(), 0)

    def test_the_cancel_signal_is_read_on_every_request(self):
        event = threading.Event()
        budget = RunBudget('run-2', RunCaps(), self.clock.monotonic, cancel_signal=event.is_set)
        self.assertIsNotNone(budget.reserve_call('alpha', 15.0))
        event.set()
        self.assertIsNone(budget.reserve_call('alpha', 15.0))
        self.assertTrue(budget.cancelled)
        event.clear()  # cancellation is final
        self.assertIsNone(budget.reserve_call('alpha', 15.0))

    def test_a_closed_budget_refuses_leftover_handles(self):
        self.budget.close()
        self.assertTrue(self.budget.closed)
        self.assertIsNone(self.budget.reserve_call('alpha', 15.0))
        self.assertFalse(self.budget.count_planner_turn())
        self.assertEqual(self.budget.refusals, {'closed': 2})

    def test_concurrent_reservations_never_pass_the_cap(self):
        granted, lock, start = [], threading.Lock(), threading.Barrier(8)

        def worker():
            start.wait()
            for _ in range(10):
                reservation = self.budget.reserve_call('alpha', 15.0)
                if reservation is not None:
                    reservation.commit(0.01)
                    with lock:
                        granted.append(reservation)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual((len(granted), self.budget.calls_used()), (30, 30))
        self.assertEqual(self.budget.refusals, {'calls': 50})

    def test_invalid_arguments_raise(self):
        cases = [(TypeError, lambda: self.budget.reserve_call(1, 15.0)),
                 (ValueError, lambda: self.budget.reserve_call('alpha', -1.0)),
                 (ValueError, lambda: self.budget.reserve_call('alpha', float('nan'))),
                 (ValueError, lambda: self.budget.reserve_call('alpha', True)),
                 (ValueError, lambda: self.budget.reserve_call('alpha', 15.0).commit(-0.5)),
                 (ValueError, lambda: RunBudget(' ', RunCaps(), self.clock.monotonic)),
                 (TypeError, lambda: RunBudget('run-3', {'seconds': 120}, self.clock.monotonic))]
        for expected, call in cases:
            with self.subTest(expected=expected), self.assertRaises(expected):
                call()


class SpendBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)

    def test_an_unknown_amount_is_refused(self):
        self.assertIsNone(self.budget.reserve_spend(None))
        self.assertEqual(self.budget.refusals, {'spend_unknown': 1})
        self.assertEqual(self.budget.spend_total(), Decimal('0'))

    def test_the_spend_reserve_is_not_extra_budget(self):
        first = self.budget.reserve_spend(Decimal('0.75'))
        self.assertIsNotNone(first)
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        first.commit(Decimal('0.75'))
        self.budget.enter_final_phase()
        last = self.budget.reserve_spend(Decimal('0.25'))
        last.commit(Decimal('0.25'))
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.000001')))
        self.assertEqual(self.budget.usage().spend, Money(amount=Decimal('1.00'), currency='USD'))
        self.assertEqual(self.budget.refusals, {'spend': 2})

    def test_held_spend_counts_until_settled(self):
        held = self.budget.reserve_spend(Decimal('0.30'))
        self.assertEqual(self.budget.usage().spend.amount, Decimal('0.30'))
        held.release()
        self.assertEqual(self.budget.usage().spend.amount, Decimal('0'))
        held = self.budget.reserve_spend(Decimal('0.30'))
        held.commit(Decimal('0.10'))
        self.assertEqual(str(self.budget.usage().spend.amount), '0.10')
        self.assertEqual(self.budget.remaining()['spend_usd'], '0.65')
        with self.assertRaisesRegex(RuntimeError, 'settled once'):
            held.release()

    def test_an_overrun_is_recorded_and_stops_further_spend(self):
        self.budget.reserve_spend(Decimal('0.10')).commit(Decimal('0.20'))
        self.assertEqual(self.budget.spend_total(), Decimal('0.20'))
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        self.assertEqual(self.budget.remaining()['spend_usd'], '0')
        self.assertFalse(self.budget.spend_unknown)

    def test_an_unknown_actual_cost_records_the_reservation_and_stops_spend(self):
        self.budget.reserve_spend(Decimal('0.10')).commit(None)
        self.assertEqual(self.budget.spend_total(), Decimal('0.10'))
        self.assertTrue(self.budget.spend_unknown)
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        # The refusal names the unknown cost, not a spend cap that was never reached.
        self.assertEqual(self.budget.refusals, {'spend_unknown': 1})

    def test_unreserved_spend_is_recorded_and_stops_spend(self):
        self.budget.record_unreserved_spend(Decimal('0.05'))
        self.assertEqual(self.budget.spend_total(), Decimal('0.05'))
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        self.budget.record_unreserved_spend(None)
        self.assertTrue(self.budget.spend_unknown)
        self.assertIsNone(self.budget.reserve_spend(Decimal('0.01')))
        self.assertEqual(self.budget.refusals, {'spend': 1, 'spend_unknown': 1})

    def test_spend_is_exact_decimal_only(self):
        cases = [(TypeError, lambda: self.budget.reserve_spend(0.1)),
                 (TypeError, lambda: self.budget.reserve_spend(1)),
                 (ValueError, lambda: self.budget.reserve_spend(Decimal('-0.01'))),
                 (ValueError, lambda: self.budget.reserve_spend(Decimal('NaN'))),
                 (TypeError, lambda: self.budget.reserve_spend(Decimal('0.1')).commit(0.1)),
                 (TypeError, lambda: self.budget.record_unreserved_spend(0.1))]
        for expected, call in cases:
            with self.subTest(expected=expected), self.assertRaises(expected):
                call()


class TurnAndUsageTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_planner_turns_stop_at_the_cap(self):
        budget = RunBudget('run-1', RunCaps(planner_turns=2), self.clock.monotonic)
        self.assertEqual([budget.count_planner_turn() for _ in range(3)], [True, True, False])
        self.assertEqual((budget.turns_used(), budget.refusals), (2, {'turns': 1}))

    def test_usage_reports_exact_totals(self):
        budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)
        budget.reserve_call('alpha', 15.0).commit(1.0)
        budget.count_planner_turn()
        budget.reserve_spend(Decimal('0.10')).commit(Decimal('0.0123'))
        self.clock.advance(2.5)
        self.assertEqual(budget.usage(), BudgetUsage(elapsed_ms=2500, external_calls=1, planner_turns=1,
                                                     spend={'amount': '0.0123', 'currency': 'USD'}))
        self.clock.wall += timedelta(hours=1)  # a wall-clock step never reaches the run's elapsed time
        self.assertEqual(budget.usage().elapsed_ms, 2500)

    def test_remaining_shows_only_what_is_left_before_the_reserve(self):
        budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)
        self.assertEqual(budget.remaining(), {'external_calls': 30, 'seconds': 90, 'planner_turns': 6,
                                              'spend_usd': '0.75'})
        self.clock.advance(100)
        self.assertEqual(budget.remaining()['seconds'], 0)
        budget.enter_final_phase()
        self.assertEqual(budget.remaining()['seconds'], 20)


class Body(httpx2.SyncByteStream):
    def __init__(self, data):
        self.data = data

    def __iter__(self):
        yield self.data

    def close(self):
        pass


def registry_entry(source_id, endpoints, **limits):
    origin = f'https://{source_id}.example'
    return {
        'id': source_id, 'name': source_id.title(), 'origin': origin, 'status': 'active',
        'reviewed_at': '2026-10-12',
        'admission': {'basis': 'Synthetic store for offline tests', 'evidence': [f'{origin}/'],
                      'reviewed_at': '2026-10-12T14:49:10+00:00', 'scope': 'Read-only test facts'},
        'allowed_endpoints': list(endpoints),
        'limits': {'requests_per_minute': 6, 'concurrency': 1, 'max_requests_per_run': 4, **limits},
    }


class GateProtocolTests(unittest.TestCase):
    """The real gate reserves against RunBudget once per attempt and sends nothing it refuses."""

    def setUp(self):
        for target in ('socket.getaddrinfo', 'socket.create_connection', 'socket.socket.connect'):
            patcher = mock.patch(target, side_effect=AssertionError(f'real network use: {target}'))
            patcher.start()
            self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.registry = Path(directory.name) / 'sources.json'
        self.registry.write_text(json.dumps({
            'schema_version': 1, 'live_release': {'admitted_source_ids': ['alpha']},
            'sources': [registry_entry('alpha', [API, PAGE])]}), encoding='utf-8')
        self.clock, self.sent, self.script = FakeClock(), [], {}
        self.gate = SourceGate._for_tests(
            self.registry, transport=httpx2.MockTransport(self.handle), resolver=lambda host, port: [PUBLIC],
            clock=self.clock.monotonic, wall_clock=self.clock.now, sleep=self.clock.advance,
            limiter=SourceLimiter())

    def handle(self, request):
        url = str(request.url)
        self.sent.append(url)
        steps = self.script.get(url, [200])
        status = steps.pop(0) if len(steps) > 1 else steps[0]
        body = (b'[{"id": 1}]' if url == API else b'<html>ok</html>') if status == 200 else b''
        ctype = 'application/json' if url == API else 'text/html'
        return httpx2.Response(status, stream=Body(body),
                               headers=[('Content-Type', ctype), ('Content-Length', str(len(body)))])

    def fetch(self, budget, url=API):
        return self.gate.fetch(budget, 'alpha', url, expect='json' if url == API else 'html')

    def test_the_gate_accepts_the_run_budget(self):
        budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)
        self.assertIsInstance(budget, sources.RunBudget)
        outcome = self.fetch(budget)
        self.assertEqual((outcome.status, outcome.attempts), ('ok', 1))
        self.assertEqual(budget.usage().external_calls, 1)

    def test_retries_count_as_external_calls(self):
        self.script[API] = [503, 503, 200]
        budget = RunBudget('run-1', RunCaps(), self.clock.monotonic)
        outcome = self.fetch(budget)
        self.assertEqual((outcome.status, outcome.attempts, len(self.sent)), ('ok', 3, 3))
        self.assertEqual((budget.calls_used(), budget.calls_used('alpha')), (3, 3))

    def test_a_retry_that_does_not_fit_is_refused_and_never_sent(self):
        self.script[API] = [503]
        budget = RunBudget('run-1', RunCaps(external_calls=3, reserve_calls=1), self.clock.monotonic)
        outcome = self.fetch(budget)
        self.assertEqual([e.code for e in outcome.errors], ['NETWORK_ERROR', 'BUDGET_EXHAUSTED'])
        self.assertEqual((len(self.sent), budget.calls_used(), budget.refusals), (2, 2, {'calls': 1}))

    def test_no_request_is_sent_when_its_charge_cannot_fit(self):
        tight = RunBudget('run-1', RunCaps(seconds=20.0, reserve_seconds=0.0), self.clock.monotonic)
        self.clock.advance(6)  # 1006 + the 15-second attempt is past the 1020 deadline
        cancelled = RunBudget('run-2', RunCaps(), self.clock.monotonic)
        cancelled.cancel()
        spent = RunBudget('run-3', RunCaps(external_calls=2, reserve_calls=1), self.clock.monotonic)
        self.assertEqual(self.fetch(spent).status, 'ok')
        self.sent.clear()
        for name, budget in (('time', tight), ('cancelled', cancelled), ('calls', spent)):
            with self.subTest(name):
                outcome = self.fetch(budget)
                self.assertEqual([e.code for e in outcome.errors], ['BUDGET_EXHAUSTED'])
                self.assertEqual(self.sent, [])
                self.assertEqual(budget.refusals, {name: 1})

    def test_a_cancel_during_a_wait_drops_the_waiting_request(self):
        def gate_sleeping_into(event_or_close):
            def sleep(seconds):  # the backoff or rate-limit wait, during which the buyer cancels
                self.clock.advance(seconds)
                event_or_close()
            return SourceGate._for_tests(
                self.registry, transport=httpx2.MockTransport(self.handle), resolver=lambda host, port: [PUBLIC],
                clock=self.clock.monotonic, wall_clock=self.clock.now, sleep=sleep, limiter=SourceLimiter())

        event = threading.Event()
        cancelled = RunBudget('run-1', RunCaps(), self.clock.monotonic, cancel_signal=event.is_set)
        closed = RunBudget('run-2', RunCaps(), self.clock.monotonic)
        for name, budget, gate in (('cancelled', cancelled, gate_sleeping_into(event.set)),
                                   ('closed', closed, gate_sleeping_into(closed.close))):
            with self.subTest(name):
                self.sent.clear()
                self.script[API] = [503, 200]  # the retry waits out a one-second backoff
                outcome = gate.fetch(budget, 'alpha', API, expect='json')
                self.assertEqual([e.code for e in outcome.errors], ['NETWORK_ERROR', 'BUDGET_EXHAUSTED'])
                self.assertEqual((self.sent, budget.calls_used(), budget.refusals), ([API], 1, {name: 1}))

    def test_the_final_phase_reserve_serves_the_gate(self):
        budget = RunBudget('run-1', RunCaps(external_calls=2, reserve_calls=1), self.clock.monotonic)
        self.assertEqual(self.fetch(budget).status, 'ok')
        self.assertEqual(self.fetch(budget, PAGE).errors[0].code, 'BUDGET_EXHAUSTED')
        budget.enter_final_phase()
        self.assertEqual(self.fetch(budget, PAGE).status, 'ok')
        self.assertEqual((self.sent, budget.calls_used()), ([API, PAGE], 2))


if __name__ == '__main__':
    unittest.main()
