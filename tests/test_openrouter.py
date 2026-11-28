"""Offline acceptance tests for the optional OpenRouter adapter (#89).

Every request goes to an injected fake client or an httpx MockTransport. No key,
account, credit purchase or network call is used.
"""
from copy import deepcopy
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import httpx2
from pydantic import ValidationError

from app.accounting import Charge, Ledger
from app.contracts import Model
from app.model_provider import JobBudget, OpenAIPlanner, OpenAIProvider
from app.openrouter import (ENDPOINTS, OpenRouterConfig, OpenRouterProvider, ProviderUnavailable,
                            load_openrouter_config, route_fingerprint)
from app.provider_http import post_json
import test_corpus as fixtures
from test_planner import state

ROOT = Path(__file__).resolve().parents[1]
KEY = 'sk-or-v1-synthetic-test-key-0000'
PARAMS = ['max_tokens', 'response_format', 'structured_outputs', 'reasoning', 'tools']
PRIMARY_BOUND = Decimal('0.0512')     # (16384 * 2.5 + 1024 * 10) / 1e6
FALLBACK_BOUND = Decimal('0.064512')  # (16384 * 3 + 1024 * 15) / 1e6
EXACT_COST = Decimal('0.000379')      # 50*2 + 20*0.2 + 30*2.5 + 20*10 per million


def route(model='openai/gpt-6.1-sol', provider='openai', **rates):
    return dict(model=model, provider=provider, supported_parameters=PARAMS,
                rates=dict(dict(input='2', cached='0.2', cache_write='2.5', output='10'), **rates))


def config(**changes):
    data = dict(enabled=True, routes=[route()], max_input_tokens=16384, max_output_tokens=1024,
                timeout_seconds=10, pricing_checked_on='2026-11-17')
    data.update(changes)
    return OpenRouterConfig.model_validate(data)


def experiment(**changes):
    data = dict(purpose='experiment', experiment='exp-provider-comparison', allow_fallback=True,
                routes=[route(), route(provider='azure', input='3', output='15')])
    data.update(changes)
    return config(**data)


class Answer(Model):
    answer: str


def reply(model='openai/gpt-6.1-sol', text='{"answer":"ok"}', cost=EXACT_COST, provider='openai'):
    return dict(id='gen-fixture-1', object='chat.completion', created=1, model=model,
                system_fingerprint='fp_fixture',
                choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content=text))],
                usage=dict(prompt_tokens=100, completion_tokens=20, total_tokens=120, cost=cost, is_byok=False,
                           prompt_tokens_details=dict(cached_tokens=20, cache_write_tokens=30),
                           completion_tokens_details=dict(reasoning_tokens=5)),
                openrouter_metadata=dict(
                    endpoints=dict(available=[dict(model=model, provider=provider, selected=True)]),
                    attempts=[dict(provider=provider, model=model, status=200)]))


def http_error(code, body=b'{}'):
    request = httpx2.Request('POST', ENDPOINTS[0])
    return httpx2.HTTPStatusError('private provider text', request=request,
                                  response=httpx2.Response(code, request=request, content=body))


def wire(body):
    """Serialize like OpenRouter: Decimal money becomes a JSON number."""
    return json.dumps(body, default=float).encode()


class Recording:
    """Pass-through budget that remembers each requested reservation."""
    def __init__(self, inner):
        self.inner, self.amounts = inner, []

    @property
    def cancelled(self):
        return self.inner.cancelled

    def reserve_spend(self, amount):
        self.amounts.append(amount)
        return self.inner.reserve_spend(amount)


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.ledger = Ledger(self.storage, 'openrouter-fixture', fixture=True)
        self.ledger.provision()
        self.job = self.ledger.open_job('buyer')
        self.budget = JobBudget(self.job)
        self.sent = []

    def provider(self, responses=None, settings=None):
        """A provider whose fake client returns (or raises) queued replies and records payloads."""
        queue = list(responses if responses is not None else [reply()])

        def client(payload):
            self.sent.append(deepcopy(payload))
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item() if callable(item) else item
        return OpenRouterProvider(settings or config(), client=client)

    def generate(self, responses=None, settings=None, budget=None, **kwargs):
        self.sent = []
        return self.provider(responses, settings).generate(
            'Return an answer.', {'question': 'synthetic buyer text'}, Answer,
            budget=budget or self.budget, **kwargs)

    def fresh(self, name):
        self.budget = JobBudget(self.ledger.open_job(name))
        return self.budget

    def attempts(self):
        return list(self.ledger.snapshot()['attempts'].values())

    # Off by default, keys and configuration

    def test_explicit_evaluation_route_and_legacy_direct_entry_points(self):
        settings = load_openrouter_config()
        self.assertFalse(settings.enabled)
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': KEY}, clear=True):
            with self.assertRaises(ProviderUnavailable) as raised:
                OpenRouterProvider()
            self.assertIn('disabled', str(raised.exception))
            self.assertNotIn(KEY, str(raised.exception))
            with self.assertRaises(ProviderUnavailable):
                OpenRouterProvider(settings, client=lambda payload: reply())
        raw = json.loads((ROOT / 'config' / 'evaluation.json').read_text(encoding='utf-8'))
        self.assertEqual(raw['model_provider']['provider'], 'openai')
        self.assertEqual(raw['evaluation_inference']['route']['model'], 'openai/gpt-6.1-sol')
        # Existing explicit v1 direct entry points retain their configuration.
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-openai-key'}, clear=True):
            from app.runs import _planner
            from app.writer import OpenAIWriter
            self.assertIsInstance(_planner('model').provider, OpenAIProvider)
            self.assertIsInstance(OpenAIWriter(self.budget).provider, OpenAIProvider)

    def test_enabled_adapter_needs_a_backend_key(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-openai-key'}, clear=True):
            with self.assertRaisesRegex(ProviderUnavailable, 'OPENROUTER_API_KEY'):
                OpenRouterProvider(config())
            for blank in ('', '   '):
                with self.assertRaises(ProviderUnavailable):
                    OpenRouterProvider(config(), api_key=blank)
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': KEY}, clear=True):
            self.assertEqual(OpenRouterProvider(config()).name, 'openrouter')

    def test_configuration_allowlists_and_pinning_rules(self):
        two = [route(), route(provider='azure')]
        bad = {
            'unapproved endpoint': dict(endpoint='https://gateway.example/api/v1/chat/completions'),
            'plain http endpoint': dict(endpoint='http://openrouter.ai/api/v1/chat/completions'),
            'auto router model': dict(routes=[route('openrouter/auto')]),
            'cheapest-provider variant': dict(routes=[route('openai/gpt-6.1-sol:floor')]),
            'fastest-provider variant': dict(routes=[route('openai/gpt-6.1-sol:nitro')]),
            'hosted web search variant': dict(routes=[route('openai/gpt-6.1-sol:online')]),
            'model without author': dict(routes=[route('gpt-6.1-sol')]),
            'invalid provider slug': dict(routes=[route(provider='OpenAI Inc')]),
            'gateway sorting': dict(sort='price'),
            'gateway model list': dict(models=['openai/gpt-6.1-sol']),
            'zero price': dict(routes=[route(input='0')]),
            'float price': dict(routes=[route(output=10.0)]),
            'no routes': dict(routes=[]),
            'no pricing date': dict(pricing_checked_on=None),
            'fallback route without opt-in': dict(routes=two),
            'fallback in the main evaluation': dict(allow_fallback=True, routes=two),
            'experiment without label': dict(purpose='experiment'),
            'label on the main evaluation': dict(experiment='exp-side-test'),
            'experiment label without exp- prefix': dict(purpose='experiment', experiment='side-test'),
            'experiment labelled main': dict(purpose='experiment', experiment='main'),
            'string boolean': dict(enabled='true'),
            'duplicate routes': dict(purpose='experiment', experiment='exp-x', allow_fallback=True,
                                     routes=[route(), route()]),
            'more than three routes': dict(purpose='experiment', experiment='exp-x', allow_fallback=True,
                                           routes=two + [route(provider='p3'), route(provider='p4')]),
            'fallback with one route': dict(purpose='experiment', experiment='exp-x', allow_fallback=True),
            'unknown fallback trigger': dict(purpose='experiment', experiment='exp-x', allow_fallback=True,
                                             routes=two, fallback_on=['invalid_output']),
            'no fallback trigger': dict(purpose='experiment', experiment='exp-x', allow_fallback=True,
                                        routes=two, fallback_on=[]),
        }
        for name, change in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    config(**change)
        self.assertTrue(experiment().allow_fallback)
        self.assertFalse(OpenRouterConfig(routes=()).enabled)
        # model_copy skips validation; the provider re-validates before use.
        bypasses = {'fallback on the main evaluation': config().model_copy(update=dict(
                        allow_fallback=True, routes=experiment().routes)),
                    'enabled without routes': OpenRouterConfig().model_copy(update=dict(enabled=True)),
                    'unapproved endpoint': config().model_copy(update=dict(endpoint='https://gateway.example/v1'))}
        for name, copied in bypasses.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    OpenRouterProvider(copied, client=lambda payload: reply())
        self.assertEqual(OpenRouterProvider(config(), client=lambda payload: reply()).settings, config())
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'openrouter.json'
            path.write_text(json.dumps({'openrouter': config().model_dump(mode='json')}), encoding='utf-8')
            self.assertEqual(load_openrouter_config(path), config())
        with self.assertRaisesRegex(ProviderUnavailable, 'no openrouter section'):
            load_openrouter_config(ROOT / 'config' / 'evaluation.json')

    # Request shape: explicit model, pinned provider, no gateway fallbacks or tools

    def test_request_pins_one_model_and_provider_without_gateway_fallbacks(self):
        result = self.generate()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.value.answer, 'ok')
        p = self.sent[0]
        self.assertEqual(p['model'], 'openai/gpt-6.1-sol')
        self.assertEqual(p['provider'], dict(order=['openai'], only=['openai'], allow_fallbacks=False,
                                             require_parameters=True, data_collection='deny'))
        for absent in ('models', 'route', 'tools', 'tool_choice', 'plugins', 'user', 'reasoning'):
            self.assertNotIn(absent, p)
        self.assertIs(p['stream'], False)
        self.assertEqual(p['max_tokens'], 1024)
        fmt = p['response_format']
        self.assertEqual((fmt['type'], fmt['json_schema']['strict']), ('json_schema', True))
        self.assertFalse(fmt['json_schema']['schema']['additionalProperties'])
        self.assertEqual([m['role'] for m in p['messages']], ['system', 'user'])
        self.assertEqual(json.loads(p['messages'][1]['content']), {'question': 'synthetic buyer text'})
        self.generate(settings=config(zdr=True, effort='low', data_collection='allow'))
        p = self.sent[0]
        self.assertTrue(p['provider']['zdr'])
        self.assertEqual(p['provider']['data_collection'], 'allow')
        self.assertEqual(p['reasoning'], {'effort': 'low'})

    def test_unsupported_parameters_are_refused_before_sending_and_by_the_gateway(self):
        cases = {
            'no structured outputs': config(routes=[dict(route(), supported_parameters=['max_tokens', 'response_format'])]),
            'no token limit': config(routes=[dict(route(), supported_parameters=['response_format', 'structured_outputs'])]),
            'reasoning not supported': config(effort='high', routes=[dict(route(), supported_parameters=[
                'max_tokens', 'response_format', 'structured_outputs'])]),
        }
        for name, settings in cases.items():
            with self.subTest(name):
                result = self.generate(settings=settings)
                self.assertEqual(result.status, 'unsupported_parameters')
                self.assertEqual(result.usage['billing'], 'not_sent')
                self.assertEqual(self.sent, [])
        self.assertEqual(self.attempts(), [])
        # A gateway capability refusal is explicit and never falls back, even in an experiment.
        result = self.generate([http_error(400), reply()], settings=experiment())
        self.assertEqual(result.status, 'invalid_request')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result.usage['attempts'][0]['error_code'], 400)

    # Model substitution

    def test_model_substitution_is_detected_rejected_and_still_accounted(self):
        message_model = reply()
        message_model['choices'][0]['message']['model'] = 'openai/gpt-6.1-mini'
        missing = reply()
        del missing['model']
        cases = {'reported model': reply(model='openai/gpt-6.1-mini'), 'message model': message_model,
                 'missing model': missing}
        for name, r in cases.items():
            with self.subTest(name):
                self.fresh(name)
                result = self.generate([r])
                self.assertEqual(result.status, 'model_mismatch')
                self.assertIsNone(result.value)
                self.assertIn('model_mismatch', result.usage['deviations'])
                self.assertEqual(result.usage['billing'], 'known')
                self.assertEqual(Decimal(result.usage['actual_usd']), EXACT_COST)
        reported = next(a for a in self.attempts() if a['job'] == 'reported model')
        self.assertEqual(reported['provider_usage']['actual_model'], 'openai/gpt-6.1-mini')
        self.assertIn('actual_model', result.usage['metadata_unavailable'])
        self.assertTrue(all(a['status'] == 'completed' for a in self.attempts()))

    # Fallback

    def test_fallback_is_off_by_default(self):
        result = self.generate([http_error(503), reply()])
        self.assertEqual(result.status, 'provider_unavailable')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result.usage['fallback'], dict(allowed=False, used=False, triggers=[], refused=None))
        self.assertEqual([a['status'] for a in self.attempts()], ['uncertain'])
        self.assertEqual(self.attempts()[0]['values'][3], str(PRIMARY_BOUND))

    def test_explicit_fallback_is_separately_reserved_recorded_and_labelled(self):
        for code, trigger in ((503, 'provider_unavailable'), (429, 'rate_limited')):
            with self.subTest(trigger):
                budget = Recording(self.fresh(trigger))
                result = self.generate([http_error(code), reply(provider='azure')], settings=experiment(),
                                       budget=budget)
                self.assertEqual(result.status, 'completed')
                self.assertEqual([p['provider']['only'] for p in self.sent], [['openai'], ['azure']])
                self.assertEqual({p['model'] for p in self.sent}, {'openai/gpt-6.1-sol'})
                self.assertEqual(budget.amounts, [PRIMARY_BOUND, FALLBACK_BOUND])
                u = result.usage
                self.assertEqual(u['fallback'], dict(allowed=True, used=True, triggers=[trigger], refused=None))
                self.assertIn('fallback_used', u['deviations'])
                self.assertEqual((u['purpose'], u['experiment']), ('experiment', 'exp-provider-comparison'))
                self.assertEqual((u['requested_provider'], u['serving_provider']), ('azure', 'azure'))
                self.assertEqual([a['billing'] for a in u['attempts']], ['unknown', 'known'])
                # The failed attempt may have been billed, so the total stays an unknown upper bound.
                self.assertEqual(u['billing'], 'unknown')
                self.assertIsNone(u['actual_usd'])
                self.assertEqual(Decimal(u['reserved_usd']), PRIMARY_BOUND + FALLBACK_BOUND)
        rows = [a for a in self.attempts()]
        self.assertEqual(sorted(a['status'] for a in rows), ['completed', 'completed', 'uncertain', 'uncertain'])
        self.assertTrue(all(a['provider_usage']['experiment'] == 'exp-provider-comparison' for a in rows))
        # Cross-model fallback through the planner bridge: every billed model is attributed.
        cross = experiment(routes=[route(), route('openai/gpt-6.1-mini', 'azure', input='3', output='15')])
        choice = reply(model='openai/gpt-6.1-mini', provider='azure',
                       text=json.dumps(dict(kind='act', action_id='A1', reason='settles gaps')))
        decision = OpenAIPlanner(self.provider([http_error(503), choice], cross)).decide(state(), self.fresh('cross'))
        self.assertEqual(decision.kind, 'act')
        self.assertEqual(decision.usage.model, 'openai/gpt-6.1-mini')
        self.assertEqual(decision.usage.attempt_models, ['openai/gpt-6.1-sol', 'openai/gpt-6.1-mini'])
        self.assertFalse(decision.usage.priced)  # the failed first attempt's cost is unknown

    def test_fallback_is_denied_outside_its_explicit_bounds(self):
        cases = {
            'not a trigger': (experiment(), [http_error(400), reply()], 'invalid_request'),
            'model substitution': (experiment(), [reply(model='openai/gpt-6.1-mini'), reply()], 'model_mismatch'),
            'trigger not configured': (experiment(fallback_on=['rate_limited']), [http_error(503), reply()],
                                       'provider_unavailable'),
            'invalid output': (experiment(), [reply(text='{}'), reply()], 'invalid_output'),
        }
        for name, (settings, responses, status) in cases.items():
            with self.subTest(name):
                self.fresh(name)
                result = self.generate(responses, settings=settings)
                self.assertEqual(result.status, status)
                self.assertEqual(len(self.sent), 1)
                self.assertFalse(result.usage['fallback']['used'])
        # Budget exhaustion: the failed first attempt keeps its bound, so the fallback cannot fit.
        # The result reports the request actually sent and records the refused fallback.
        self.fresh('budget')
        held = self.budget.job.reserve('model', Charge(Decimal('0.69')), 30)
        result = self.generate([http_error(503), reply()], settings=experiment())
        self.assertEqual(result.status, 'provider_unavailable')
        self.assertEqual(len(self.sent), 1)
        u = result.usage
        self.assertEqual([a['billing'] for a in u['attempts']], ['unknown', 'not_sent'])
        self.assertEqual(u['fallback'], dict(allowed=True, used=False, triggers=['provider_unavailable'],
                                             refused='budget_denied'))
        self.assertNotIn('fallback_used', u['deviations'])
        self.assertEqual((u['requested_provider'], Decimal(u['reserved_usd'])), ('openai', PRIMARY_BOUND))
        held.release()
        # The run controller's budget refuses all spend after an unknown cost, so there the
        # fallback is refused too; the planner attributes the spend to the model actually called.
        from app.runs import AccountedRunBudget, RunCaps, _SpendView
        run_budget = AccountedRunBudget('buyer', RunCaps(), job=self.fresh('run-budget').job,
                                        registry_path='config/sources.json', model_seconds=30)
        cross = experiment(routes=[route(), route('openai/gpt-6.1-mini', 'azure', input='3', output='15')])
        self.sent = []
        decision = OpenAIPlanner(self.provider([http_error(503), reply()], cross)).decide(
            state(), _SpendView(run_budget))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((decision.kind, decision.reason), ('invalid', 'openrouter: provider_unavailable'))
        self.assertEqual((decision.usage.model, decision.usage.attempt_models, decision.usage.priced),
                         ('openai/gpt-6.1-sol', ['openai/gpt-6.1-sol'], False))
        # Cancellation during the first attempt stops the chain.
        self.fresh('cancel')
        stop = threading.Event()

        def cancelled_failure():
            stop.set()
            raise http_error(503)
        result = self.generate([cancelled_failure, reply()], settings=experiment(), cancelled=stop.is_set)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(len(self.sent), 1)

    def test_fallback_shares_the_original_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_failure():
            time.sleep(1)
            raise http_error(503)

        def stuck():
            release.wait(8)
            return reply()
        self.fresh('deadline')
        result = self.generate([slow_failure, stuck], settings=experiment(timeout_seconds=3))
        self.assertEqual(result.status, 'timeout')
        self.assertEqual(len(self.sent), 2)
        first, second = result.usage['attempts']
        self.assertEqual((first['status'], second['status']), ('provider_unavailable', 'timeout'))
        # The fallback only gets what is left of the 3 s (under 2 s); a fresh deadline would wait 3 s.
        self.assertGreaterEqual(first['latency_ms'], 1000)
        self.assertLess(second['latency_ms'], 2600)

    # Deadline, cancellation and streaming

    def test_cancel_before_send_reserves_nothing(self):
        result = self.generate(cancelled=lambda: True)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(self.sent, [])
        self.job.cancel()
        self.assertEqual(self.generate().status, 'cancelled')
        self.assertEqual(self.sent, [])
        self.assertEqual(self.attempts(), [])

    def test_cancel_after_reserving_releases_the_unsent_hold(self):
        checks = []

        def cancelled():  # false at the first check, true at the check just before dispatch
            checks.append(1)
            return len(checks) > 1
        budget = self.fresh('reserved-then-cancelled')
        result = self.generate(budget=budget, cancelled=cancelled)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(self.sent, [])
        self.assertEqual((result.usage['billing'], result.usage['reserved_usd']), ('not_sent', None))
        (row,) = [a for a in self.attempts() if a['job'] == budget.job.run_id]
        self.assertEqual(row['status'], 'released')

    def test_settlement_still_happens_when_the_evidence_write_fails(self):
        class Hold:
            def __init__(self):
                self.records, self.committed = 0, []

            def record(self, usage):
                self.records += 1
                if self.records > 1:
                    raise RuntimeError('ledger unavailable')

            def dispatch(self):
                pass

            def commit(self, actual):
                self.committed.append(actual)

            def release(self):
                raise AssertionError('a sent attempt must never be released')

        class Budget:
            cancelled = False

            def reserve_spend(self, amount):
                self.hold = Hold()
                return self.hold
        budget = Budget()
        with self.assertRaises(RuntimeError):
            self.generate(budget=budget)
        self.assertEqual(budget.hold.committed, [EXACT_COST])

    def test_timeout_and_cancellation_discard_the_late_reply(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                entered, release, stop = threading.Event(), threading.Event(), threading.Event()
                self.addCleanup(release.set)

                def slow():
                    entered.set()
                    if cancel:
                        stop.set()
                    release.wait(8)
                    return reply()
                budget = self.fresh(f'late-{cancel}')
                start = time.monotonic()
                result = self.generate([slow], settings=config(timeout_seconds=1), cancelled=stop.is_set)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - start, 5)  # the reply alone would take 8 s
                self.assertEqual(result.status, 'cancelled' if cancel else 'timeout')
                self.assertIsNone(result.value)
                self.assertEqual(result.usage['billing'], 'unknown')
                before = self.ledger.snapshot()['attempts']
                release.set()
                time.sleep(.05)
                self.assertEqual(self.ledger.snapshot()['attempts'], before)
                row = [a for a in before.values() if a['job'] == budget.job.run_id][0]
                self.assertEqual((row['status'], row['values'][3]), ('uncertain', str(PRIMARY_BOUND)))

    def test_streamed_http_body_cannot_outlive_the_deadline(self):
        class Trickle(httpx2.SyncByteStream):
            def __iter__(self):
                for _ in range(160):
                    time.sleep(.05)
                    yield b' '
                yield wire(reply())
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx2.Response(200, headers={'content-type': 'application/json'}, stream=Trickle())
        provider = OpenRouterProvider(config(timeout_seconds=1), api_key=KEY, transport=httpx2.MockTransport(handler))
        start = time.monotonic()
        result = provider.generate('answer', {}, Answer, budget=self.budget)
        self.assertLess(time.monotonic() - start, 5)  # the body alone takes 8 s
        self.assertEqual(result.status, 'timeout')
        self.assertIs(bodies[0]['stream'], False)
        self.assertEqual([a['status'] for a in self.attempts()], ['uncertain'])

    # Spend reservation and accounting

    def test_reservation_and_exact_accounting_are_durable(self):
        budget = Recording(self.budget)
        result = self.generate(budget=budget)
        self.assertEqual(budget.amounts, [PRIMARY_BOUND])
        u = result.usage
        self.assertEqual((u['billing'], Decimal(u['actual_usd']), Decimal(u['reserved_usd'])),
                         ('known', EXACT_COST, PRIMARY_BOUND))
        self.assertEqual(u['tokens'], dict(input_tokens=100, output_tokens=20, total_tokens=120))
        self.assertEqual(Decimal(u['attempts'][0]['rate_estimate_usd']), EXACT_COST)
        self.assertEqual(u['deviations'], [])
        (row,) = self.attempts()
        self.assertEqual((row['status'], row['values'][3]), ('completed', str(EXACT_COST)))
        record = row['provider_usage']
        for field, expected in (('provider', 'openrouter'), ('requested_model', 'openai/gpt-6.1-sol'),
                                ('actual_model', 'openai/gpt-6.1-sol'), ('requested_provider', 'openai'),
                                ('serving_provider', 'openai'), ('response_id', 'gen-fixture-1'),
                                ('system_fingerprint', 'fp_fixture'), ('endpoints_available', 1),
                                ('purpose', 'evaluation'), ('experiment', None), ('status', 'completed'),
                                ('endpoint', ENDPOINTS[0])):
            self.assertEqual(record[field], expected, field)
        self.assertEqual(record['tokens']['reasoning_tokens'], 5)
        self.assertIsInstance(record['latency_ms'], int)
        self.assertNotIn('synthetic buyer text', json.dumps(self.ledger.snapshot()))
        self.assertNotIn('Return an answer', json.dumps(self.ledger.snapshot()))

    def test_overrun_and_token_ceiling_are_denied_and_recorded(self):
        over_tokens = reply(cost=Decimal('0.0001'))
        over_tokens['usage'].update(completion_tokens=5000, total_tokens=5100)
        result = self.generate([over_tokens])
        self.assertEqual(result.status, 'budget_denied')
        self.assertFalse(self.ledger.snapshot()['blocked'])
        self.fresh('overrun')
        result = self.generate([reply(cost=Decimal('0.06'))])
        self.assertEqual(result.status, 'budget_denied')
        snapshot = self.ledger.snapshot()
        self.assertTrue(snapshot['blocked'])  # the campaign stops for operator reconciliation
        self.assertIn('0.06', [a['values'][3] for a in snapshot['attempts'].values()])

    def test_budget_denial_and_oversized_input_never_send(self):
        result = self.provider().generate('x' * 20000, {}, Answer, budget=self.budget)
        self.assertEqual(result.status, 'input_too_large')
        held = self.job.reserve('model', Charge(Decimal('0.72')), 30)
        result = self.generate()
        self.assertEqual(result.status, 'budget_denied')
        self.assertEqual(result.usage['billing'], 'not_sent')
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.attempts()), 1)  # only the test's own hold
        held.release()

    def test_unknown_billed_attempts_keep_the_full_reservation(self):
        def changed(**usage):
            r = reply()
            r['usage'].update(usage)
            return r

        def without(field):
            r = reply()
            if field == 'usage':
                del r['usage']
            else:
                del r['usage'][field]
            return r
        cases = {
            'missing cost': (without('cost'), 'unknown_usage'),
            'missing usage': (without('usage'), 'unknown_usage'),
            'float cost': (changed(cost=0.000379), 'unknown_usage'),
            'string cost': (changed(cost='0.000379'), 'unknown_usage'),
            'boolean cost': (changed(cost=True), 'unknown_usage'),
            'negative cost': (changed(cost=Decimal('-1')), 'unknown_usage'),
            'nonfinite cost': (changed(cost=Decimal('NaN')), 'unknown_usage'),
            'byok upstream bill': (changed(is_byok=True), 'unknown_usage'),
            'missing byok flag': (without('is_byok'), 'unknown_usage'),
            'inconsistent total': (changed(total_tokens=121), 'unknown_usage'),
            'boolean tokens': (changed(prompt_tokens=True), 'unknown_usage'),
            'detail exceeds total': (changed(prompt_tokens_details=dict(cached_tokens=200)), 'unknown_usage'),
            'error in a 200 body': ({'error': {'code': 502, 'message': 'upstream failed'}}, 'provider_unavailable'),
            'malformed error body': ({'error': 'failed'}, 'malformed_response'),
            'server error': (http_error(500), 'http_500'),
            'connection lost': (httpx2.ConnectError('lost'), 'transport_error'),
            'read timeout': (httpx2.ReadTimeout('slow'), 'timeout'),
            'invalid json': (ValueError('bad json'), 'malformed_response'),
            'not an object': ([], 'malformed_response'),
        }
        for name, (response, status) in cases.items():
            with self.subTest(name):
                budget = self.fresh(name)
                result = self.generate([response])
                self.assertEqual(result.status, status)
                self.assertIsNone(result.value)
                self.assertEqual((result.usage['billing'], result.usage['actual_usd']), ('unknown', None))
                self.assertEqual(Decimal(result.usage['reserved_usd']), PRIMARY_BOUND)
                row = [a for a in self.attempts() if a['job'] == budget.job.run_id][0]
                self.assertEqual((row['status'], row['values'][3]), ('uncertain', str(PRIMARY_BOUND)))

    def test_invalid_refused_or_incomplete_output_is_billed_but_not_returned(self):
        def edit(change):
            r = reply()
            change(r, r['choices'][0], r['choices'][0]['message'])
            return r
        cases = {
            'schema violation': (reply(text='{"unknown":42}'), 'invalid_output'),
            'not json': (reply(text='hello'), 'invalid_output'),
            'refusal': (edit(lambda r, c, m: m.update(refusal='no', content=None)), 'refused'),
            'content filter': (edit(lambda r, c, m: c.update(finish_reason='content_filter')), 'refused'),
            'length': (edit(lambda r, c, m: c.update(finish_reason='length')), 'incomplete'),
            'gateway error finish': (edit(lambda r, c, m: c.update(finish_reason='error')), 'incomplete'),
            'unrequested tool call': (edit(lambda r, c, m: m.update(tool_calls=[{'id': 't', 'type': 'function'}])),
                                      'invalid_output'),
            'tool call finish': (edit(lambda r, c, m: c.update(finish_reason='tool_calls')), 'invalid_output'),
            'two choices': (edit(lambda r, c, m: r['choices'].append(deepcopy(c))), 'invalid_output'),
            'no choices': (edit(lambda r, c, m: r.update(choices=[])), 'invalid_output'),
            'content parts': (edit(lambda r, c, m: m.update(content=[{'type': 'text', 'text': '{}'}])),
                              'invalid_output'),
            'wrong role': (edit(lambda r, c, m: m.update(role='user')), 'invalid_output'),
            'message not an object': (edit(lambda r, c, m: c.update(message='{"answer":"ok"}')), 'invalid_output'),
        }
        for name, (response, status) in cases.items():
            with self.subTest(name):
                self.fresh(name)
                result = self.generate([response])
                self.assertEqual(result.status, status)
                self.assertIsNone(result.value)
                self.assertEqual(result.usage['billing'], 'known')
        self.assertTrue(all(a['status'] == 'completed' for a in self.attempts()))

    # Metadata: recorded when available, labelled when not

    def test_route_metadata_is_recorded_or_labelled(self):
        bare = reply()
        del bare['openrouter_metadata'], bare['system_fingerprint']
        result = self.generate([bare])
        self.assertEqual(result.status, 'completed')
        u = result.usage
        self.assertIsNone(u['serving_provider'])
        self.assertIsNone(u['provider_verified'])
        self.assertEqual(u['metadata_unavailable'], ['endpoint_metadata', 'serving_provider', 'system_fingerprint'])
        top = reply()
        top['provider'] = 'OpenAI'
        self.assertTrue(self.generate([top]).usage['provider_verified'])
        other = self.generate([reply(provider='Azure')]).usage
        self.assertEqual((other['serving_provider'], other['provider_verified']), ('Azure', False))
        self.assertIn('serving_provider_unconfirmed', other['deviations'])
        dear = self.generate([reply(cost=Decimal('0.0004'))])
        self.assertEqual(dear.status, 'completed')
        self.assertIn('cost_above_configured_rates', dear.usage['deviations'])
        cheap = self.generate([reply(cost=Decimal('0.0001'))])  # under half the configured-rate estimate
        self.assertEqual((cheap.status, cheap.usage['deviations']), ('completed', ['cost_below_configured_rates']))
        self.assertEqual(self.generate([reply(cost=Decimal('0.0003'))]).usage['deviations'], [])
        long = reply(provider='p' * 500)
        long['openrouter_metadata']['attempts'].append({'provider': None, 'model': 7, 'status': None})
        attempt = self.generate([long]).usage['attempts'][0]
        self.assertEqual(len(attempt['serving_provider']), 100)
        self.assertEqual(attempt['gateway_attempts'][1], dict(provider=None, model=None, status=None))

    def test_main_evaluation_is_pinned_and_experiments_are_labelled(self):
        main = self.generate().usage
        self.assertEqual((main['purpose'], main['experiment']), ('evaluation', None))
        self.assertEqual(main['fallback'], dict(allowed=False, used=False, triggers=[], refused=None))
        self.assertEqual(main['routing'], dict(allow_fallbacks=False, require_parameters=True,
                                               data_collection='deny', zdr=False))
        self.assertEqual(main['route_fingerprint'], route_fingerprint(config()))
        changes = (dict(routes=[route('openai/gpt-6.1-mini')]), dict(routes=[route(provider='azure')]),
                   dict(routes=[route(output='11')]), dict(data_collection='allow'), dict(prompt_version='roles-v2'))
        prints = {route_fingerprint(config(**c)) for c in changes} | {route_fingerprint(config())}
        self.assertEqual(len(prints), len(changes) + 1)
        labelled = self.generate(settings=experiment()).usage
        self.assertEqual((labelled['purpose'], labelled['experiment']), ('experiment', 'exp-provider-comparison'))
        self.assertNotEqual(labelled['route_fingerprint'], main['route_fingerprint'])

    # Secrets and transport

    def test_key_never_appears_in_logs_results_or_ledger(self):
        replies = [httpx2.Response(401, content=f'{{"error":{{"code":401,"message":"bad key {KEY}"}}}}'.encode(),
                                   headers={'content-type': 'application/json'}),
                   httpx2.Response(200, content=wire({'error': {'code': 502, 'message': f'echo {KEY}'}}),
                                   headers={'content-type': 'application/json'}),
                   httpx2.Response(200, content=wire(reply()), headers={'content-type': 'application/json'})]
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers['authorization'], request.headers['x-openrouter-metadata']))
            return replies[len(seen) - 1]
        stream = io.StringIO()
        handler_log = logging.StreamHandler(stream)
        root = logging.getLogger()
        level = root.level
        root.addHandler(handler_log)
        root.setLevel(logging.DEBUG)
        try:
            provider = OpenRouterProvider(config(), api_key=KEY, transport=httpx2.MockTransport(handler))
            results = [provider.generate('answer', {}, Answer, budget=JobBudget(self.ledger.open_job(str(i))))
                       for i in range(3)]
        finally:
            root.removeHandler(handler_log)
            root.setLevel(level)
        self.assertEqual([r.status for r in results], ['access_denied', 'provider_unavailable', 'completed'])
        self.assertEqual(Decimal(results[2].usage['actual_usd']), EXACT_COST)
        self.assertEqual({url for url, _, _ in seen}, {ENDPOINTS[0]})
        self.assertEqual({auth for _, auth, _ in seen}, {f'Bearer {KEY}'})
        self.assertEqual({meta for _, _, meta in seen}, {'enabled'})
        surfaces = [stream.getvalue(), repr(provider), repr(vars(provider)), json.dumps(self.ledger.snapshot())]
        surfaces += [repr(r) + json.dumps(r.usage) for r in results]
        for surface in surfaces:
            self.assertNotIn(KEY, surface)
            self.assertNotIn('bad key', surface)
            self.assertNotIn('echo', surface)

    def test_http_status_codes_map_to_explicit_errors_without_retry(self):
        expected = {401: 'access_denied', 403: 'access_denied', 402: 'insufficient_credits', 404: 'unavailable',
                    408: 'timeout', 413: 'input_too_large', 429: 'rate_limited', 502: 'provider_unavailable',
                    503: 'provider_unavailable', 500: 'http_500'}
        for code, status in expected.items():
            with self.subTest(code=code):
                self.fresh(str(code))
                result = self.generate([http_error(code), reply()])
                self.assertEqual(result.status, status)
                self.assertEqual(len(self.sent), 1)
                self.assertEqual(result.usage['attempts'][0]['error_code'], code)
                self.assertNotIn('private provider text', repr(result))

    def test_post_json_extra_headers_cannot_replace_credentials(self):
        transport = httpx2.MockTransport(lambda request: httpx2.Response(
            200, content=b'{"cost": 0.000379, "x": 1}', headers={'content-type': 'application/json'}))
        for name in ('Authorization', 'accept-encoding'):
            with self.assertRaises(ValueError):
                post_json(ENDPOINTS[0], KEY, {}, transport=transport, headers={name: 'x'})
        body = post_json(ENDPOINTS[0], KEY, {}, transport=transport, headers={'X-Test': '1'}, parse_float=Decimal)
        self.assertEqual(body['cost'], Decimal('0.000379'))
        self.assertIsInstance(post_json(ENDPOINTS[0], KEY, {}, transport=transport)['cost'], float)

    # Roles keep the same provider interface

    def test_existing_roles_accept_the_adapter(self):
        choice = reply(text=json.dumps(dict(kind='act', action_id='A1', reason='settles gaps')))
        decision = OpenAIPlanner(self.provider([choice])).decide(state(), self.budget)
        self.assertEqual(decision.kind, 'act')
        self.assertEqual((decision.usage.model, decision.usage.input_tokens, decision.usage.priced),
                         ('openai/gpt-6.1-sol', 100, True))
        import asyncio
        from app import writer as writing
        from test_writer import context as writing_context, ids
        ctx = writing_context()
        menu = writing.build_menu(ctx)
        selection = {section: ids(menu, section) for section in writing.SECTIONS}
        # The writer needs its own prompt version and a smaller input ceiling (USD 0.25 final reserve).
        writer_settings = config(prompt_version=writing.WRITER_PROMPT_VERSION, max_input_tokens=32768)
        self.sent = []
        provider = self.provider([reply(text=json.dumps(selection))], writer_settings)
        writer = writing.OpenAIWriter.for_job(self.job, seconds=10, provider=provider)
        self.assertEqual(writer.config['provider'], 'openrouter')
        result = asyncio.run(writer.explain(ctx))
        self.assertEqual(result.status, 'completed')
        self.assertIn(result.source, ('model', 'mixed'))
        self.assertEqual((len(self.sent), result.spend, result.priced), (1, EXACT_COST, True))
        # The general role prompt version is not a writer prompt, so the writer keeps its template.
        self.sent = []
        fallback = asyncio.run(writing.OpenAIWriter.for_job(self.job, seconds=10, provider=self.provider()).explain(ctx))
        self.assertEqual((fallback.source, fallback.reason, self.sent), ('fallback', 'unknown_prompt', []))
        with self.assertRaises(ValueError):
            writing.OpenAIWriter.for_job(self.job, seconds=1, provider=self.provider())


if __name__ == '__main__':
    unittest.main()
