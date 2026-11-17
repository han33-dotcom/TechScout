from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import inspect

from app.accounting import BudgetDenied, Charge, Ledger
from app.accounting_admin import initialize, reconcile
from app.storage import Storage, accounting_state


class LedgerAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(TemporaryDirectory())
        self.url = f'sqlite:///{Path(self.tmp) / "accounting.db"}'
        self.storage = Storage(self.url, Path(self.tmp) / 'evidence')
        self.addCleanup(self.storage.engine.dispose)
        self.ledger = Ledger(self.storage, 'campaign', fixture=True)
        initialize(self.ledger)
        self.receipt = dict(id='audit', international=True, fetch_attempts=100, paid_calls=60,
                            active_seconds=900, usd='3.00', external_calls=160,
                            basis='offline test evidence', status='conservative_hold')

    def test_standalone_initialization_needs_no_corpus_or_vector_tables(self):
        self.assertEqual(inspect(self.storage.engine).get_table_names(), ['accounting_state'])
        reconcile(self.ledger, self.receipt)
        initialize(self.ledger)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '3.00')
        with self.assertRaises(BudgetDenied):
            initialize(Ledger(self.storage, 'different-campaign', fixture=True))

    def test_blank_configuration_cannot_fall_back_to_application_database(self):
        for config in [dict(TECHSCOUT_ACCOUNTING_URL='', TECHSCOUT_ACCOUNTING_PROJECT='campaign'),
                       dict(TECHSCOUT_ACCOUNTING_URL='postgresql://host/db', TECHSCOUT_ACCOUNTING_PROJECT=' '), {}]:
            with patch.dict('os.environ', config, clear=True), patch('app.accounting.Storage') as constructor:
                with self.assertRaises(BudgetDenied):
                    Ledger.configured()
                constructor.assert_not_called()

    def test_replay_in_another_connection_preserves_history_without_double_charge(self):
        reconcile(self.ledger, self.receipt)
        other_storage = Storage(self.url, Path(self.tmp) / 'other-evidence')
        self.addCleanup(other_storage.engine.dispose)
        other = Ledger(other_storage, 'campaign', fixture=True)
        reconcile(other, deepcopy(self.receipt))
        state = other.snapshot()
        self.assertEqual(state['scopes']['bootstrap:international'], [100, 60, 900000, '3.00', 160])
        self.assertEqual(state['scopes']['project'], state['scopes']['bootstrap'])
        self.assertEqual(state['attempts'], {})
        self.assertEqual(len(state['reconciliation']), 1)

    def test_missing_state_in_existing_schema_is_not_silently_reset(self):
        with self.storage.engine.begin() as db:
            db.execute(accounting_state.delete())
        with self.assertRaises(BudgetDenied):
            initialize(self.ledger)

    def test_conflicting_receipt_cannot_refund_or_change_scope(self):
        reconcile(self.ledger, self.receipt)
        for key, value in [('usd', '0'), ('international', False), ('fetch_attempts', 0)]:
            changed = dict(self.receipt, **{key: value})
            with self.assertRaises(ValueError):
                reconcile(self.ledger, changed)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3], '3.00')

    def test_paid_allowance_hold_is_enforced_by_normal_reservations(self):
        reconcile(self.ledger, self.receipt)
        job = self.ledger.open_job('next-run', 'bootstrap', international=True)
        with self.assertRaises(BudgetDenied):
            job.reserve('search', Charge(Decimal('.01')), 5, international=True)
        self.assertEqual(len(self.ledger.snapshot()['attempts']), 0)

    def test_overrun_is_recorded_and_blocks_campaign_even_after_reinitialize(self):
        receipt = dict(self.receipt, usd='501')
        with self.assertRaises(BudgetDenied):
            reconcile(self.ledger, receipt)
        initialize(self.ledger)
        state = self.ledger.snapshot()
        self.assertTrue(state['blocked'])
        self.assertEqual(state['scopes']['project'][3], '501')
        self.assertIn('audit', state['reconciliation'])
        reconcile(self.ledger, receipt)  # Idempotent even when blocked.
        job = self.ledger.open_job('blocked')
        with self.assertRaises(BudgetDenied):
            job.reserve('search', Charge(Decimal(0)), 1)

    def test_domestic_receipt_does_not_consume_international_scope(self):
        reconcile(self.ledger, dict(self.receipt, international=False))
        self.assertNotIn('bootstrap:international', self.ledger.snapshot()['scopes'])

    def test_invalid_usage_does_not_create_a_receipt(self):
        for key, value in [('fetch_attempts', -1), ('paid_calls', True), ('active_seconds', 1.5),
                           ('usd', '-1'), ('usd', 'NaN'), ('international', 'false'),
                           ('external_calls', 1), ('status', 'refunded'), ('basis', '')]:
            with self.assertRaises(ValueError):
                reconcile(self.ledger, dict(self.receipt, **{key: value}))
        self.assertEqual(self.ledger.snapshot()['scopes'], {})


if __name__ == '__main__':
    unittest.main()
