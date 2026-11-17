from copy import deepcopy
from decimal import Decimal
import json
import threading
import time
import unittest
from unittest.mock import patch

import httpx2
from app.accounting import Ledger
from app.contracts import Model
from app.model_provider import OpenAIProvider, OpenAIPlanner, JobBudget, load_provider_config
import test_corpus as fixtures
from test_planner import state


class Answer(Model):
    answer: str


def reply():
    return dict(id='resp-fixture', model='gpt-6.1-sol', service_tier='default', status='completed',
        usage=dict(input_tokens=100, output_tokens=20, total_tokens=120,
            input_tokens_details=dict(cached_tokens=20,cache_write_tokens=30,
                cache_write_tokens_details=dict(ttl_30m_tokens=20,ttl_12h_tokens=10))),
        output=[dict(type='message', role='assistant', content=[dict(type='output_text',text='{"answer":"ok"}')])])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.ledger = Ledger(self.storage,'model-fixture',fixture=True)
        self.ledger.provision()
        self.job = self.ledger.open_job('buyer')
        self.budget = JobBudget(self.job)
        self.config = load_provider_config()

    def generate(self, response=None, **kwargs):
        provider = OpenAIProvider(self.config,client=lambda _: response if response is not None else reply())
        return provider.generate('Return an answer.', {'question':'synthetic'}, Answer, budget=self.budget, **kwargs)

    def test_structured_request_and_exact_durable_accounting(self):
        sent = []
        def client(payload):
            sent.append(payload)
            return reply()
        result = OpenAIProvider(self.config,client=client).generate('answer', {}, Answer,budget=self.budget)
        self.assertEqual(result.value.answer,'ok')
        self.assertEqual(result.usage['actual_usd'],'0.000402')
        p = sent[0]
        self.assertEqual(p['model'],'gpt-6.1-sol')
        self.assertFalse(p['store'])
        self.assertNotIn('tools',p)
        self.assertTrue(p['text']['format']['strict'])
        attempts = list(self.ledger.snapshot()['attempts'].values())
        self.assertEqual(len(attempts),1)
        self.assertEqual(attempts[0]['status'],'completed')
        self.assertEqual(attempts[0]['provider_usage']['actual_usd'],'0.000402')
        self.assertNotIn('question',json.dumps(attempts))

    def test_unknown_counts_and_model_keep_full_bound(self):
        for change in ('model','cache','tokens','tier','tool'):
            with self.subTest(change=change):
                self.budget = JobBudget(self.ledger.open_job(change))
                r = reply()
                if change=='model': r['model']='unpriced-snapshot'
                if change=='cache': del r['usage']['input_tokens_details']['cache_write_tokens']
                if change=='tokens': r['usage']['input_tokens']=True
                if change=='tier': r['service_tier']='priority'
                if change=='tool': r['tool_usage']={'web_search':{'num_requests':1}}
                result = self.generate(r)
                self.assertEqual(result.status,'unknown_usage')
                self.assertEqual(result.usage['billing'],'unknown')
        self.assertTrue(all(a['status']=='uncertain' for a in self.ledger.snapshot()['attempts'].values()))

    def test_invalid_refused_and_incomplete_output_still_billed(self):
        for kind in ('schema','refusal','incomplete','extra'):
            r = reply()
            if kind=='schema': r['output'][0]['content'][0]['text']='{"unknown":42}'
            if kind=='refusal': r['output'][0]['content']=[{'type':'refusal','refusal':'no'}]
            if kind=='incomplete': r['status']='incomplete'
            if kind=='extra': r['output'].append({'type':'function_call'})
            result = self.generate(r)
            self.assertNotEqual(result.status,'completed')
            self.assertEqual(result.usage['billing'],'known')

    def test_cancel_before_send_spends_nothing(self):
        result = self.generate(cancelled=lambda:True)
        self.assertEqual(result.status,'cancelled')
        self.assertEqual(self.ledger.snapshot()['attempts'],{})

    def test_cancel_and_timeout_discard_late_result(self):
        for cancel in (False,True):
            entered, release, stop = threading.Event(), threading.Event(), threading.Event()
            self.addCleanup(release.set)
            config = self.config.model_copy(update={'timeout_seconds':.12})
            def client(_):
                entered.set()
                if cancel: stop.set()
                release.wait(2)
                return reply()
            start = time.monotonic()
            result = OpenAIProvider(config,client=client).generate('answer',{},Answer,
                budget=JobBudget(self.ledger.open_job(str(cancel))),cancelled=stop.is_set)
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(result.status,'cancelled' if cancel else 'timeout')
            before = self.ledger.snapshot()['attempts']
            release.set()
            time.sleep(.02)
            self.assertEqual(self.ledger.snapshot()['attempts'],before)
            self.assertEqual(result.usage['billing'],'unknown')

    def test_access_error_never_falls_back_or_retries(self):
        calls = []
        def client(payload):
            calls.append(payload)
            req = httpx2.Request('POST','https://api.openai.com/v1/responses')
            res = httpx2.Response(403,request=req)
            raise httpx2.HTTPStatusError('private response',request=req,response=res)
        result = OpenAIProvider(self.config,client=client).generate('answer',{},Answer,budget=self.budget)
        self.assertEqual(result.status,'access_denied')
        self.assertEqual(len(calls),1)
        self.assertNotIn('private',str(result))
        with patch.dict('os.environ',{},clear=True), self.assertRaisesRegex(ValueError,'OPENAI_API_KEY'):
            OpenAIProvider(self.config)

    def test_large_input_and_budget_denial_do_not_call(self):
        calls=[]
        provider=OpenAIProvider(self.config,client=lambda p:calls.append(p))
        result=provider.generate('x'*100000,{},Answer,budget=self.budget)
        self.assertEqual(result.status,'input_too_large')
        from app.accounting import Charge
        held=self.job.reserve('model',Charge(Decimal('.6')),30)
        self.assertEqual(provider.generate('ok',{},Answer,budget=self.budget).status,'budget_denied')
        held.release()
        self.job.cancel()
        self.assertEqual(provider.generate('ok',{},Answer,budget=self.budget).status,'cancelled')
        self.assertEqual(calls,[])

    def test_legacy_action_menu_bridge_rejects_invented_action(self):
        for action in ('A1','invented'):
            r = reply()
            r['output'][0]['content'][0]['text']=json.dumps(dict(kind='act',action_id=action,reason='settles gaps'))
            planner=OpenAIPlanner(OpenAIProvider(self.config,client=lambda _:r))
            decision=planner.decide(state(),self.budget)
            self.assertEqual(decision.kind,'act' if action=='A1' else 'invalid')
            self.assertTrue(decision.usage.priced)

    def test_default_model_cli_is_openai_and_baselines_unchanged(self):
        from app.runs import _planner
        with patch.dict('os.environ',{'OPENAI_API_KEY':'synthetic-key'},clear=True):
            self.assertIsInstance(_planner('model'),OpenAIPlanner)
        self.assertEqual(_planner('rule').name,'rule-planner')
        self.assertEqual(_planner('none').name,'no-investigation')

    def test_http_transport_and_existing_controller_budget(self):
        from app.runs import AccountedRunBudget, RunCaps, _SpendView
        seen=[]
        def handler(req):
            seen.append(str(req.url))
            self.assertEqual(req.headers['authorization'],'Bearer synthetic-key')
            r=reply()
            r['output'][0]['content'][0]['text']='{"kind":"act","action_id":"A1","reason":"settles gaps"}'
            return httpx2.Response(200,json=r)
        budget=AccountedRunBudget('buyer',RunCaps(),job=self.job,registry_path='config/sources.json',model_seconds=30)
        provider=OpenAIProvider(self.config,api_key='synthetic-key',transport=httpx2.MockTransport(handler))
        decision=OpenAIPlanner(provider).decide(state(),_SpendView(budget))
        self.assertEqual(decision.kind,'act')
        self.assertEqual(seen,['https://api.openai.com/v1/responses'])
        self.assertEqual(len(self.ledger.snapshot()['attempts']),1)
        self.assertNotIn('synthetic-key',json.dumps(self.ledger.snapshot()))

    def test_provider_overcharge_is_recorded_and_blocks_campaign(self):
        r=reply()
        r['usage'].update(output_tokens=100000,total_tokens=100100)
        result=self.generate(r)
        self.assertEqual(result.status,'budget_denied')
        snapshot=self.ledger.snapshot()
        self.assertTrue(snapshot['blocked'])
        attempt=next(iter(snapshot['attempts'].values()))
        self.assertEqual(attempt['values'][3],result.usage['actual_usd'])
