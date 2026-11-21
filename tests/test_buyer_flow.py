"""First browser-to-result flow (#75): offline, end to end, through the real owned-run API,
worker, shared ledger, Class Cellulars reader and OpenRouter adapter.

OpenRouter is a fake HTTP transport and the store is fake source HTTP serving the dated
minimized Class Cellulars captures, stamped with the real clock. No network, no key and no
paid call is used. The verified case adds synthetic route facts that the real store does not
publish; it is labelled synthetic and is never live evidence.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx2
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import buyer_flow
from app import contracts_v2 as v
from app.accounting import Charge, Ledger
from app.buyer_flow import FlowDependencies, ResearchSetup, execute, listing_resources
from app.buyer_runs import BuyerRunService
from app.buyer_sources import (
    NO_ITEM_ELIGIBILITY, NOT_ROUTE_EVIDENCE, ClassCellularsSource, DeliveryChoice,
)
from app.collection_contracts import RetailerDirectoryEntry
from app.contracts import SourcePolicy
from app.corpus_contracts import ProcessingVersion
from app.directory import CategoryCoverage, Directory, DirectoryRecord
from app.inference import (
    InferenceConfig, InferenceUnavailable, TracedProvider, CallLog, build_inference, load_env_file,
    load_inference_config,
)
from app.main import create_app
from app.openrouter import ENDPOINTS
from app.research_workers import ResearchWorkers
from app.run_worker import execute_one
from app.source_registry import DEFAULT_REGISTRY, get_active_source
from app.sources import FetchOutcome
from app.writer import PROMPTS
from adapters import CLASS_API, CLASS_FAQ, CLASS_PAGE, fixture
import test_corpus as corpus_fixtures

ROOT = Path(__file__).resolve().parents[1]
KEY = 'sk-or-v1-SENTINEL-flow-test-key-7f3c'
MODEL = 'openai/gpt-6.1-sol'
ROLES_BOUND = Decimal('0.06144')   # (16384 * 2.5 + 2048 * 10) / 1e6
WRITER_BOUND = Decimal('0.12288')  # (32768 * 2.5 + 4096 * 10) / 1e6
REQUEST = dict(request_id='flow-1', intent=dict(kind='exact_model', manufacturer='Apple', model='iPhone 17'),
               requirements=dict(category='phone', storage_gb=256),
               fulfillment=dict(destination=dict(city='Beirut')))
STORE = {CLASS_API: fixture('class_cellulars/api.json'), CLASS_PAGE: fixture('class_cellulars/page.html'),
         CLASS_FAQ: fixture('class_cellulars/faq.html')}


def inference_config(**changes):
    data = dict(route=dict(model=MODEL, provider='openai',
                           supported_parameters=['max_tokens', 'response_format', 'structured_outputs'],
                           rates=dict(input='2', cached='0.2', cache_write='2.5', output='10')),
                pricing_checked_on='2026-11-20')
    data.update(changes)
    return InferenceConfig.model_validate(data)


class FakeOpenRouter:
    """Offline OpenRouter: answers each role by its prompt and records every request."""
    def __init__(self, *, metadata=True, cost=True, delay=None, gate=None):
        self.requests, self.metadata, self.cost, self.delay, self.gate = [], metadata, cost, delay or {}, gate
        self.lock = threading.Lock()

    @staticmethod
    def role(instructions):
        for prefix, role in (('Plan electronics research', 'plan'), ('Review the controller', 'critique'),
                             ('Select one supplied resource index', 'research'),
                             ('Select suitable candidate models', 'selection'), (PROMPTS['writer-v1'][:30], 'writer')):
            if instructions.startswith(prefix):
                return role
        raise AssertionError('Unexpected prompt')

    def answer(self, role, data):
        if role == 'plan':
            if data['remaining_tasks'] == 3 and data['resources']:
                return dict(investigations=[dict(question='Check condition and region', resources=[0])],
                            clarification=None)
            return dict(investigations=[], clarification=None)
        if role == 'critique':
            return dict(issue_index=None)
        if role == 'research':
            return dict(resource_index=0)
        if role == 'selection':
            return dict(candidates=[])
        sections = {s: [c['id'] for c in data['claims'] if c['section'] == s] for s in data['sections']}
        return sections

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        role = self.role(body['messages'][0]['content'])
        with self.lock:
            self.requests.append(dict(url=str(request.url), auth=request.headers.get('authorization'),
                                      role=role, body=body))
            count = len(self.requests)
        if self.gate is not None:
            self.gate(role)
        if role in self.delay:
            time.sleep(self.delay[role])
        content = self.answer(role, json.loads(body['messages'][1]['content']))
        usage = dict(prompt_tokens=900, completion_tokens=40, total_tokens=940, is_byok=False)
        cost = self.cost(count) if callable(self.cost) else self.cost
        if cost:
            usage['cost'] = 0.0022
        reply = dict(id=f'gen-{count}', object='chat.completion', model=MODEL,
                     choices=[dict(index=0, finish_reason='stop',
                                   message=dict(role='assistant', content=json.dumps(content)))],
                     usage=usage)
        metadata = self.metadata(count) if callable(self.metadata) else self.metadata
        if metadata:
            reply.update(provider='OpenAI', system_fingerprint='fp-fixture', openrouter_metadata=dict(
                endpoints=dict(available=[dict(provider='OpenAI', model=MODEL, selected=True)])))
        return httpx2.Response(200, content=json.dumps(reply).encode(), headers={'content-type': 'application/json'})

    def roles(self):
        return [r['role'] for r in self.requests]


class LiveFetch:
    """Fake source HTTP: fixture bodies stamped with the real clock, like a live gate reply."""
    def __init__(self, table):
        self.table, self.calls, self.last = dict(table), [], None

    def __call__(self, source_id, url, *, expect, capability=None):
        if url not in self.table:
            raise AssertionError(f'unrouted URL {url}')
        self.calls.append(url)
        now = datetime.now(timezone.utc)
        if self.last is not None and now <= self.last:
            now = self.last + timedelta(microseconds=1)
        self.last = now
        body = self.table[url]
        return FetchOutcome(source_id, url, (url,), 1, 4, final_url=url, status_code=200,
                            content_type='application/json' if expect == 'json' else 'text/html', body=body,
                            response_sha256=hashlib.sha256(body).hexdigest(), observed_at=now)


class SyntheticRouteFacts:
    """SYNTHETIC test double, never live evidence: adds what Class Cellulars does not publish
    (condition, per-item delivery eligibility, a route-scoped shipping charge), each bound to a
    reply the run really received, so the verified path can be exercised offline."""
    def __init__(self, inner):
        self.inner = inner
        self.source_id, self.retailer_id, self.name = inner.source_id, inner.retailer_id, inner.name

    def adapter_version(self):
        return self.inner.adapter_version()

    @staticmethod
    def augment(offer):
        price = next(e for e in offer.evidence if e.field_path == 'price')

        def record(path, scope, **extra):
            data = price.model_dump()
            data.update(evidence_id=f'{price.evidence_id}#synthetic-{path}', field_path=path, scope=scope,
                        excerpt='synthetic test fact', json_pointer=None, **extra)
            return v.Evidence.model_validate(data)
        condition, eligible = record('condition', 'variant'), record('route.item_eligible', 'route',
                                                                     route_id=offer.route.route_id)
        shipping = record('costs.order.shipping', 'route', route_id=offer.route.route_id)
        data = offer.model_dump(mode='python')
        data['condition'] = dict(state='known', value='new', evidence_ids=[condition.evidence_id])
        data['route']['item_eligible'] = dict(state='known', value=True, evidence_ids=[eligible.evidence_id])
        data['costs'] = [c for c in data['costs'] if c['kind'] != 'shipping'] + [
            dict(kind='shipping', leg='order', state='included', evidence_ids=[shipping.evidence_id])]
        data['evidence'] = [*data['evidence'], condition, eligible, shipping]
        return v.Offer.model_validate(data)

    def discover(self, *args, **kwargs):
        read = self.inner.discover(*args, **kwargs)
        return replace(read, offers=tuple(self.augment(o) for o in read.offers))

    def refresh(self, *args, **kwargs):
        read = self.inner.refresh(*args, **kwargs)
        return replace(read, offers=tuple(self.augment(o) for o in read.offers))


class FlowCase(unittest.TestCase):
    def setUp(self):
        corpus_fixtures.CorpusTests.setUp(self)
        self.storage.save_source(SourcePolicy.model_validate(get_active_source('class_cellulars', DEFAULT_REGISTRY)))
        self.service = BuyerRunService(self.storage, clock=lambda: datetime.now(timezone.utc))
        self.app = create_app(self.storage, buyer_service=self.service, cookie_secure=True)
        self.client = self.enterContext(TestClient(self.app, base_url='https://testserver'))
        session = self.client.get('/api/session').json()
        self.headers = {'Origin': 'https://testserver', 'X-CSRF-Token': session['csrf_token']}
        self.ledger = Ledger(self.storage, 'flow-fixture', fixture=True)
        self.ledger.provision()
        self.directory = Directory(self.storage)
        entry = RetailerDirectoryEntry(retailer_id='class_cellulars', name='Class Cellulars',
                                       origin='https://classcellulars.com', markets=('LB',), categories=('phone',),
                                       discovery_status='discovered')
        self.directory.upsert(DirectoryRecord(entry=entry, updated_at=datetime.now(timezone.utc),
                                              source_id='class_cellulars', source_country='LB',
                                              seller_id='class_cellulars', seller_identity='retailer',
                                              product_urls=(CLASS_PAGE,),
                                              coverage=(CategoryCoverage(category='phone', discovered=True),)))
        self.traces = Path(self.storage.snapshots.root).parent / 'traces'
        self.openrouter = FakeOpenRouter()
        self.fetch = LiveFetch(STORE)
        self.processing = ProcessingVersion(corpus_version='buyer-run-v1', index_version='buyer-run-v1',
                                            extraction_version='class_cellulars/1', chunk_version='none')

    def deps(self, *, config=None, source=None, synthetic=False, research=True):
        inference = build_inference(config or inference_config(), api_key=KEY,
                                    transport=httpx2.MockTransport(self.openrouter))
        source = source or ClassCellularsSource(lambda job: self.fetch, storage=self.storage)
        if synthetic:
            source = SyntheticRouteFacts(source)
        setup = ResearchSetup(ResearchWorkers(self.directory, self.corpus, Mock(), {}), self.processing,
                              listing_resources) if research else None
        return FlowDependencies(ledger=self.ledger, inference=inference, source=source, research=setup,
                                traces=self.traces, versions=dict(code='test-commit'))

    def start(self, **changes):
        request = v.ShoppingRequest.model_validate(dict(REQUEST, **changes))
        response = self.client.post('/api/runs', json=request.model_dump(mode='json'), headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return v.RunSnapshot.model_validate(response.json())

    def work(self, deps, *, before=None):
        """One worker claim running the controller; waits until the controller itself returns."""
        finished = threading.Event()

        def controller(context):
            try:
                if before:
                    before(context)
                execute(context, deps)
            finally:
                finished.set()
        self.assertTrue(execute_one(self.service, controller))
        self.assertTrue(finished.wait(30))
        # The runner's thread makes one last run check after the controller returns; let it
        # finish so no database connection outlives the test.
        for thread in threading.enumerate():
            if thread.name == 'buyer-controller':
                thread.join(10)

    def read(self, snapshot):
        response = self.client.get('/api/runs/' + snapshot.run.run_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response, v.ComparisonWorkspace.model_validate(response.json()).current

    def browse(self, deps, **changes):
        snapshot = self.start(**changes)
        self.published = []

        def record(context):
            publish = context.publish

            def recorded(**fields):
                result = publish(**fields)
                self.published.append(result)
                return result
            context.publish = recorded
        self.work(deps, before=record)
        return self.read(snapshot)

    def trace(self, run):
        return json.loads((self.traces / f'{run.run.run_id}-r{run.run.run_revision}.json').read_text())

    def job_attempts(self, run_id):
        return [a for a in self.ledger.snapshot()['attempts'].values() if a['job'] == run_id]


class BrowserFlowTests(FlowCase):
    def test_browser_request_reaches_an_honest_unresolved_outcome(self):
        _, run = self.browse(self.deps())
        self.assertEqual(run.status, 'abstained')
        self.assertEqual(run.stop_reason, buyer_flow.NONE_VERIFIED)
        self.assertEqual(run.recommendations, ())
        self.assertEqual(run.provisional, ())
        self.assertEqual((run.coverage.discovered, run.coverage.retrieved, run.coverage.qualifying), (1, 1, 0))
        self.assertEqual(run.source_outcomes[0].status, 'checked')
        self.assertEqual(len(run.uncertain), 4)
        for item in run.uncertain:
            self.assertIn('condition: required evidence is missing or conflicting', item.reasons)
            self.assertIn('item delivery eligibility: required evidence is missing or conflicting', item.reasons)
            self.assertEqual(item.cost.status, 'incomplete')
            shipping = next(c for c in item.offer.costs if c.kind == 'shipping')
            self.assertEqual((shipping.state, shipping.reason), ('unknown', NOT_ROUTE_EVIDENCE))
        # The three finalists were re-read in the final phase: their price cites this run's re-read.
        rechecked = [u for u in run.uncertain if buyer_flow.REASON_NOT_RECHECKED not in u.reasons]
        self.assertEqual(len(rechecked), 3)
        trace = self.trace(run)
        final = {e['observed_at'] for e in trace['verify']['fetches']}
        for item in rechecked:
            price = next(e for e in item.offer.evidence if e.field_path == 'price')
            self.assertEqual(price.run, run.run)
            self.assertIn(price.observed_at.isoformat(), final)
        self.assertEqual(self.fetch.calls, [CLASS_API, CLASS_PAGE, CLASS_FAQ, CLASS_API, CLASS_PAGE])
        self.assertEqual(run.explanation.source, 'fallback')
        self.assertTrue(run.explanation.text.startswith('No offer was verified in this run.'))
        # The orchestrator and the evaluator answered through OpenRouter and passed local schema validation.
        calls = trace['model_calls']
        self.assertEqual([c['role'] for c in calls], ['orchestrator', 'orchestrator', 'evaluator'])
        self.assertTrue(all(c['validated'] and c['status'] == 'completed' for c in calls))
        self.assertTrue(all(c['usage']['provider'] == 'openrouter' and c['usage']['purpose'] == 'buyer'
                            for c in calls))
        self.assertEqual(calls[0]['usage']['requested_model'], MODEL)
        self.assertEqual(calls[0]['usage']['actual_model'], MODEL)
        self.assertEqual(calls[0]['usage']['serving_provider'], 'OpenAI')
        self.assertTrue(calls[0]['usage']['provider_verified'])
        self.assertEqual(calls[0]['usage']['spend'], 'known')
        self.assertIn('has no reviewed extraction to fetch', ' '.join(trace['research']['gaps']))
        self.assertEqual(trace['versions']['inference']['model'], MODEL)
        self.assertEqual(trace['versions']['inference']['fallback'], 'disabled')
        self.assertEqual(trace['spend']['known_usd'], str(Decimal('0.0022') * 3))
        self.assertEqual(trace['spend']['uncertain_usd'], '0')

    def test_verified_result_with_synthetic_route_facts_and_the_writer(self):
        response, run = self.browse(self.deps(synthetic=True))
        self.assertEqual(run.status, 'complete', run.stop_reason)
        self.assertEqual(len(run.recommendations), 3)
        self.assertEqual(run.coverage.qualifying, 1)
        for group in run.recommendations:
            verified = group.primary
            self.assertEqual(verified.verification.run, run.run)
            self.assertEqual(verified.cost.total, verified.offer.price.value)
            self.assertTrue(run.started_at <= verified.verification.checked_at <= run.finished_at)
        self.assertEqual(len(run.uncertain), 1)
        self.assertIn(buyer_flow.REASON_NOT_RECHECKED, run.uncertain[0].reasons)
        self.assertEqual(run.explanation.source, 'model')
        cited = {e.evidence_id for g in run.recommendations for e in g.primary.offer.evidence}
        self.assertTrue(run.explanation.evidence_ids and set(run.explanation.evidence_ids) <= cited)
        self.assertIn('Costs:', response.json()['current']['explanation']['text'])
        # The writer kept its own prompt version and token allowance.
        writer = next(r for r in self.openrouter.requests if r['role'] == 'writer')
        self.assertEqual(writer['body']['max_tokens'], 4096)
        trace = self.trace(run)
        call = next(c for c in trace['model_calls'] if c['role'] == 'writer')
        self.assertEqual((call['prompt_version'], call['usage']['reserved_usd']), ('writer-v1', str(WRITER_BOUND)))
        roles = next(c for c in trace['model_calls'] if c['role'] == 'orchestrator')
        self.assertEqual((roles['prompt_version'], roles['usage']['reserved_usd']), ('roles-v1', str(ROLES_BOUND)))
        self.assertEqual(trace['writer']['prompt_version'], 'writer-v1')
        # Publishing the result closes the run; that is never recorded as a stop.
        self.assertEqual(trace['stopped_by'], [])
        self.assertFalse(self.ledger.snapshot()['jobs'][run.run.run_id]['cancelled'])

    def test_repeat_reuses_stored_listings_and_still_re_reads_finalists(self):
        _, first = self.browse(self.deps(synthetic=True))
        self.assertEqual(self.trace(first)['source']['mode'], 'live')
        self.fetch.calls.clear()
        _, second = self.browse(self.deps(synthetic=True), request_id='flow-2')
        trace = self.trace(second)
        self.assertEqual(trace['source']['mode'], 'stored')
        # While checking, the browser showed the stored listings with their original observation time.
        early = next(s for s in self.published if s.provisional)
        self.assertTrue(all(c.observed_at < second.started_at for c in early.provisional))
        self.assertTrue(all(c.observed_at in {e.observed_at for e in c.offer.evidence if e.field_path == 'price'}
                            for c in early.provisional))
        # Discovery read nothing but the policy page; the final re-read still went to the store.
        self.assertEqual(self.fetch.calls, [CLASS_FAQ, CLASS_API, CLASS_PAGE])
        self.assertEqual(second.status, 'complete')
        for group in second.recommendations:
            price = next(e for e in group.primary.offer.evidence if e.field_path == 'price')
            self.assertEqual(price.run, second.run)
            self.assertGreaterEqual(price.observed_at, second.started_at)
        # The fourth listing was not re-read: its stored evidence keeps its original time and no run.
        stored = next(e for e in second.uncertain[0].offer.evidence if e.field_path == 'price')
        self.assertIsNone(stored.run)
        self.assertLess(stored.observed_at, second.started_at)
        self.assertIn(buyer_flow.REASON_NOT_RECHECKED, second.uncertain[0].reasons)
        # Versions, latency and cost are recorded with the run.
        self.assertEqual(trace['versions']['code'], 'test-commit')
        self.assertEqual(trace['versions']['inference']['roles']['prompt_version'], 'roles-v1')
        self.assertEqual(trace['versions']['inference']['writer']['prompt_version'], 'writer-v1')
        self.assertIsInstance(trace['latency_ms'], int)
        self.assertEqual(trace['spend']['ledger'], 'read')
        self.assertEqual(trace['status'], 'complete')

    def test_inference_runs_on_openrouter_only_without_openai_key_or_fallback(self):
        environ = {k: v for k, v in os.environ.items() if k != 'OPENAI_API_KEY'}
        with patch.dict(os.environ, environ, clear=True), \
                patch('app.model_provider.OpenAIProvider.__init__', side_effect=AssertionError('direct OpenAI')):
            _, run = self.browse(self.deps(synthetic=True))
        self.assertEqual(run.status, 'complete')
        self.assertEqual(self.openrouter.roles(), ['plan', 'plan', 'critique', 'writer'])
        for request in self.openrouter.requests:
            body = request['body']
            self.assertEqual(request['url'], ENDPOINTS[0])
            self.assertEqual(request['auth'], f'Bearer {KEY}')
            self.assertEqual(body['model'], MODEL)
            self.assertEqual(body['provider'], dict(order=['openai'], only=['openai'], allow_fallbacks=False,
                                                    require_parameters=True, data_collection='deny'))
            self.assertFalse({'models', 'route', 'tools', 'plugins', 'user'} & set(body))
        trace = self.trace(run)
        self.assertTrue(all(c['usage']['fallback'] == dict(allowed=False, used=False, triggers=[], refused=None)
                            for c in trace['model_calls']))

    def test_key_stays_in_the_backend_transport(self):
        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))
        handler = Keep(level=logging.DEBUG)
        root = logging.getLogger()
        old = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old)
        deps = self.deps(synthetic=True)
        response, run = self.browse(deps)
        self.assertTrue(self.openrouter.requests and all(r['auth'] == f'Bearer {KEY}' for r in self.openrouter.requests))
        exposed = {
            'buyer API response': response.text,
            'run trace': (self.traces / f'{run.run.run_id}-r1.json').read_text(),
            'ledger': json.dumps(self.ledger.snapshot(), default=str),
            'logs': '\n'.join(records),
            'inference repr': repr(deps.inference) + repr(deps.inference.roles),
        }
        for name, text in exposed.items():
            self.assertNotIn(KEY, text, name)
            self.assertNotIn('Bearer', text, name)
        # The browser bundle and its configuration never name the provider or its key.
        files = [*Path(ROOT, 'frontend/src').rglob('*'), ROOT / 'frontend/vite.config.js', ROOT / 'frontend/index.html',
                 *(Path(ROOT, 'frontend/dist').rglob('*') if (ROOT / 'frontend/dist').exists() else [])]
        for path in files:
            if path.is_file():
                text = path.read_text(encoding='utf-8', errors='ignore')
                self.assertNotIn('OPENROUTER', text.upper(), path)
        example = (ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertIn('\nOPENROUTER_API_KEY=\n', example)
        self.assertNotIn('VITE_OPENROUTER', example)


class LimitTests(FlowCase):
    def test_buyer_cancellation_stops_the_model_call_and_publication(self):
        arrived, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def gate(role):
            if role == 'plan':
                arrived.set()
                release.wait(10)
        self.openrouter.gate = gate
        snapshot = self.start()
        done = threading.Event()

        def cancel():
            self.assertTrue(arrived.wait(10))
            response = self.client.post(f'/api/runs/{snapshot.run.run_id}/cancel',
                                        json={'run': snapshot.run.model_dump(mode='json')}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            done.set()
        canceller = threading.Thread(target=cancel)
        canceller.start()
        self.work(self.deps(synthetic=True))
        release.set()
        canceller.join(5)
        self.assertTrue(done.is_set())
        _, run = self.read(snapshot)
        self.assertEqual(run.status, 'cancelled')
        self.assertIsNone(run.explanation)
        self.assertEqual(self.openrouter.roles(), ['plan'])
        self.assertEqual(self.fetch.calls, [CLASS_API, CLASS_PAGE, CLASS_FAQ])  # no final re-read
        self.assertTrue(self.ledger.snapshot()['jobs'][snapshot.run.run_id]['cancelled'])
        trace = self.trace(run)
        self.assertEqual(trace['status'], 'cancelled')
        self.assertIn('cancelled', trace['stopped_by'])
        plan = trace['model_calls'][0]
        self.assertEqual((plan['status'], plan['usage']['spend']), ('cancelled', 'uncertain'))

    def test_re_read_citing_an_earlier_reply_of_this_run_is_not_verified(self):
        # The v2 contract accepts any current-run evidence; only the #70 re-check rule notices that
        # a "re-read" copy still cites the discovery replies (for example a reader's cached copy).
        source = SyntheticRouteFacts(ClassCellularsSource(lambda job: self.fetch, storage=self.storage))
        inner_refresh = source.refresh

        def cached(*args, earlier, **kwargs):
            fresh = inner_refresh(*args, earlier=earlier, **kwargs)  # real requests, real final replies
            return replace(fresh, offers=earlier.offers, originals=earlier.originals)
        source.refresh = cached
        _, run = self.browse(self.deps(source=source))
        self.assertEqual(run.recommendations, ())
        rechecked = [u for u in run.uncertain if buyer_flow.REASON_RECHECK_FAILED in u.reasons]
        self.assertEqual(len(rechecked), 3)
        self.assertEqual(set(self.trace(run)['verify']['outcomes'].values()), {'not_current'})

    def test_listings_that_cannot_be_converted_are_a_named_data_gap(self):
        source = SyntheticRouteFacts(ClassCellularsSource(lambda job: self.fetch, storage=self.storage))
        inner_discover = source.discover

        def unreadable(*args, **kwargs):
            read = inner_discover(*args, **kwargs)
            return replace(read, offers=(), skipped=tuple(f'{o.offer_id}: invalid offer' for o in read.offers))
        source.discover = unreadable
        _, run = self.browse(self.deps(source=source))
        self.assertEqual((run.status, run.stop_reason), ('abstained', buyer_flow.UNREADABLE))
        self.assertEqual((run.source_outcomes[0].status, run.source_outcomes[0].reason),
                         ('failed', buyer_flow.UNREADABLE))
        self.assertEqual(len(self.trace(run)['source']['skipped']), 4)

    def test_requests_that_would_outlast_the_run_deadline_are_refused_unsent(self):
        with patch.object(buyer_flow, 'FINAL_BY', 20):  # less than one full gate request needs
            _, run = self.browse(self.deps())
        self.assertEqual((run.status, run.stop_reason), ('partial', buyer_flow.LIMIT.format('deadline')))
        self.assertEqual(self.fetch.calls, [])
        trace = self.trace(run)
        self.assertGreaterEqual(trace['source']['refused'], 1)
        self.assertEqual(trace['source']['fetches'][0]['status'], 'refused')

    def test_final_re_read_is_skipped_when_one_request_no_longer_fits(self):
        with patch.object(buyer_flow, 'FETCH_ROOM_S', 1000):
            _, run = self.browse(self.deps(synthetic=True))
        self.assertEqual((run.status, run.stop_reason), ('partial', buyer_flow.LIMIT.format('deadline')))
        self.assertEqual(run.recommendations, ())
        self.assertEqual(self.fetch.calls, [CLASS_API, CLASS_PAGE, CLASS_FAQ])
        self.assertEqual(sum(buyer_flow.REASON_NOT_RECHECKED in u.reasons for u in run.uncertain), 4)

    def test_writer_lease_and_wait_fit_the_time_left(self):
        with patch.object(buyer_flow, 'FINAL_BY', 30):
            _, run = self.browse(self.deps(synthetic=True))
        writer = self.trace(run)['writer']
        self.assertLess(writer['lease_seconds'], 29)
        self.assertGreaterEqual(writer['final_deadline_left_s'], 0)
        call = next((c for c in self.trace(run)['model_calls'] if c['role'] == 'writer'), None)
        if call is not None:
            self.assertLessEqual(call['wall_ms'], writer['lease_seconds'] * 1000)

    def test_one_failed_run_check_is_retried_not_treated_as_cancellation(self):
        failures = []

        def flaky(context):
            check = context.cancelled

            def once():
                # Only the flow's watcher sees the blip; the worker runner's own loop is unaffected.
                if not failures and threading.current_thread().name == 'buyer-flow-watch':
                    failures.append('database blip')
                    raise RuntimeError('transient')
                return check()
            context.cancelled = once
        snapshot = self.start()
        self.work(self.deps(synthetic=True), before=flaky)
        _, run = self.read(snapshot)
        self.assertEqual(failures, ['database blip'])
        self.assertEqual(run.status, 'complete')
        self.assertFalse(self.ledger.snapshot()['jobs'][run.run.run_id]['cancelled'])
        self.assertEqual(self.trace(run)['stopped_by'], [])

    def test_failed_final_re_read_verifies_nothing_and_does_not_crash(self):
        source = SyntheticRouteFacts(ClassCellularsSource(lambda job: self.fetch, storage=self.storage))
        source.refresh = Mock(side_effect=RuntimeError('store unreachable'))
        _, run = self.browse(self.deps(source=source))
        self.assertEqual((run.status, run.stop_reason), ('abstained', buyer_flow.NONE_VERIFIED))
        self.assertEqual(run.recommendations, ())
        self.assertEqual(sum(buyer_flow.REASON_NOT_RETURNED in u.reasons for u in run.uncertain), 3)
        self.assertEqual(self.trace(run)['verify_failure'], 'RuntimeError')

    def test_run_deadline_stops_before_any_external_call(self):
        # The per-request room check is switched off so only the run's own deadline can stop it.
        with patch.object(buyer_flow, 'FINAL_BY', 0), patch('app.buyer_sources.FETCH_ROOM_S', float('-inf')):
            _, run = self.browse(self.deps())
        self.assertEqual(run.status, 'partial')
        self.assertEqual(run.stop_reason, buyer_flow.LIMIT.format('deadline'))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.openrouter.requests, [])
        self.assertIn('deadline', self.trace(run)['stopped_by'])

    def test_provider_timeout_is_recorded_and_writer_respects_the_final_deadline(self):
        self.openrouter.delay = {'plan': 1.5}
        config = inference_config(roles=dict(max_input_tokens=16384, max_output_tokens=2048, timeout_seconds=0.5))
        with patch.object(buyer_flow, 'MIN_WRITER_SECONDS', 1000):
            _, run = self.browse(self.deps(config=config, synthetic=True))
        trace = self.trace(run)
        plan = trace['model_calls'][0]
        self.assertEqual((plan['role'], plan['status'], plan['usage']['spend']), ('orchestrator', 'timeout', 'uncertain'))
        self.assertLess(plan['wall_ms'], 1400)
        self.assertNotIn('writer', self.openrouter.roles())
        self.assertEqual(run.status, 'partial')
        self.assertEqual(run.stop_reason, buyer_flow.LIMIT.format('deadline'))
        self.assertTrue(run.recommendations)
        self.assertEqual(run.explanation.source, 'fallback')
        self.assertEqual(trace['writer']['reason'], 'no time left for the writer')
        attempt = next(a for a in self.job_attempts(run.run.run_id) if a['kind'] == 'model')
        self.assertEqual((attempt['status'], attempt['values'][3]), ('uncertain', str(ROLES_BOUND)))

    def test_spend_cap_refuses_role_calls_before_they_are_sent(self):
        snapshot = self.start()
        job = self.ledger.open_job(snapshot.run.run_id)
        held = job.reserve('model', Charge(Decimal('0.70')), 30)
        held.dispatch()
        held.complete(Decimal('0.70'), 1.0)
        self.work(self.deps())
        _, run = self.read(snapshot)
        self.assertEqual(self.openrouter.requests, [])
        trace = self.trace(run)
        self.assertEqual({c['status'] for c in trace['model_calls']}, {'budget_denied'})
        self.assertTrue(all(c['usage']['spend'] == 'none' for c in trace['model_calls']))
        self.assertIn('spend', trace['stopped_by'])
        self.assertEqual((run.status, run.stop_reason), ('partial', buyer_flow.LIMIT.format('spend')))
        self.assertEqual(self.ledger.snapshot()['scopes'][f'buyer:{run.run.run_id}'][3], '0.70')

    def test_missing_metadata_and_unreported_cost_stay_explicit(self):
        self.openrouter.metadata = lambda count: False
        self.openrouter.cost = lambda count: count != 2
        _, run = self.browse(self.deps())
        calls = self.trace(run)['model_calls']
        first, second = calls[0], calls[1]
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(set(first['usage']['metadata_unavailable']),
                         {'serving_provider', 'endpoint_metadata', 'system_fingerprint'})
        self.assertIsNone(first['usage']['serving_provider'])
        self.assertEqual((second['status'], second['usage']['spend']), ('unknown_usage', 'uncertain'))
        self.assertIn('full reservation', second['usage']['spend_note'])
        self.assertEqual(self.trace(run)['spend']['uncertain_usd'], str(ROLES_BOUND))


class ScopeAndConfigurationTests(FlowCase):
    def test_out_of_scope_requests_abstain_without_ledger_calls_or_fetches(self):
        cases = [
            (dict(request_id='needs', intent=dict(kind='needs', description='A phone with a good camera')),
             buyer_flow.OUT_OF_SCOPE),
            (dict(request_id='laptop', requirements=dict(category='laptop')), buyer_flow.OUT_OF_SCOPE),
            (dict(request_id='no-city', fulfillment=dict()), buyer_flow.NEEDS_CITY),
            (dict(request_id='pickup', fulfillment=dict(mode='pickup', destination=dict(city='Beirut'))),
             buyer_flow.NO_PICKUP),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason, request=changes['request_id']):
                _, run = self.browse(self.deps(), **changes)
                self.assertEqual((run.status, run.stop_reason), ('abstained', reason))
                self.assertNotIn(run.run.run_id, self.ledger.snapshot()['jobs'])
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.openrouter.requests, [])

    def test_unconfigured_worker_publishes_an_explicit_error(self):
        snapshot = self.start()
        problem = InferenceUnavailable('OpenRouter inference requires a backend OPENROUTER_API_KEY')
        with patch.object(buyer_flow, '_PRODUCTION', None), \
                patch.object(buyer_flow, 'production_dependencies', side_effect=problem), \
                self.assertLogs('techscout.buyer_flow', 'WARNING') as logs:
            finished = threading.Event()

            def controller(context):
                try:
                    buyer_flow.run(context)
                finally:
                    finished.set()
            execute_one(self.service, controller)
            self.assertTrue(finished.wait(10))
        _, run = self.read(snapshot)
        self.assertEqual((run.status, run.stop_reason), ('error', buyer_flow.NOT_CONFIGURED))
        self.assertIn('OPENROUTER_API_KEY', '\n'.join(logs.output))  # the operator's detail stays in the log

    def test_production_wiring_uses_openrouter_without_an_openai_key(self):
        path = Path(self.storage.snapshots.root).parent / 'inference.json'
        path.write_text(json.dumps({'inference': inference_config().model_dump(mode='json')}), encoding='utf-8')
        environ = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'OPENROUTER_API_KEY')}
        environ.update(TECHSCOUT_INFERENCE_CONFIG=str(path), OPENROUTER_API_KEY=KEY, DATABASE_URL=self.url)
        with patch.object(buyer_flow, 'load_env_file', return_value=()), patch.dict(os.environ, environ, clear=True), \
                patch('app.accounting.Ledger.configured', return_value=self.ledger), \
                patch('app.model_provider.OpenAIProvider.__init__', side_effect=AssertionError('direct OpenAI')):
            deps = buyer_flow.production_dependencies()
        self.addCleanup(deps.source.storage.engine.dispose)
        from app.openrouter import OpenRouterProvider
        self.assertIsInstance(deps.inference.roles, OpenRouterProvider)
        self.assertEqual(deps.inference.roles.settings.purpose, 'buyer')
        self.assertEqual(deps.inference.writer(30).settings.prompt_version, 'writer-v1')
        self.assertEqual(deps.research.workers.profiles, {})
        self.assertIsNone(deps.research.workers.provider)  # the flow injects the traced OpenRouter provider
        self.assertEqual(deps.versions['source']['adapter_version'], 'class_cellulars/1')
        self.assertEqual(len(deps.versions['inference_config_sha256']), 64)
        self.assertNotIn(KEY, repr(deps))

    def test_production_wiring_fails_before_anything_without_config_or_key(self):
        missing = Path(self.storage.snapshots.root).parent / 'missing.json'
        with patch.object(buyer_flow, 'load_env_file', return_value=()), \
                patch.dict(os.environ, {'TECHSCOUT_INFERENCE_CONFIG': str(missing)}):
            with self.assertRaisesRegex(InferenceUnavailable, 'No runtime inference configuration'):
                buyer_flow.production_dependencies()
        path = Path(self.storage.snapshots.root).parent / 'inference.json'
        path.write_text(json.dumps({'inference': inference_config().model_dump(mode='json')}), encoding='utf-8')
        environ = {k: v for k, v in os.environ.items() if k not in ('OPENROUTER_API_KEY',)}
        environ.update(TECHSCOUT_INFERENCE_CONFIG=str(path), OPENAI_API_KEY='sk-direct-openai-is-not-accepted')
        with patch.object(buyer_flow, 'load_env_file', return_value=()), patch.dict(os.environ, environ, clear=True):
            with self.assertRaisesRegex(InferenceUnavailable, 'OPENROUTER_API_KEY'):
                buyer_flow.production_dependencies()


class InferenceConfigTests(unittest.TestCase):
    def write(self, directory, data):
        path = Path(directory) / 'inference.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_unpriced_unpinned_or_credential_bearing_configurations_fail_explicitly(self):
        from tempfile import TemporaryDirectory
        good = inference_config().model_dump(mode='json')
        route = good['route']
        cases = {
            'zero input price': dict(good, route=dict(route, rates=dict(route['rates'], input='0'))),
            'missing output price': dict(good, route=dict(route, rates={k: v for k, v in route['rates'].items()
                                                                       if k != 'output'})),
            'no prices': dict(good, route={k: v for k, v in route.items() if k != 'rates'}),
            'no pricing date': {k: v for k, v in good.items() if k != 'pricing_checked_on'},
            'credential in file': dict(good, api_key='sk-or-should-never-be-echoed'),
            'fallback list': dict(good, routes=[route, route], allow_fallback=True),
            'router model': dict(good, route=dict(route, model='openrouter/auto')),
            'writer above the final reserve': dict(good, writer=dict(max_input_tokens=100000, max_output_tokens=4096)),
            'missing structured outputs': dict(good, route=dict(route, supported_parameters=['max_tokens'])),
        }
        with TemporaryDirectory() as directory:
            for name, data in cases.items():
                with self.subTest(name):
                    path = self.write(directory, {'inference': data})
                    try:
                        config = load_inference_config(path)
                    except InferenceUnavailable as exc:
                        self.assertNotIn('sk-or-should-never-be-echoed', str(exc))
                        continue
                    # Capability gaps are refused per call, before any reservation or request.
                    self.assertEqual(name, 'missing structured outputs')
                    provider = build_inference(config, api_key=KEY, transport=httpx2.MockTransport(
                        lambda request: self.fail('nothing may be sent'))).roles
                    budget = Mock(cancelled=False)
                    result = provider.generate('x', {}, v.RunReference, budget=budget)
                    self.assertEqual(result.status, 'unsupported_parameters')
                    budget.reserve_spend.assert_not_called()
            self.assertRaisesRegex(InferenceUnavailable, 'No runtime inference configuration',
                                   load_inference_config, Path(directory) / 'absent.json')
            bad = Path(directory) / 'bad.json'
            bad.write_text('{not json', encoding='utf-8')
            self.assertRaises(InferenceUnavailable, load_inference_config, bad)
            self.assertRaises(InferenceUnavailable, load_inference_config, self.write(directory, {'other': {}}))
        self.assertRaisesRegex(InferenceUnavailable, 'OPENROUTER_API_KEY', build_inference, inference_config(),
                               environ={'OPENAI_API_KEY': 'sk-direct'})

    def test_example_configuration_documents_every_field_and_is_not_live(self):
        example = json.loads((ROOT / 'config/inference.example.json').read_text(encoding='utf-8'))
        self.assertEqual(set(example), {'inference'})
        with self.assertRaises(ValidationError):  # placeholders are not prices or a confirmed route
            InferenceConfig.model_validate(example['inference'])

    def test_env_file_loader_sets_only_named_unset_keys_and_returns_no_values(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('﻿# comment\nOPENAI_API_KEY=sk-direct\nexport OPENROUTER_API_KEY="sk-or-file"\n'
                            'POSTGRES_PASSWORD=secret\n', encoding='utf-8')
            environ = {}
            self.assertEqual(load_env_file(path, environ=environ), ('OPENROUTER_API_KEY',))
            self.assertEqual(environ, {'OPENROUTER_API_KEY': 'sk-or-file'})
            environ = {'OPENROUTER_API_KEY': 'sk-or-shell'}
            self.assertEqual(load_env_file(path, environ=environ), ())
            self.assertEqual(environ['OPENROUTER_API_KEY'], 'sk-or-shell')
            self.assertEqual(load_env_file(Path(directory) / 'absent', environ={}), ())


class LiveCheckTests(FlowCase):
    def test_dry_run_reports_presence_never_values_and_sends_nothing(self):
        from app.live_check import check, summarize
        root = Path(self.storage.snapshots.root).parent / 'repo'
        (root / 'frontend').mkdir(parents=True)
        (root / '.env').write_text(f'OPENROUTER_API_KEY={KEY}' + chr(10), encoding='utf-8')
        path = root / 'inference.json'
        path.write_text(json.dumps({'inference': inference_config().model_dump(mode='json')}), encoding='utf-8')
        environ = dict(TECHSCOUT_INFERENCE_CONFIG=str(path), TECHSCOUT_ACCOUNTING_URL='postgresql://ledger',
                       TECHSCOUT_ACCOUNTING_PROJECT='techscout-december-2026')
        with patch('app.openrouter.post_json', side_effect=AssertionError('nothing may be sent')), \
                patch('app.model_provider.post_json', side_effect=AssertionError('nothing may be sent')):
            ready, lines = check(environ=environ, root=root)
        text = chr(10).join(lines)
        self.assertFalse(ready)  # no application database is configured here
        self.assertNotIn(KEY, text)
        self.assertIn('OK      OPENROUTER_API_KEY', text)
        self.assertIn(f'one role call reserves USD {ROLES_BOUND}', text)
        self.assertIn(f'one writer call reserves USD {WRITER_BOUND}', text)
        self.assertIn('MISSING application database', text)
        (root / 'frontend' / '.env.local').write_text('VITE_OPENROUTER_API_KEY=x' + chr(10), encoding='utf-8')
        _, lines = check(environ=environ, root=root)
        self.assertIn('MISSING key kept out of the browser build', chr(10).join(lines))
        # The evidence summary of a finished run names models, providers, usage, latency and spend.
        _, run = self.browse(self.deps(synthetic=True))
        summary = summarize(self.traces / f'{run.run.run_id}-r1.json')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(summary['validated_responses'], 4)
        self.assertEqual(summary['model_calls'][0]['reported_model'], MODEL)
        self.assertEqual(summary['model_calls'][0]['serving_provider'], 'OpenAI')
        self.assertEqual(summary['discovery']['mode'], 'live')
        self.assertEqual(summary['final_recheck']['finalists'], 3)
        self.assertNotIn(KEY, json.dumps(summary))


class FreshDatabaseTests(unittest.TestCase):
    def setUp(self):
        corpus_fixtures.CorpusTests.setUp(self)  # migrated, with no class_cellulars source row

    def test_first_live_read_registers_the_reviewed_source_and_keeps_the_listings(self):
        self.assertIsNone(self.storage.load_source('class_cellulars'))
        run = v.RunReference(run_id='fresh', run_revision=1, request_id='flow-1', request_revision=1)
        source = ClassCellularsSource(lambda job: LiveFetch(STORE), storage=self.storage)
        read = source.discover(v.ShoppingRequest.model_validate(REQUEST), DeliveryChoice('Beirut'), None, run)
        self.assertEqual((read.mode, len(read.offers), read.errors), ('live', 4, ()))
        self.assertEqual(self.storage.load_source('class_cellulars').status, 'active')
        self.assertEqual(len(self.storage.recent_offers('class_cellulars', datetime.now(timezone.utc)
                                                        - timedelta(minutes=5))), 4)

    def test_a_storage_failure_never_discards_a_successful_read(self):
        run = v.RunReference(run_id='fresh', run_revision=1, request_id='flow-1', request_revision=1)
        source = ClassCellularsSource(lambda job: LiveFetch(STORE), storage=self.storage)
        with patch.object(self.storage, 'save_offer', side_effect=ValueError('disk full')):
            read = source.discover(v.ShoppingRequest.model_validate(REQUEST), DeliveryChoice('Beirut'), None, run)
        self.assertEqual(len(read.offers), 4)
        self.assertIn('listings could not be stored for reuse (ValueError)', read.errors)


class SourceAndRoleTests(FlowCase):
    def test_conversion_keeps_unknowns_and_binds_run_only_to_this_runs_replies(self):
        run = v.RunReference(run_id='convert', run_revision=1, request_id='flow-1', request_revision=1)
        request = v.ShoppingRequest.model_validate(REQUEST)
        source = ClassCellularsSource(lambda job: self.fetch)
        read = source.discover(request, DeliveryChoice('Beirut'), None, run)
        self.assertEqual((read.mode, len(read.offers), read.skipped), ('live', 4, ()))
        offer = read.offers[0]
        self.assertEqual(offer.condition.state, 'unknown')
        self.assertEqual((offer.route.item_eligible.state, offer.route.item_eligible.reason),
                         ('unknown', NO_ITEM_ELIGIBILITY))
        self.assertEqual(next(c for c in offer.costs if c.kind == 'tax').state, 'included')
        self.assertTrue(all(e.run == run and e.origin == 'runtime' for e in offer.evidence))
        self.assertEqual(str(offer.listing_url), CLASS_PAGE)
        # Listings stored by an earlier run come back without this run's reference.
        source = ClassCellularsSource(lambda job: self.fetch, storage=self.storage)
        source.discover(request, DeliveryChoice('Beirut'), None, run)
        later = run.model_copy(update={'run_id': 'later'})
        stored = source.discover(request, DeliveryChoice('Beirut'), None, later)
        self.assertEqual(stored.mode, 'stored')
        records = [e for e in stored.offers[0].evidence if e.field_path == 'price']
        self.assertTrue(records and all(e.run is None for e in records))
        self.assertTrue(any(e.run == later for e in stored.offers[0].evidence if e.field_path == 'payment'))
        # A stored listing for another delivery city is not reused.
        self.assertEqual(source.discover(request, DeliveryChoice('Tyre'), None, later).mode, 'live')

    def test_research_workers_skip_the_model_when_no_extraction_can_fetch(self):
        import test_research_workers as research
        fixture_case = research.ResearchWorkerTests()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        provider = Mock()
        workers = ResearchWorkers(fixture_case.directory, fixture_case.corpus, fixture_case.gate, {},
                                  provider=provider, page_reader=fixture_case.reader)
        result, = workers.execute(fixture_case.job, [fixture_case.work(tools=('corpus', 'fetch', 'model'))], timeout=10)
        self.assertEqual(result['status'], 'completed')
        provider.generate.assert_not_called()
        fixture_case.reader.assert_not_called()
        self.assertIn('has no reviewed extraction to fetch', ' '.join(result['result'].finding.gaps))
        # With an extraction profile the research worker's choice goes through OpenRouter.
        log = CallLog()
        inference = build_inference(inference_config(), api_key=KEY, transport=httpx2.MockTransport(self.openrouter))
        fixture_case.workers.provider = TracedProvider(inference.roles, log)
        report = fixture_case.run_work(fixture_case.work(tools=('corpus', 'fetch', 'model')))
        self.assertEqual(self.openrouter.roles()[0], 'research')
        self.assertEqual((log.entries()[0]['role'], log.entries()[0]['validated']), ('researcher', True))
        self.assertEqual(report.trace[-1].action, 'fetch')

    def test_explanation_belongs_only_to_finished_runs(self):
        snapshot = self.start()
        data = snapshot.model_dump(mode='json')
        explanation = dict(text='No offer was verified in this run.', source='fallback')
        for status in ('running', 'cancelled', 'error'):
            finished = None if status == 'running' else data['updated_at']
            with self.subTest(status), self.assertRaises(ValidationError):
                v.RunSnapshot.model_validate(dict(data, status=status, finished_at=finished, stop_reason='x',
                                                  explanation=explanation))
        done = v.RunSnapshot.model_validate(dict(data, status='abstained', finished_at=data['updated_at'],
                                                 stop_reason='x', explanation=explanation))
        self.assertEqual(done.explanation.source, 'fallback')
        self.assertIsNone(v.RunSnapshot.model_validate(data).explanation)  # older snapshots stay readable


if __name__ == '__main__':
    unittest.main()
