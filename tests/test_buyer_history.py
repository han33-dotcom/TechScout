from datetime import timedelta
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func

from app.buyer_history import export_comparison, plain
from app.buyer_runs import RunError
from app.contracts_v2 import ComparisonWorkspace
from app.storage import buyer_runs, buyer_sessions, corpus_documents
import test_buyer_api as fixtures
import test_corpus as corpus_fixtures
from test_contracts_v2 import fixture


class BuyerHistoryTests(unittest.TestCase):
    setUp=fixtures.BuyerApiTests.setUp
    start=fixtures.BuyerApiTests.start
    command=fixtures.BuyerApiTests.command

    def test_recent_order_and_limit_across_independent_requests(self):
        runs=[]
        self.now=self.now.replace(microsecond=0)
        for index in range(3):
            self.request=self.request.model_copy(update={'request_id':f'history-{index}'})
            run=self.start();self.service.cancel(self.token,run.run);runs.append(run)
            self.now+=timedelta(microseconds=100000)
        items=self.client.get('/api/history?limit=2').json()['items']
        self.assertEqual([item['run_id'] for item in items],[run.run.run_id for run in reversed(runs[1:])])

    def test_deleted_history_cannot_be_recreated_by_late_worker_and_keeps_public_corpus(self):
        capture=corpus_fixtures.CorpusTests.capture(self)
        self.corpus.ingest(capture,corpus_fixtures.extract)
        saved=self.start();claim=self.service.claim()
        self.service.cancel(self.token,saved.run)
        self.service.delete_history(self.token,saved.run.run_id)
        with self.assertRaises(RunError) as error:self.service.publish(claim,status='complete')
        self.assertEqual(error.exception.status,404)
        with self.storage.engine.connect() as db:
            self.assertEqual(db.execute(select(func.count()).select_from(corpus_documents)).scalar_one(),1)

    def test_history_lists_latest_revision_only_and_reopens_exact_history(self):
        first=self.start()
        self.now+=timedelta(seconds=1)
        second=self.service.refine(self.token,first.run,self.request.refine({})).current
        response=self.client.get('/api/history')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([i['run_id'] for i in response.json()['items']],[second.run.run_id])
        saved=self.client.get('/api/history/'+first.run.run_id)
        self.assertTrue(saved.json()['historical'])
        self.assertEqual(saved.json()['workspace']['current']['run']['run_id'],first.run.run_id)
        self.assertEqual(saved.headers['cache-control'],'no-store')
        for limit in (0,21):self.assertEqual(self.client.get(f'/api/history?limit={limit}').status_code,422)

    def test_other_session_cannot_list_reopen_export_or_delete(self):
        saved=self.start()
        other=self.enterContext(TestClient(self.app,base_url='https://testserver'))
        csrf=other.get('/api/session').json()['csrf_token']
        self.assertEqual(other.get('/api/history').json()['items'],[])
        url='/api/history/'+saved.run.run_id
        for path in (url,url+'/export'):
            self.assertEqual(other.get(path).status_code,404)
        self.assertEqual(other.post(url+'/delete',json={},headers={'Origin':'https://testserver','X-CSRF-Token':csrf}).status_code,404)
        self.assertEqual(self.client.post(url+'/delete',json={}).status_code,403)

    def test_delete_refuses_active_chain_then_deletes_every_revision(self):
        first=self.start()
        second=self.service.refine(self.token,first.run,self.request.refine({})).current
        url='/api/history/'+first.run.run_id+'/delete'
        self.assertEqual(self.client.post(url,json={},headers=self.headers).status_code,409)
        self.service.cancel(self.token,second.run)
        self.assertEqual(self.client.post(url,json={},headers=self.headers).json(),{'deleted':True})
        for saved in (first,second):
            self.assertEqual(self.client.get('/api/runs/'+saved.run.run_id).status_code,404)
            self.assertEqual(self.client.get('/api/history/'+saved.run.run_id+'/export').status_code,404)
        self.assertEqual(self.client.get('/api/history').json()['items'],[])
        with self.storage.engine.connect() as db:
            self.assertEqual(db.execute(select(func.count()).select_from(corpus_documents)).scalar_one(),0)

    def test_expired_sessions_lose_access_and_cleanup_removes_private_rows(self):
        saved=self.start()
        self.now+=timedelta(hours=25)
        for path in ('/api/history','/api/history/'+saved.run.run_id,'/api/history/'+saved.run.run_id+'/export'):
            self.assertEqual(self.client.get(path).status_code,401)
        self.service.recover()
        with self.storage.engine.connect() as db:
            self.assertEqual(db.execute(select(func.count()).select_from(buyer_sessions)).scalar_one(),0)
            self.assertEqual(db.execute(select(func.count()).select_from(buyer_runs)).scalar_one(),0)

    def test_export_is_plain_allowlisted_historical_content_without_private_fields(self):
        saved=self.start()
        raw=fixture('workspace')
        snapshot=raw['current']
        snapshot['request']['refinement_note']='PRIVATE-PREFERENCE-SECRET'
        snapshot['recommendations'][0]['primary']['offer']['seller']['name']='<script>EVIL()</script><b>Example seller</b>'
        snapshot['recommendations'][0]['primary']['offer']['listing_url']='https://shop.example/item?api_key=SECRET#token'
        # Store a valid historical fixture as an owned test row; no live result is claimed.
        snapshot=ComparisonWorkspace.model_validate(raw).current
        with self.storage.transaction() as db:
            db.execute(update(buyer_runs).where(buyer_runs.c.run_id==saved.run.run_id).values(
                snapshot=snapshot.model_dump(mode='json'),status=snapshot.status))
        url='/api/history/'+saved.run.run_id+'/export'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.headers['content-type'].startswith('text/plain'))
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertIn('attachment;',response.headers['content-disposition'])
        for secret in ('PRIVATE-PREFERENCE-SECRET',self.token,self.headers['X-CSRF-Token'],
                       snapshot.request.request_id,snapshot.run.run_id,'api_key','SECRET','<script>','EVIL()','<b>'):
            self.assertNotIn(secret,response.text)
        self.assertIn('HISTORICAL',response.text)
        self.assertIn('Example seller',response.text)
        self.assertIn('known subtotal',response.text)
        self.assertIn('Evidence (original observations',response.text)
        self.assertNotIn('Evidence (original observations',self.client.get(url+'?include_evidence=false').text)

    def test_export_keeps_unknown_costs_unknown_and_strips_control_markup(self):
        raw=fixture('workspace');raw['current']['uncertain'][0]['cost']=None
        text=export_comparison(ComparisonWorkspace.model_validate(raw).current)
        self.assertIn('Total: Unknown',text)
        self.assertNotIn('<',plain('&lt;img src=x&gt;\n\u202e'))
