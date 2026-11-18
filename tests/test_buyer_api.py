from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app import contracts_v2 as v
from app.assessment import assess_full_product
from app.buyer_runs import BuyerRunService, RunError, session_hash
from app.main import create_app
from app.run_worker import execute_one
from app.storage import Storage, buyer_sessions, buyer_runs
import test_corpus as fixtures
from test_contracts_v2 import fixture


def process_claim(url):
    storage=Storage(url)
    try:
        claim=BuyerRunService(storage,max_running=1).claim()
        return claim.run.run_id if claim else None
    finally:
        storage.engine.dispose()


class BuyerApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.CorpusTests.setUp(self)
        self.now=datetime.now(timezone.utc)
        self.service=BuyerRunService(self.storage,clock=lambda:self.now)
        self.app=create_app(self.storage,buyer_service=self.service,cookie_secure=True)
        self.client=self.enterContext(TestClient(self.app,base_url='https://testserver'))
        response=self.client.get('/api/session')
        self.assertEqual(response.status_code,200)
        self.session_response=response
        self.headers={'Origin':'https://testserver','X-CSRF-Token':response.json()['csrf_token']}
        self.token=self.client.cookies.get('techscout_session')
        self.request=v.ShoppingRequest.model_validate(fixture('requests')['exact_model'])

    def start(self):
        response=self.client.post('/api/runs',json=self.request.model_dump(mode='json'),headers=self.headers)
        self.assertEqual(response.status_code,202,response.text)
        return v.RunSnapshot.model_validate(response.json())

    def command(self,snapshot,action,**fields):
        return self.client.post('/api/runs/'+snapshot.run.run_id+'/'+action,
            json={'run':snapshot.run.model_dump(mode='json'),**fields},headers=self.headers)

    def read(self,snapshot):
        response=self.client.get('/api/runs/'+snapshot.run.run_id)
        self.assertEqual(response.status_code,200,response.text)
        return v.ComparisonWorkspace.model_validate(response.json())

    def test_queued_creation_polling_and_session_cookie_security(self):
        snapshot=self.start()
        self.assertEqual(snapshot.status,'queued')
        self.assertEqual(self.read(snapshot).current,snapshot)
        cookie=self.session_response.headers['set-cookie']
        for flag in ('HttpOnly','Secure','SameSite=strict','Path=/'):
            self.assertIn(flag,cookie)
        self.assertEqual(self.client.get('/api/runs/'+snapshot.run.run_id).headers['cache-control'],'no-store')
        with self.storage.engine.connect() as db:
            row=db.execute(select(buyer_sessions)).mappings().one()
        self.assertEqual(row['session_hash'],session_hash(self.token))
        self.assertNotIn(self.token,json.dumps(dict(row),default=str))
        self.assertEqual(self.client.get('/api/session').json()['csrf_token'],self.headers['X-CSRF-Token'])

    def test_csrf_origin_and_other_session_ownership_on_every_action(self):
        payload=self.request.model_dump(mode='json')
        for headers in ({},{'Origin':'https://evil.example', 'X-CSRF-Token':self.headers['X-CSRF-Token']},
                        {'Origin':'https://testserver','X-CSRF-Token':'wrong'}):
            self.assertEqual(self.client.post('/api/runs',json=payload,headers=headers).status_code,403)
        snapshot=self.start()
        other=TestClient(self.app,base_url='https://testserver')
        self.addCleanup(other.close)
        info=other.get('/api/session').json()
        headers={'Origin':'https://testserver','X-CSRF-Token':info['csrf_token']}
        self.assertEqual(other.get('/api/runs/'+snapshot.run.run_id).status_code,404)
        for action,extra in [('cancel',{}),('clarify',{'clarification_id':'q','answer':'answer'}),
                             ('refine',{'request':self.request.refine({}).model_dump(mode='json')})]:
            response=other.post('/api/runs/'+snapshot.run.run_id+'/'+action,
                json={'run':snapshot.run.model_dump(mode='json'),**extra},headers=headers)
            self.assertEqual(response.status_code,404,response.text)

    def test_cancel_rejects_late_worker_and_is_idempotent(self):
        snapshot=self.start()
        claim=self.service.claim()
        cancelled=self.command(snapshot,'cancel')
        self.assertEqual(cancelled.json()['status'],'cancelled')
        with self.assertRaises(RunError):
            self.service.publish(claim,status='partial',stop_reason='late')
        self.assertEqual(self.command(snapshot,'cancel').json(),cancelled.json())
        self.assertEqual(self.read(snapshot).current.status,'cancelled')

    def test_provisional_evidence_keeps_its_time_and_cannot_be_a_recommendation(self):
        snapshot=self.start()
        claim=self.service.claim()
        offer=v.Offer.model_validate(fixture('offers')['phone_pickup'])
        observed=offer.evidence[0].observed_at
        candidate=v.ProvisionalCandidate(offer=offer,observed_at=observed)
        self.service.publish(claim,phase='research',provisional=[candidate])
        current=self.read(snapshot).current
        self.assertEqual(current.provisional[0].observed_at,observed)
        self.assertEqual(current.provisional[0].status,'provisional')
        self.assertEqual(current.recommendations,())
        with self.assertRaises(ValidationError):
            self.service.publish(claim,status='complete',stop_reason='unsupported',
                recommendations=[{'primary':{'offer':offer,'cost':{'status':'verified','total':offer.price.value},
                    'verification':{'checked_at':self.now,'run':claim.run}}}])
        self.assertEqual(self.read(snapshot).current.status,'running')

    def test_current_run_verification_can_publish_checked_result(self):
        snapshot=self.start()
        claim=self.service.claim()
        self.now+=timedelta(seconds=2)
        offer=deepcopy(fixture('offers')['phone_pickup'])
        for e in offer['evidence']:
            if e['field_path'] in {'price','stock'}:
                e.update(run=claim.run.model_dump(mode='json'),observed_at=self.now.isoformat(),origin='runtime')
        checked={'offer':offer,'cost':{'status':'verified','total':offer['price']['value']},
            'verification':{'checked_at':self.now,'run':claim.run}}
        wrong_model=deepcopy(checked)
        wrong_model['offer']['variant']['product']['model']='Scout Phone Pro'
        with self.assertRaisesRegex(ValueError,'hard requirements'):
            self.service.publish(claim,status='complete',phase='write',stop_reason='Wrong model',
                coverage={'discovered':1,'attempted':1,'retrieved':1,'qualifying':1},
                recommendations=[{'primary':wrong_model}])
        self.service.publish(claim,status='complete',phase='write',stop_reason='Verified one seller',
            coverage={'discovered':1,'attempted':1,'retrieved':1,'qualifying':1},recommendations=[{'primary':checked}])
        self.assertEqual(len(self.read(snapshot).current.recommendations),1)

    def test_worker_publication_checks_conditional_charges_against_the_buyer_request(self):
        request = self.request.model_dump(mode='json')
        request['fulfillment'] = {'mode': 'pickup', 'destination': {'city': 'Beirut'}}
        self.request = v.ShoppingRequest.model_validate(request)
        snapshot = self.start()
        claim = self.service.claim()
        self.now += timedelta(seconds=2)
        offer = deepcopy(fixture('offers')['phone_pickup'])
        for evidence in offer['evidence']:
            if evidence['field_path'] in {'price', 'stock'}:
                evidence.update(run=claim.run.model_dump(mode='json'),
                                observed_at=self.now.isoformat(), origin='runtime')
        offer['costs'][0].update(state='additional', amount={'amount':'30', 'currency':'USD'})
        checked = {'offer':offer, 'cost':{'status':'verified','total':{'amount':'900','currency':'USD'}},
                   'verification':{'checked_at':self.now,'run':claim.run}}

        def publish():
            self.service.publish(claim, status='complete', phase='write', stop_reason='Fixture result',
                coverage={'discovered':1,'attempted':1,'retrieved':1,'qualifying':1},
                recommendations=[{'primary':checked}])

        for condition in ('city:Beirut', 'city:Tripoli'):
            with self.subTest(condition=condition):
                offer['costs'][0]['conditions'] = [condition]
                with self.assertRaisesRegex(ValueError, 'domestic cost'):
                    publish()
                current = self.read(snapshot).current
                self.assertEqual(current.status, 'running')
                self.assertEqual(current.recommendations, ())

        offer['costs'][0]['conditions'] = ['city:Beirut']
        checked['cost']['total']['amount'] = '930'
        publish()
        self.assertEqual(str(self.read(snapshot).current.recommendations[0].primary.cost.total.amount), '930')

    def test_shipping_quote_must_match_the_route_and_still_be_valid(self):
        request = self.request.model_dump(mode='json')
        request['allowed_conditions'] = ['used']
        self.request = v.ShoppingRequest.model_validate(request)
        snapshot = self.start()
        claim = self.service.claim()
        self.now += timedelta(seconds=2)
        offer = deepcopy(fixture('offers')['phone_equivalent'])
        for evidence in offer['evidence']:
            if evidence['field_path'] in {'price', 'stock'}:
                evidence.update(run=claim.run.model_dump(mode='json'),
                                observed_at=self.now.isoformat(), origin='runtime')
        quote = next(e for e in offer['evidence'] if e['field_path']=='costs.order.shipping')
        quote['route_id'] = 'another-delivery-route'
        with self.assertRaisesRegex(ValidationError, 'Route evidence must match'):
            v.Offer.model_validate(offer)
        quote['route_id'] = offer['route']['route_id']

        shipping = next(charge for charge in offer['costs'] if charge['kind']=='shipping')
        shipping['valid_until'] = self.now.isoformat()
        assessment = assess_full_product(self.request, v.Offer.model_validate(offer), now=self.now)
        self.assertEqual(assessment.cost.status, 'incomplete')
        self.assertIn('costs.order.shipping', assessment.cost.missing)
        checked = {'offer':offer, 'cost':{'status':'verified','total':{'amount':'910','currency':'USD'}},
                   'verification':{'checked_at':self.now,'run':claim.run}}
        def publish():
            self.service.publish(claim, status='complete', phase='write', stop_reason='Fixture result',
                coverage={'discovered':1,'attempted':1,'retrieved':1,'qualifying':1},
                recommendations=[{'primary':checked}])

        with self.assertRaisesRegex(ValueError, 'Decisive cost quotes must be current'):
            publish()
        self.assertEqual(self.read(snapshot).current.recommendations, ())
        shipping['valid_until'] = (self.now + timedelta(seconds=1)).isoformat()
        publish()
        self.assertEqual(str(self.read(snapshot).current.recommendations[0].primary.cost.total.amount), '910')

    def test_clarification_preserves_active_time_and_rejects_old_revision(self):
        snapshot=self.start()
        claim=self.service.claim()
        self.now+=timedelta(seconds=10)
        waiting=self.service.publish(claim,status='clarification',clarification={
            'clarification_id':'q1','question':'Which branch?','material_reason':'Pickup stock differs'})
        self.now+=timedelta(hours=1)
        response=self.command(waiting,'clarify',clarification_id='q1',answer='Beirut branch')
        self.assertEqual(response.status_code,202,response.text)
        resumed=v.RunSnapshot.model_validate(response.json())
        self.assertEqual(resumed.run.run_revision,2)
        next_claim=self.service.claim()
        self.assertEqual(next_claim.remaining_ms,110000)
        self.assertEqual(next_claim.answers[0]['answer'],'Beirut branch')
        self.assertEqual(self.command(waiting,'cancel').status_code,409)
        with self.assertRaises(RunError): self.service.publish(claim,phase='verify')
        self.now+=timedelta(seconds=111)
        self.assertEqual(self.read(resumed).current.status,'partial')

    def test_refinement_new_request_revision_keeps_old_snapshot_separate(self):
        snapshot=self.start()
        claim=self.service.claim()
        offer=v.Offer.model_validate(fixture('offers')['phone_pickup'])
        self.service.publish(claim,provisional=[v.ProvisionalCandidate(offer=offer,observed_at=offer.evidence[0].observed_at)])
        refined=self.request.refine({'budget':{'maximum':{'amount':'800','currency':'USD'}}})
        response=self.command(snapshot,'refine',request=refined.model_dump(mode='json'))
        self.assertEqual(response.status_code,202,response.text)
        workspace=v.ComparisonWorkspace.model_validate(response.json())
        self.assertEqual(workspace.previous.status,'cancelled')
        self.assertEqual(len(workspace.previous.provisional),1)
        self.assertEqual(workspace.current.provisional,())
        self.assertEqual(workspace.current.request.revision,2)
        self.assertEqual(self.read(snapshot),workspace)
        with self.assertRaises(RunError): self.service.publish(claim,phase='verify')
        self.assertEqual(self.command(snapshot,'refine',request=refined.model_dump(mode='json')).status_code,409)

    def test_session_expiry_denies_read_write_and_cascades_private_rows(self):
        snapshot=self.start()
        claim=self.service.claim()
        self.now+=timedelta(hours=25)
        self.assertEqual(self.client.get('/api/runs/'+snapshot.run.run_id).status_code,401)
        self.assertEqual(self.command(snapshot,'cancel').status_code,401)
        with self.assertRaises(RunError): self.service.publish(claim,phase='write')
        self.service.recover()
        with self.storage.engine.connect() as db:
            self.assertEqual(db.execute(select(buyer_runs)).all(),[])

    def test_recovery_preserves_live_lease_then_closes_interruption(self):
        snapshot=self.start()
        claim=self.service.claim()
        other=BuyerRunService(self.storage,clock=lambda:self.now)
        other.recover()
        self.assertEqual(self.read(snapshot).current.status,'running')
        self.now+=timedelta(seconds=16)
        other.recover()
        self.assertEqual(self.read(snapshot).current.status,'partial')
        self.assertIsNone(other.claim())
        with self.assertRaises(RunError): other.publish(claim,phase='research')

    def test_global_per_session_queue_limits_and_separate_process_claims(self):
        first=self.start()
        self.assertEqual(self.client.post('/api/runs',json=self.request.model_copy(update={'request_id':'other'}).model_dump(mode='json'),
            headers=self.headers).status_code,409)
        # Multiple processes contend for one global execution slot.
        for n in range(3):
            token,_,_=self.service.session()
            self.service.start(token,self.request.model_copy(update={'request_id':str(n)}))
        with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
            claims=list(pool.map(process_claim,[self.url]*2))
        self.assertEqual(sum(c is not None for c in claims),1)
        bounded=BuyerRunService(self.storage,clock=lambda:self.now,max_pending=4)
        token,_,_=bounded.session()
        with self.assertRaises(RunError) as caught: bounded.start(token,self.request)
        self.assertEqual(caught.exception.status,429)

    def test_worker_publishes_without_http_wait_and_cancelled_late_callback_is_rejected(self):
        snapshot=self.start()
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        late=[]
        def controller(context):
            entered.set()
            release.wait(3)
            try: context.publish(status='partial',stop_reason='late callback')
            except RunError: late.append('rejected')
            finally: finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(execute_one,self.service,controller)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.read(snapshot).current.status,'running')
                self.command(snapshot,'cancel')
                self.assertTrue(future.result(timeout=2))
            finally: release.set()
            self.assertTrue(finished.wait(2))
        self.assertEqual(late,['rejected'])
        self.assertEqual(self.read(snapshot).current.status,'cancelled')

    def test_payload_limits_and_spa_never_masks_api_or_missing_assets(self):
        response=self.client.post('/api/runs',content=b'x'*65537,headers=self.headers)
        self.assertEqual(response.status_code,413)
        directory=self.storage.snapshots.root.parent/'frontend-dist'
        directory.mkdir()
        (directory/'assets').mkdir()
        (directory/'index.html').write_text('<html>fixture SPA</html>')
        with patch('app.main.FRONTEND_DIST',directory):
            app=create_app(self.storage,buyer_service=self.service)
        with TestClient(app,base_url='https://testserver') as client:
            for path in ('/api/missing','/api/runs','/assets/missing.js','/evidence/secret','/missing.js'):
                response=client.get(path,headers={'accept':'text/html'})
                self.assertEqual(response.status_code,404,response.text)
                self.assertNotIn('fixture SPA',response.text)
            self.assertIn('fixture SPA',client.get('/comparison/abc',headers={'accept':'text/html'}).text)
        schema=app.openapi()
        self.assertIn('/api/runs/{run_id}/clarify',schema['paths'])
        self.assertIn('ComparisonWorkspace',schema['components']['schemas'])

    def test_generated_react_contract_matches_current_api(self):
        from app.api_types import OUTPUT,schema_header
        self.assertEqual(OUTPUT.read_text(encoding='utf-8').splitlines()[0],schema_header())

    def test_stalled_controller_is_closed_at_active_limit_without_late_write(self):
        self.service.limit_ms=50
        snapshot=self.start()
        release,finished=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        late=[]
        def controller(context):
            release.wait(2)
            try:
                context.publish(status='partial',stop_reason='late')
            except RunError:
                late.append('rejected')
            finally: finished.set()
        execute_one(self.service,controller)
        self.assertEqual(self.read(snapshot).current.status,'partial')
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(late,['rejected'])
