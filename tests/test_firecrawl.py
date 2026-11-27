from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4
import json
import unittest
from unittest.mock import Mock

import httpx2

from app.accounting import Ledger, BudgetDenied
from app.firecrawl import Firecrawl, retrieve_page
from app.sources import SourceGate, FetchOutcome
from app.contracts import ToolError
import test_corpus as fixtures
from test_corpus import SOURCE, URL


class FirecrawlTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.registry = self.storage.snapshots.root.parent/'sources.json'
        self.config = {'sources':[self.storage.load_source(SOURCE).model_dump(mode='json')],
                       'live_release':{'admitted_source_ids':[SOURCE]}}
        self.registry.write_text(json.dumps(self.config),encoding='utf-8')
        self.gate = SourceGate._for_tests(self.registry,resolver=lambda *_:['8.8.8.8'])
        self.ledger = Ledger(self.storage,'firecrawl-fixture',fixture=True)
        self.ledger.provision()
        self.job = self.ledger.open_job('run')
        self.calls = []

    def response(self):
        return {'success':True,'data':{'rawHtml':'<html>public fixture</html>',
            'metadata':{'sourceURL':URL,'url':URL,'statusCode':200,'contentType':'text/html',
                        'provider':{'creditsCost':1},'scrapeId':'synthetic'}}}

    def provider(self, raw=None, *, status=200):
        def handler(req):
            self.calls.append((str(req.url),json.loads(req.content)))
            return httpx2.Response(status,json=self.response() if raw is None else raw)
        return Firecrawl('synthetic-key',self.gate,usd_per_credit=Decimal('.01'),
                         transport=httpx2.MockTransport(handler))

    def test_fixed_single_page_payload_and_durable_source_charge(self):
        page = self.provider().scrape(self.job,SOURCE,URL)
        endpoint,payload = self.calls[0]
        self.assertEqual(endpoint,'https://api.firecrawl.dev/v2/scrape')
        self.assertEqual(payload['maxAge'],0)
        self.assertFalse(payload['storeInCache'])
        self.assertFalse(payload['skipTlsVerification'])
        self.assertEqual(payload['proxy'],'basic')
        self.assertEqual(payload['formats'],['rawHtml'])
        self.assertEqual(payload['parsers'],[])
        self.assertNotIn('actions',payload)
        self.assertEqual(page.outcome.body,b'<html>public fixture</html>')
        used = self.ledger.snapshot()['scopes']['project']
        self.assertEqual(used[0:2],[1,1])
        self.assertEqual(used[3], '0.01')

    def test_target_scope_and_private_dns_refused_before_provider(self):
        provider = self.provider()
        with self.assertRaises(ValueError):
            provider.scrape(self.job,SOURCE,'https://evil.example/p')
        self.gate = SourceGate._for_tests(self.registry,resolver=lambda *_:['127.0.0.1'])
        with self.assertRaises(ValueError):
            self.provider().scrape(self.job,SOURCE,URL)
        self.assertEqual(self.calls,[])

    def test_missing_redirected_cached_failed_or_wrong_type_provenance_rejected(self):
        changes = [ {'sourceURL':'https://evil.example/p'}, {'url':'https://store.example/other'},
                    {'sourceURL':None}, {'statusCode':403}, {'contentType':'application/json'},
                    {'cacheState':'hit'}, {'cachedAt':'2026-11-15T12:00:00Z'} ]
        for i,change in enumerate(changes):
            with self.subTest(change=change):
                raw = self.response()
                raw['data']['metadata'].update(change)
                job = self.ledger.open_job(f'bad-{i}')
                # Independent source windows, preserving campaign money.
                with self.ledger._transaction() as (state,_):
                    for a in state['attempts'].values():
                        a['created'] -= 61
                with self.assertRaises(ValueError):
                    self.provider(raw).scrape(job,SOURCE,URL)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3],'0.07')

    def test_no_unknown_usage_refund_and_provider_failures_count(self):
        raw = self.response()
        del raw['data']['metadata']['provider']
        self.provider(raw).scrape(self.job,SOURCE,URL)
        with self.assertRaises(httpx2.HTTPStatusError):
            self.provider(status=503).scrape(self.job,SOURCE,URL)
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3],'0.02')
        self.assertEqual({a['status'] for a in self.ledger.snapshot()['attempts'].values()},{'uncertain'})

    def test_unknown_rate_and_overbilling_fail_closed(self):
        with self.assertRaises(ValueError):
            Firecrawl('synthetic',self.gate,usd_per_credit=None)
        raw = self.response()
        raw['data']['metadata']['provider']['creditsCost']=5
        with self.assertRaises(BudgetDenied):
            self.provider(raw).scrape(self.job,SOURCE,URL)
        self.assertTrue(self.ledger.snapshot()['blocked'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][3],'0.05')

    def test_included_scrape_uses_a_call_and_preserves_missing_or_invalid_usage(self):
        env = {'FIRECRAWL_API_KEY': 'synthetic', 'FIRECRAWL_SCRAPE_USD_PER_CREDIT': 'included'}
        provider = Firecrawl.from_env(self.gate, env)
        for usage in (1, None, 2):
            raw = self.response()
            raw['data']['metadata']['provider']['creditsCost'] = usage
            provider.transport = self.provider(raw).transport
            if usage == 2:
                with self.assertRaisesRegex(ValueError, 'provider usage'):
                    provider.scrape(self.job, SOURCE, URL)
            else:
                self.assertEqual(provider.scrape(self.job, SOURCE, URL).outcome.status, 'ok')
        snapshot = self.ledger.snapshot()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(snapshot['scopes']['project'][4], 3)
        self.assertEqual(Decimal(snapshot['scopes']['project'][3]), Decimal(0))
        self.assertCountEqual([a['status'] for a in snapshot['attempts'].values()],
                              ['completed', 'uncertain', 'uncertain'])

    def test_missing_large_or_warning_content_refused(self):
        for change in ({'rawHtml':None},{'rawHtml':'x'*2_097_153},{'warning':'partial output'}):
            raw = self.response()
            raw['data'].update(change)
            with self.assertRaises(ValueError):
                self.provider(raw).scrape(self.job,SOURCE,URL)

    def test_agent_recovers_only_its_current_interrupted_scrape_and_reads_large_html(self):
        job_id = str(uuid4())
        html = 'x' * 2_650_000
        raw = self.response()
        raw['data'].update(rawHtml=html)
        raw['data']['metadata']['scrapeId'] = job_id
        def send(request):
            self.calls.append((request.method, request.url.path))
            if request.method == 'POST':
                self.submitted = datetime.now(timezone.utc)
                raise httpx2.ReadTimeout('interrupted before delivery')
            if request.url.path.endswith('/activity'):
                rows = [dict(endpoint='scrape', target=URL, id=key, created_at=stamp.isoformat())
                        for key, stamp in [(str(uuid4()), self.submitted-timedelta(hours=1)), (job_id, self.submitted)]]
                return httpx2.Response(200, json=dict(success=True, data=rows))
            self.assertTrue(request.url.path.endswith(job_id))
            return httpx2.Response(200, json=raw)

        provider = Firecrawl('key', self.gate, usd_per_credit='included', transport=httpx2.MockTransport(send))
        page = provider.scrape(self.job, SOURCE, URL, agent_read=True)
        self.assertEqual(page.outcome.body, html.encode())
        self.assertEqual(page.request_id, job_id)
        self.assertEqual([m for m, _ in self.calls], ['POST', 'GET', 'GET'])
        self.assertEqual(self.ledger.snapshot()['scopes']['project'][4], 3)

    def test_agent_declines_ambiguous_archive_jobs_without_reading_either_one(self):
        def send(request):
            if request.method == 'POST':
                self.submitted = datetime.now(timezone.utc).isoformat()
                raise TimeoutError('interrupted')
            self.assertTrue(request.url.path.endswith('/activity'))
            rows = [dict(id=str(uuid4()), target=URL, endpoint='scrape', created_at=self.submitted) for _ in range(2)]
            return httpx2.Response(200, json=dict(success=True, data=rows))
        provider = Firecrawl('key', self.gate, usd_per_credit='included', transport=httpx2.MockTransport(send))
        with self.assertRaisesRegex(TimeoutError, 'one result'):
            provider.scrape(self.job, SOURCE, URL, agent_read=True)

    def test_provider_cannot_override_revocation_during_request(self):
        def revoke(req):
            self.config['live_release']['admitted_source_ids']=[]
            self.registry.write_text(json.dumps(self.config),encoding='utf-8')
            return httpx2.Response(200,json=self.response())
        provider = Firecrawl('synthetic',self.gate,usd_per_credit=Decimal('.01'),transport=httpx2.MockTransport(revoke))
        with self.assertRaises(ValueError):
            provider.scrape(self.job,SOURCE,URL)

    def test_cancellation_does_not_send_provider_request(self):
        self.job.cancel()
        with self.assertRaises(BudgetDenied):
            self.provider().scrape(self.job,SOURCE,URL)
        self.assertEqual(self.calls,[])

    def test_fallback_precedence_and_refusal_guards(self):
        direct = FetchOutcome(SOURCE,URL,(URL,),1,1,body=b'listing',status_code=200)
        fallback = Mock()
        gate = Mock(_registry_path=self.registry)
        gate.fetch.return_value=direct
        self.assertIs(retrieve_page(gate,self.job,SOURCE,URL,fallback=fallback),direct)
        for code,message in (('BLOCKED','blocked:redirect'),('BUDGET_EXHAUSTED','budget'),
                             ('RATE_LIMIT','rate'),('HTTP_ERROR','http:404 not found')):
            failed = FetchOutcome(SOURCE,URL,(URL,),1,1,errors=(ToolError(code=code,message=message,retryable=False),))
            gate.fetch.return_value=failed
            self.assertIs(retrieve_page(gate,self.job,SOURCE,URL,fallback=fallback),failed)
        fallback.scrape.assert_not_called()
        failed = FetchOutcome(SOURCE,URL,(URL,),1,1,errors=(ToolError(code='HTTP_ERROR',message='http:403 unavailable',retryable=False),))
        gate.fetch.return_value=failed
        fallback.scrape.return_value.outcome=direct
        self.assertIs(retrieve_page(gate,self.job,SOURCE,URL,fallback=fallback),direct)
        fallback.scrape.assert_called_once()
