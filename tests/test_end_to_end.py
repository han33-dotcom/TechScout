"""One search from request to result over the real pieces (MVP 05-12), fully offline.

The real SourceGate, store readers, fixed rules, run controller and RulePlanner run together.
A scripted transport serves the minimized store fixtures for the admitted endpoints, a fake
resolver and clock stand in for DNS and time, and any socket use fails the test.
"""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import httpx2

from app.adapters import ADAPTERS
from app.contracts import ShoppingRequest
from app import runs
from app.runs import STOP_NO_ACTION, STOP_NO_INVESTIGATION, RunCaps, RunDependencies, run_search
from app.sources import SourceGate, SourceLimiter
from app.storage import Storage, metadata

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / 'config' / 'sources.json'
FIXTURES = ROOT / 'tests' / 'fixtures' / 'stores'
PUBLIC = '104.18.0.10'
CONDITION = 'condition is not published'


def request(**changes) -> ShoppingRequest:
    fields = {
        'request_id': 'e2e-1', 'manufacturer': 'Apple', 'model': 'iPhone 17', 'storage_gb': 256,
        'allowed_colors': ['any'], 'sim': 'any', 'allowed_regions': ['any'], 'warranty_requirement': None,
        'budget': {'amount': '1100.00', 'currency': 'USD'}, 'payment_basis': 'cash',
        'fulfillment': {'mode': 'delivery', 'country': 'LB', 'city': 'Beirut'}, 'allowed_markets': ['LB'],
        'deadline': None, **changes}
    return ShoppingRequest.model_validate(fields)


class Clock:
    def __init__(self):
        self.mono, self.wall = 1000.0, datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.mono

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class Body(httpx2.SyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    def __iter__(self):
        yield self.data


def fixture_files() -> dict[str, Path]:
    """Each admitted endpoint and the minimized fixture that stands in for its reply."""
    files = {}
    for source_id, adapter in ADAPTERS.items():
        for product in adapter.config.products:
            files[product.api_url] = FIXTURES / source_id / 'api.json'
            files[product.page_url] = FIXTURES / source_id / 'page.html'
        for policy in adapter.config.policies:
            files[policy.url] = FIXTURES / source_id / f'{policy.policy_id}.html'
    return files


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.getaddrinfo', 'socket.create_connection', 'socket.socket.connect'):
            patcher = mock.patch(target, side_effect=AssertionError(f'real network use: {target}'))
            patcher.start()
            self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        tmp = Path(directory.name)
        self.storage = Storage(f"sqlite:///{tmp / 'e2e.db'}", tmp / 'evidence')
        metadata.create_all(self.storage.engine)
        self.addCleanup(self.storage.engine.dispose)
        self.clock, self.files, self.sent = Clock(), fixture_files(), []

    def handle(self, http_request) -> httpx2.Response:
        url = str(http_request.url)
        self.sent.append(url)
        self.clock.advance(0.4)
        path = self.files.get(url)
        if path is None:
            return httpx2.Response(404, stream=Body(b''), headers=[('Content-Length', '0')])
        body = path.read_bytes()
        content_type = 'application/json' if path.suffix == '.json' else 'text/html; charset=UTF-8'
        return httpx2.Response(200, stream=Body(body), headers=[('Content-Type', content_type),
                                                                ('Content-Length', str(len(body)))])

    def deps(self) -> RunDependencies:
        gate = SourceGate._for_tests(REGISTRY, transport=httpx2.MockTransport(self.handle),
                                     resolver=lambda host, port: [PUBLIC], clock=self.clock.monotonic,
                                     wall_clock=self.clock.now, sleep=self.clock.advance,
                                     limiter=SourceLimiter())
        return RunDependencies(gate=gate, storage=self.storage, adapters=ADAPTERS, registry_path=REGISTRY,
                               wall_clock=self.clock.now, clock=self.clock.monotonic)

    def search(self, shopping_request: ShoppingRequest, *, caps: RunCaps = RunCaps(), run_id='e2e-run'):
        with self.assertLogs('techscout.trace', 'INFO'):
            result = run_search(shopping_request, self.deps(), run_id=run_id, caps=caps)
        self.assertEqual(self.storage.load_run(run_id), result)
        return result

    def by_store(self, issues) -> dict[str, list]:
        grouped = {}
        for issue in issues:
            grouped.setdefault(issue.offer_id.split(':')[0], []).append(issue)
        return grouped

    def test_todays_stores_abstain_because_no_store_states_the_condition(self):
        result = self.search(request())
        self.assertEqual((result.status, result.stop_reason), ('abstained', STOP_NO_ACTION))
        self.assertEqual(result.recommendations, [])
        # Two requests per store to read its product, then the FAQ the rule planner chose.
        products = [ADAPTERS[s].config.products[0] for s in ('class_cellulars', 'mojitech', 'beytech')]
        self.assertEqual(self.sent, [*(url for p in products for url in (p.api_url, p.page_url)),
                                     'https://classcellulars.com/faq/'])
        self.assertEqual((result.usage.external_calls, result.usage.planner_turns), (7, 1))
        self.assertEqual(result.usage.spend.amount, 0)
        self.assertEqual({c.source_id: (c.status, c.candidates_seen) for c in result.source_coverage},
                         {'class_cellulars': ('ok', 4), 'mojitech': ('ok', 12), 'beytech': ('ok', 4)})
        unresolved, rejected = self.by_store(result.unresolved), self.by_store(result.rejected)
        # The FAQ settled cash payment, so condition is Class Cellulars' only open question.
        self.assertEqual([issue.reasons for issue in unresolved['class_cellulars']], [[CONDITION]] * 4)
        self.assertEqual(len(unresolved['beytech']), 4)
        self.assertTrue(all(CONDITION in issue.reasons for issue in unresolved['beytech']))
        # Every Mojitech option shows a zero price and cannot be bought.
        self.assertEqual(len(rejected['mojitech']), 12)
        self.assertTrue(all('store lists the variant as not purchasable' in issue.reasons
                            for issue in rejected['mojitech']))
        self.assertNotIn('mojitech', unresolved)

    def test_the_no_investigation_baseline_reads_only_the_products(self):
        result = self.search(request(), caps=RunCaps(planner_turns=0))
        self.assertEqual((result.status, result.stop_reason), ('abstained', STOP_NO_INVESTIGATION))
        self.assertEqual(len(self.sent), 6)
        class_issues = self.by_store(result.unresolved)['class_cellulars']
        class_reasons = {tuple(issue.reasons) for issue in class_issues}
        self.assertEqual(class_reasons, {(CONDITION, 'payment terms are not published')})

    def test_a_colour_the_stores_do_not_sell_rejects_every_offer(self):
        result = self.search(request(allowed_colors=['Cosmic Orange']))
        self.assertEqual(result.status, 'abstained')
        self.assertEqual(result.unresolved, [])
        self.assertEqual(len(result.rejected), 20)
        # Rejected offers have nothing worth investigating, so no planner turn or extra request.
        self.assertEqual((result.usage.external_calls, result.usage.planner_turns), (6, 0))

    def test_every_stored_record_cites_a_stored_reply(self):
        result = self.search(request())
        stored = json.dumps([a['result'] for a in self.storage.list_actions('e2e-run')])
        self.assertNotIn('Traceback', stored)
        for issue in [*result.unresolved, *result.rejected]:
            offer = self.storage.load_offer(issue.offer_id)
            self.assertIsNotNone(offer)
            for evidence in offer.evidence:
                self.assertIsNotNone(self.storage.load_snapshot(evidence.response_sha256))

    def test_the_search_command_prints_the_stored_result(self):
        out, err = io.StringIO(), io.StringIO()
        with (self.assertLogs('techscout.trace', 'INFO'), contextlib.redirect_stdout(out),
              contextlib.redirect_stderr(err)):
            code = runs.main(['search', str(ROOT / 'examples' / 'request-iphone17-beirut.json'),
                              '--planner', 'none'], dependencies=self.deps())
        self.assertEqual((code, err.getvalue()), (0, ''))
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['status'], printed['stop_reason']), ('abstained', STOP_NO_INVESTIGATION))
        self.assertEqual(self.storage.load_run(printed['run_id']).model_dump(mode='json'), printed)
        self.assertEqual(len(self.sent), 6)

    def test_the_search_command_fails_when_no_store_could_be_read(self):
        self.files = {}  # every endpoint answers 404
        out = io.StringIO()
        with self.assertLogs('techscout.trace', 'INFO'), contextlib.redirect_stdout(out):
            code = runs.main(['search', str(ROOT / 'examples' / 'request-iphone17-beirut.json')],
                             dependencies=self.deps())
        printed = json.loads(out.getvalue())
        self.assertEqual((code, printed['status'], printed['stop_reason']), (1, 'error', runs.STOP_NO_STORE_READ))
        self.assertEqual({c['status'] for c in printed['source_coverage']}, {'error'})
        self.assertEqual(len(self.sent), 3)  # each store's product record, once

    def test_the_search_command_rejects_an_unreadable_request(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = runs.main(['search', str(ROOT / 'examples' / 'missing.json')], dependencies=self.deps())
        self.assertEqual((code, err.getvalue().strip()), (2, 'Cannot read the request: FileNotFoundError'))
        self.assertEqual(self.sent, [])


    def test_the_model_planner_without_credentials_stops_before_any_request(self):
        err = io.StringIO()
        missing = ValueError('AnthropicPlanner needs API credentials, such as ANTHROPIC_API_KEY')
        with mock.patch('app.planner.AnthropicPlanner', side_effect=missing), contextlib.redirect_stderr(err):
            code = runs.main(['search', str(ROOT / 'examples' / 'request-iphone17-beirut.json'),
                              '--planner', 'model'])
        self.assertEqual((code, err.getvalue().strip()), (2, 'Cannot set up the model planner: ValueError'))
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
