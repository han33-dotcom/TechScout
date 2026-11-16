from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from app.accounting import Ledger, Charge, BudgetDenied, LIMITS
from app.contracts import SourceLimits
from app.research import research
from app.storage import Storage
from test_storage import migrate

D = Decimal
START = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
LIMIT = SourceLimits(requests_per_minute=6, concurrency=1, max_requests_per_run=4)


def contender(args):
    url, index, source = args
    storage = Storage(url)
    try:
        ledger = Ledger(storage, 'test-campaign', fixture=True)
        job = ledger.open_job(f'process-{index}', 'bootstrap' if source else 'buyer')
        try:
            attempt = job.reserve('retrieval' if source else 'model', Charge(D(0) if source else D('.7')), 30,
                                  **({'source': 'same-store', 'limits': LIMIT} if source else {}))
            attempt.dispatch()
            return True
        except BudgetDenied:
            return False
    finally:
        storage.engine.dispose()


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(TemporaryDirectory())
        self.url = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{Path(self.tmp) / "ledger.db"}'
        migrate(self.url, 'base')
        migrate(self.url)
        self.storage = Storage(self.url, Path(self.tmp) / 'evidence')
        self.addCleanup(migrate, self.url, 'base')
        self.addCleanup(self.storage.engine.dispose)
        self.now = START
        self.ledger = Ledger(self.storage, 'test-campaign', fixture=True, clock=lambda: self.now)
        self.ledger.provision()

    def test_explicit_provisioning_and_no_reset(self):
        self.ledger.credit_purchase('one', D(500), D(5))
        self.ledger.provision()
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '5')
        with self.assertRaises(BudgetDenied):
            Ledger(self.storage, 'another', fixture=True).provision()
        if self.storage.engine.dialect.name == 'sqlite':
            with self.assertRaises(ValueError):
                Ledger(self.storage, 'test-campaign')

    def test_final_phase_is_inside_original_run_deadline(self):
        job = self.ledger.open_job('buyer')
        a = job.reserve('search', Charge(D('.75')), 10)
        a.dispatch()
        a.complete(D('.75'), 1)
        with self.assertRaises(BudgetDenied):
            job.reserve('model', Charge(D('.01')), 10)
        self.now += timedelta(seconds=91)
        same = self.ledger.open_job('buyer')
        with self.assertRaises(BudgetDenied):
            same.reserve('model', Charge(D('.1')), 1)
        same.enter_final_phase()
        same.reserve('model', Charge(D('.25')), 29)
        with self.assertRaises(BudgetDenied):
            same.reserve('model', Charge(D(0)), 30)

    def test_all_attempt_kinds_and_retries_charge_once(self):
        job = self.ledger.open_job('bootstrap', 'bootstrap')
        for kind in ('model', 'search', 'embedding', 'model'):
            a = job.reserve(kind, Charge(D('.1'), gateway=D('.01'), byok=D('.01')), 5)
            a.dispatch()
            a.complete(D('.12'), 1)
        a = job.reserve('retrieval', Charge(D(0)), 5, source='store', limits=LIMIT)
        a.dispatch()
        a.complete(D(0), 1)
        self.assertEqual(self.ledger.snapshot()['scopes']['bootstrap'], [1,4,5000,'0.48',5])

    def test_unknown_quote_rejected_and_uncertain_billing_held(self):
        job = self.ledger.open_job('buyer')
        with self.assertRaises(ValueError):
            job.reserve('model', Charge(None), 5)
        a = job.reserve('model', Charge(D('.6')), 5)
        a.dispatch()
        a.uncertain()
        with self.assertRaises(BudgetDenied):
            job.reserve('model', Charge(D('.2')), 5)
        self.assertEqual(self.ledger.snapshot()['attempts'][a.token]['status'], 'uncertain')

    def test_expiry_keeps_resources_and_rejects_stale_owner(self):
        job = self.ledger.open_job('buyer')
        a = job.reserve('model', Charge(D('.7')), 5)
        a.dispatch()
        self.now += timedelta(seconds=6)
        self.ledger.recover()
        for action in (a.release, lambda: a.complete(D(0), 1), a.dispatch):
            with self.assertRaises(BudgetDenied):
                action()
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.7')

    def test_release_only_before_dispatch_and_settle_once(self):
        job = self.ledger.open_job('buyer')
        a = job.reserve('model', Charge(D('.7')), 5)
        a.release()
        b = job.reserve('model', Charge(D('.7')), 5)
        b.dispatch()
        with self.assertRaises(BudgetDenied):
            b.release()
        b.complete(D('.1'), 1)
        with self.assertRaises(BudgetDenied):
            b.complete(D('.1'), 1)

    def test_overrun_is_recorded_and_blocks_campaign(self):
        a = self.ledger.open_job('buyer').reserve('model', Charge(D('.1')), 5)
        a.dispatch()
        with self.assertRaises(BudgetDenied):
            a.complete(D('.2'), 1)
        self.assertTrue(self.ledger.snapshot()['blocked'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.2')

    def test_prepaid_credit_consumption_and_fees_not_double_counted(self):
        self.ledger.credit_purchase('receipt', D(100), D(5))
        self.ledger.credit_purchase('receipt', D(100), D(5))
        a = self.ledger.open_job('buyer').reserve('model', Charge(D('.5')), 5)
        a.dispatch()
        a.complete(D('.5'), 1)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '5.5')

    def test_each_aggregate_bound_including_international_subset(self):
        # Seed immediately below each documented bound, then prove an atomic
        # reservation cannot cross it or silently reset on another job ID.
        for kind in ('bootstrap', 'maintenance'):
            for international in (False, True):
                profile = kind + (':international' if international else '')
                for dimension, ceiling in enumerate(LIMITS[profile][:4]):
                    with self.subTest(profile=profile, dimension=dimension):
                        with self.ledger._transaction() as (state, _):
                            state['scopes'], state['attempts'], state['jobs'] = {}, {}, {}
                        job = self.ledger.open_job('one', kind, international=international)
                        with self.ledger._transaction() as (state, _):
                            key = self.ledger._scope_keys('one', state['jobs']['one'], international)[-1][0]
                            used = [0,0,0,'0',0]
                            used[dimension] = str(ceiling) if dimension == 3 else ceiling
                            state['scopes'][key] = used
                        resumed = self.ledger.open_job('another-worker', kind, international=international)
                        with self.assertRaises(BudgetDenied):
                            resumed.reserve('retrieval', Charge(D('.1')), 1, source='store', limits=LIMIT,
                                            international=international)

    def test_foreign_opt_in_and_utc_day_cannot_change_on_resume(self):
        job = self.ledger.open_job('maintenance', 'maintenance')
        with self.assertRaises(BudgetDenied):
            job.reserve('model', Charge(D('.1')), 1, international=True)
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('maintenance', 'maintenance', international=True)
        self.now += timedelta(days=1)
        with self.assertRaises(BudgetDenied):
            job.reserve('model', Charge(D('.1')), 1)

    def test_interactive_capacity_survives_background_pressure(self):
        for i in range(2):
            self.ledger.open_job(str(i),'bootstrap').reserve('embedding', Charge(D('.1')), 10)
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('third','bootstrap').reserve('embedding', Charge(D('.1')), 10)
        self.ledger.open_job('interactive').reserve('model', Charge(D('.1')), 10)

    def test_separate_processes_share_spend(self):
        self.ledger.credit_purchase('fee', D(0), D('499.2'))
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(contender, [(self.url,i,False) for i in range(8)]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(D(self.ledger.snapshot()['scopes']['project'][3]), D('499.9'))

    def test_separate_ingestion_processes_share_source_lease(self):
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(contender, [(self.url,i,True) for i in range(8)]))
        self.assertEqual(sum(results), 1)

    def test_parallel_roles_cannot_delegate_recursively(self):
        job = self.ledger.open_job('research')
        barrier = threading.Barrier(3)
        def client(request, handle, stop):
            barrier.wait(timeout=5)
            with self.assertRaises(ValueError):
                research(handle, [client], request)
            a = handle.reserve('search', Charge(D('.1')), 5)
            a.dispatch()
            a.complete(D('.1'), 1)
            return request
        result = research(job, [client]*3, 'laptop')
        self.assertEqual([r['status'] for r in result], ['completed']*3)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '0.3')

    def test_timeout_cancels_dispatch_and_keeps_completed_results(self):
        job = self.ledger.open_job('research')
        finished = threading.Event()
        def slow(request, handle, stop):
            stop.wait(2)
            try:
                with self.assertRaises(BudgetDenied):
                    handle.reserve('model', Charge(D('.1')), 1)
            finally:
                finished.set()
        result = research(job, [lambda *args: 'partial', slow], None, timeout=.1)
        self.assertEqual(result[0], {'status':'completed','result':'partial'})
        self.assertEqual(result[1]['status'], 'cancelled')
        self.assertTrue(finished.wait(2))

    def test_three_worker_limit_is_durable(self):
        job = self.ledger.open_job('research')
        for _ in range(3):
            job.reserve('worker', Charge(D(0)), 30)
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('research').reserve('worker', Charge(D(0)), 30)

    def test_existing_controller_budget_records_local_and_shared_usage(self):
        from app.runs import AccountedRunBudget
        budget = AccountedRunBudget('v1', job=self.ledger.open_job('v1'),
            registry_path='tests/fixtures/source-capabilities.json', model_seconds=5)
        call = budget.reserve_call('alpha', 5)
        self.assertTrue(call.still_wanted())
        call.commit(1)
        spend = budget.reserve_spend(D('.2'))
        spend.commit(D('.1'))
        self.assertEqual(budget.calls_used('alpha'), 1)
        self.assertEqual(budget.spend_total(), D('.1'))
        self.assertEqual(self.ledger.snapshot()['scopes']['buyer:v1'][4], 2)
        budget.enter_final_phase()
        budget.close()
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('v1').reserve('model', Charge(D('.1')), 1)

    def test_source_rate_and_per_job_caps_persist_after_completion(self):
        job = self.ledger.open_job('source', 'bootstrap')
        for _ in range(4):
            a = job.reserve('retrieval', Charge(D(0)), 1, source='same', limits=LIMIT)
            a.dispatch()
            a.complete(D(0), .1)
        self.now += timedelta(minutes=2)
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('source','bootstrap').reserve('retrieval', Charge(D(0)), 1, source='same', limits=LIMIT)
        other = self.ledger.open_job('other', 'bootstrap')
        tight = SourceLimits(requests_per_minute=1, concurrency=1, max_requests_per_run=4)
        a = other.reserve('retrieval', Charge(D(0)), 1, source='same', limits=tight)
        a.dispatch()
        a.complete(D(0), .1)
        with self.assertRaises(BudgetDenied):
            self.ledger.open_job('third','bootstrap').reserve('retrieval', Charge(D(0)), 1, source='same', limits=tight)
