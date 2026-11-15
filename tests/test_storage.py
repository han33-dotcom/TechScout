"""Migration and storage smoke checks.

Runs against a temporary SQLite database by default. Set TEST_DATABASE_URL to a
disposable Postgres database to run the same checks there (CI does this).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import contracts as c
from app.main import create_app
from app.storage import RUN_LEASE, Storage, load_fixtures, metadata, runs

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / 'tests' / 'fixtures' / 'contracts.json').read_text(encoding='utf-8'))
STARTED = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


def migrate(url, revision='head'):
    config = Config(str(ROOT / 'alembic.ini'))
    config.attributes['database_url'] = url
    if revision == 'base':
        command.downgrade(config, 'base')
    else:
        command.upgrade(config, revision)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{Path(self.tmp) / 'test.db'}"
        migrate(self.url, 'base')
        migrate(self.url)
        self.storage = Storage(self.url, Path(self.tmp) / 'evidence')

    def tearDown(self):
        self.storage.engine.dispose()
        migrate(self.url, 'base')
        shutil.rmtree(self.tmp, ignore_errors=True)

    def offer(self, amount='1000.00', offer_id='offer-1'):
        data = json.loads(json.dumps(EXAMPLE['offer']))
        data['offer_id'] = offer_id
        data['price']['value']['amount'] = amount
        return c.Offer.model_validate(data)

    def load(self):
        return load_fixtures(self.storage)

    def test_migration_matches_models_and_downgrades(self):
        with self.storage.engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), metadata)
        self.assertEqual(diff, [])
        migrate(self.url, 'base')
        with self.storage.engine.connect() as connection:
            self.assertEqual(
                compare_metadata(MigrationContext.configure(connection), metadata)[0][0],
                'add_table')

    def test_fixtures_load_registry_and_synthetic_offer(self):
        registry = json.loads((ROOT / 'config' / 'sources.json').read_text(encoding='utf-8'))
        self.assertEqual(self.load(), (len(registry['sources']), 1))
        self.assertEqual(self.load(), (len(registry['sources']), 1))  # idempotent
        active = {s.id for s in self.storage.list_sources(status='active')}
        self.assertEqual(active, {s['id'] for s in registry['sources'] if s['status'] == 'active'})
        self.assertEqual(self.storage.load_source('seller-1').status, 'paused')
        self.assertEqual(self.storage.load_offer('offer-1'), c.Offer.model_validate(EXAMPLE['offer']))

    def test_money_is_exact(self):
        self.load()
        for offer_id, amount in [('a', '1099.99'), ('b', '0.10'), ('c', '123456789012.3456789')]:
            self.storage.save_offer(self.offer(amount, offer_id))
            price = self.storage.offer_price(offer_id)
            self.assertIsInstance(price.amount, Decimal)
            self.assertEqual(price.amount, Decimal(amount))
            self.assertEqual(self.storage.load_offer(offer_id).price.value.amount, Decimal(amount))
            self.assertEqual(str(self.storage.load_offer(offer_id).price.value.amount), amount)
        with self.assertRaises(Exception):
            from app.storage import ExactMoney
            ExactMoney().process_bind_param(0.1, self.storage.engine.dialect)

    def test_evidence_references_and_protected_snapshots(self):
        self.load()
        body = b'{"prices": {"price": "100000"}}'
        sha = self.storage.save_snapshot(body, 'application/json')
        self.assertEqual(self.storage.load_snapshot(sha), body)
        # Fresh evidence IDs: stored evidence is immutable, and the fixture cites another hash.
        data = json.loads(self.offer(offer_id='offer-2').model_dump_json().replace('"ev-', '"snap-'))
        for e in data['evidence']:
            e['response_sha256'] = sha
        offer = c.Offer.model_validate(data)
        self.storage.save_offer(offer)
        stored = self.storage.load_offer('offer-2')
        self.assertEqual({e.response_sha256 for e in stored.evidence}, {sha})
        self.assertEqual(self.storage.load_snapshot(stored.evidence[0].response_sha256), body)

        changed = data['evidence'][0] | {'json_pointer': '/elsewhere'}
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.storage.save_offer(c.Offer.model_validate(data | {
                'evidence': [changed] + data['evidence'][1:]}))
        self.assertEqual(self.storage.load_offer('offer-2'), offer)  # rolled back

        path = self.storage.snapshots._path(sha)
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.storage.load_snapshot(sha)
        self.assertEqual(self.storage.save_snapshot(body, 'application/json'), sha)  # repairs it
        self.assertEqual(self.storage.load_snapshot(sha), body)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])
        for bad in ('../' * 3 + 'etc/passwd', 'A' * 64, sha[:-1]):
            with self.assertRaises((ValueError, KeyError)):
                self.storage.load_snapshot(bad)
        with self.assertRaises(KeyError):
            self.storage.load_snapshot('b' * 64)

    def test_offer_needs_known_source(self):
        with self.assertRaisesRegex(ValueError, 'Unknown source'):
            self.storage.save_offer(self.offer())

    def test_interrupted_run_becomes_partial_and_is_not_resumed(self):
        request = c.ShoppingRequest.model_validate(EXAMPLE['request'])
        self.storage.start_run('run-1', request, started_at=STARTED)
        ok = c.ToolResult(status='ok', data={'hits': 2}, evidence_ids=['ev-price'], duration_ms=40)
        with mock.patch('app.storage._now', return_value=STARTED + timedelta(seconds=30)):
            self.assertEqual(self.storage.record_action('run-1', 'search', {'q': 'iPhone 17'}, ok,
                                                        external_calls=1, spend=Decimal('0.10')), 1)
        with mock.patch('app.storage._now', return_value=STARTED + timedelta(seconds=45)):
            self.assertEqual(self.storage.record_action('run-1', 'fetch', {}, ok, external_calls=2,
                                                        planner_turns=1, spend=Decimal('0.20')), 2)

        # The run may still be live in another process until its lease has passed.
        self.assertEqual(self.storage.recover_interrupted_runs(STARTED + RUN_LEASE), [])
        self.assertEqual(self.storage.run_status('run-1'), 'running')
        restarted = STARTED + timedelta(hours=8)
        self.assertEqual(self.storage.recover_interrupted_runs(restarted), ['run-1'])
        self.assertEqual(self.storage.recover_interrupted_runs(restarted), [])
        self.assertEqual(self.storage.run_status('run-1'), 'partial')
        result = self.storage.load_run('run-1')
        self.assertEqual((result.status, result.recommendations, result.finished_at),
                         ('partial', [], restarted))
        self.assertIn('interrupted', result.stop_reason)
        # Elapsed time runs to the last recorded step: the hours the service was down are not counted.
        self.assertEqual(result.usage, c.BudgetUsage(
            elapsed_ms=45000, external_calls=3, planner_turns=1,
            spend={'amount': '0.30', 'currency': 'USD'}))
        self.assertEqual(str(result.usage.spend.amount), '0.30')
        self.assertEqual([a['seq'] for a in self.storage.list_actions('run-1')], [1, 2])
        with self.assertRaisesRegex(ValueError, 'running run'):
            self.storage.record_action('run-1', 'search', {}, ok)
        with self.assertRaisesRegex(ValueError, 'running run'):
            self.storage.finish_run(result.model_copy(update={'status': 'abstained'}))

    def test_usage_in_flight_is_noted_ahead_and_the_result_sets_the_final_totals(self):
        request = c.ShoppingRequest.model_validate(EXAMPLE['request'])
        self.storage.start_run('run-5', request, started_at=STARTED)
        ok = c.ToolResult(status='ok', duration_ms=5)
        self.storage.record_action('run-5', 'search', {}, ok, external_calls=1)
        self.storage.note_usage('run-5', external_calls=2, planner_turns=1, spend=Decimal('0.30'))
        for usage in [dict(spend=Decimal('-1.00')), dict(spend=0.1), dict(external_calls=-1)]:
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                self.storage.note_usage('run-5', **usage)
        result = c.RunResult(run_id='run-5', request_id=request.request_id, status='abstained',
                             stop_reason='no qualifying offer', started_at=STARTED,
                             finished_at=STARTED + timedelta(seconds=5),
                             usage=dict(elapsed_ms=5000, external_calls=2, planner_turns=1,
                                        spend={'amount': '0.12', 'currency': 'USD'}))
        self.storage.finish_run(result)  # one reserved call was never sent; the paid call cost less
        with self.storage.engine.connect() as db:
            row = db.execute(select(runs.c.external_calls, runs.c.planner_turns, runs.c.spend_amount)
                             .where(runs.c.run_id == 'run-5')).one()
        self.assertEqual(tuple(row), (2, 1, Decimal('0.12')))
        with self.assertRaisesRegex(ValueError, 'running run'):
            self.storage.note_usage('run-5', external_calls=1)

    def test_invalid_usage_is_rejected_and_run_still_recovers(self):
        request = c.ShoppingRequest.model_validate(EXAMPLE['request'])
        self.storage.start_run('run-4', request, started_at=STARTED)
        ok = c.ToolResult(status='ok', duration_ms=5)
        for usage in [dict(spend=Decimal('-1.00')), dict(spend=Decimal('NaN')), dict(spend=0.1),
                      dict(external_calls=-1), dict(planner_turns=-1), dict(external_calls=True)]:
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                self.storage.record_action('run-4', 'search', {}, ok, **usage)
        self.assertEqual(self.storage.list_actions('run-4'), [])
        recovered = self.storage.recover_interrupted_runs(STARTED + timedelta(seconds=1), live_for=timedelta(0))
        self.assertEqual(recovered, ['run-4'])
        usage = self.storage.load_run('run-4').usage
        self.assertEqual((usage.elapsed_ms, usage.external_calls, usage.planner_turns, usage.spend.amount),
                         (0, 0, 0, Decimal('0')))

    def test_finished_run_round_trips(self):
        request = c.ShoppingRequest.model_validate(EXAMPLE['request'])
        self.storage.start_run('run-2', request, started_at=STARTED)
        result = c.RunResult(run_id='run-2', request_id=request.request_id, status='abstained',
                             stop_reason='no qualifying offer', started_at=STARTED,
                             finished_at=STARTED + timedelta(seconds=5),
                             usage=dict(elapsed_ms=5000, external_calls=0, planner_turns=1,
                                        spend={'amount': '0', 'currency': 'USD'}))
        self.storage.finish_run(result)
        self.assertEqual(self.storage.load_run('run-2'), result)
        self.assertEqual(self.storage.recover_interrupted_runs(), [])

    def test_app_health_sources_and_startup_recovery(self):
        self.load()
        request = c.ShoppingRequest.model_validate(EXAMPLE['request'])
        self.storage.start_run('run-3', request, started_at=STARTED)
        self.storage.start_run('run-live', request)  # a command-line search started just now
        with TestClient(create_app(self.storage)) as client:
            self.assertEqual(self.storage.run_status('run-3'), 'partial')
            self.assertEqual(self.storage.run_status('run-live'), 'running')
            self.assertEqual(client.get('/health').json(), {'status': 'ok', 'database': 'ok'})
            ids = [s['id'] for s in client.get('/sources').json()]
            self.assertEqual(ids, [s.id for s in self.storage.list_sources(status='active')])
            self.assertNotIn('seller-1', ids)
            self.assertEqual(client.get('/evidence/' + 'a' * 64).status_code, 404)


if __name__ == '__main__':
    unittest.main()
