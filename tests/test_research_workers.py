from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event
import time
import unittest
from unittest.mock import Mock

from app.accounting import Ledger, Charge
from app.contracts_v2 import ShoppingRequest
from app.model_provider import ModelResult
from app.page_extraction import PageResult, read_page
from app.extraction import PageProfile
from app.research_workers import ResearchWorkers, Resource, Work, Choice
from app.sources import FetchOutcome
from test_corpus import SOURCE, URL, extract
import test_corpus as corpus_fixtures
import test_directory as directory_fixtures


class ResearchWorkerTests(unittest.TestCase):
    capture = corpus_fixtures.CorpusTests.capture
    record = directory_fixtures.DirectoryTests.record

    def setUp(self):
        directory_fixtures.DirectoryTests.setUp(self)
        self.directory.upsert(self.record(source_country='LB'))
        # A one-second fake source lease must last a full second. SQLite's
        # CURRENT_TIMESTAMP truncates subsecond times at the next second boundary.
        self.ledger = Ledger(self.storage,'research-fixture',fixture=True,clock=lambda:datetime.now(timezone.utc))
        self.ledger.provision()
        self.job = self.ledger.open_job('research')
        self.gate = Mock()
        self.reader = Mock(side_effect=self.read)
        self.workers = ResearchWorkers(self.directory,self.corpus,self.gate,{SOURCE:object()},page_reader=self.reader)

    def read(self,gate,job,key,profile,processing,**kwargs):
        lease=job.reserve('retrieval',Charge(Decimal(0)),1,source=SOURCE,
                          limits=self.storage.load_source(SOURCE).limits)
        lease.dispatch()
        now=datetime.now(timezone.utc)
        capture=self.capture(key=key,origin='runtime').model_copy(update={
            'checked_at':now,'observed_at':now,'processing':processing})
        lease.complete(Decimal(0),.001)
        return PageResult(Mock(),capture,extract(capture),'fixture')

    def work(self,*,task_id='task',fields=('variant.specs.ram_gb',),content='listing',international=False,
             tools=('corpus','fetch'),allowance=10,resources=None):
        request=ShoppingRequest(request_id='request',intent={'kind':'needs','description':'University laptop'},
            requirements={'category':'laptop'},market_scope={'include_international':international})
        return Work(context={'task':{'task_id':task_id,'run':{'run_id':'research','run_revision':1,
            'request_id':'request','request_revision':1},'role':'researcher','question':'RAM',
            'permitted_tools':tools,'deadline':datetime.now(timezone.utc)+timedelta(seconds=80),
            'call_allowance':allowance,'spend_allowance_usd':'0.50'},'request':request},
            resources=resources if resources is not None else [Resource(retailer_id='offline',key=self.key,
                content=content,fields=fields)],processing=self.capture().processing)

    def run_work(self,work):
        result,=self.workers.execute(self.job,[work],timeout=10)
        self.assertEqual(result['status'],'completed',result)
        return result['result']

    def seed(self):
        now=datetime.now(timezone.utc)
        self.corpus.ingest(self.capture().model_copy(update={'checked_at':now,'observed_at':now}),extract)

    def foreign(self):
        old=self.directory.get('offline')
        self.directory.upsert(old.model_copy(update={'source_country':'US','updated_at':old.updated_at+timedelta(seconds=1)}))

    def test_stored_specification_prevents_external_calls_and_keeps_provenance(self):
        self.seed()
        report=self.run_work(self.work())
        self.reader.assert_not_called()
        self.assertEqual(report.observations[0].freshness,'fresh')
        self.assertEqual(report.observations[0].observation.fact.value,16)
        self.assertEqual(report.evidence[0].corpus_version,'corpus-1')
        self.assertEqual(report.finding.claims[0].fact.value,Decimal(16))
        self.assertEqual(report.finding.gaps,())

    def test_foreign_support_is_allowed_without_foreign_purchase_candidates(self):
        self.seed();self.foreign()
        report=self.run_work(self.work(content='specification'))
        self.assertEqual(len(report.observations),1)
        excluded=self.run_work(self.work())
        self.assertEqual(excluded.observations,())
        self.assertEqual(excluded.evidence,())
        self.assertTrue(any(t.action=='scope' for t in excluded.trace))
        self.reader.assert_not_called()

    def test_opt_in_reuses_foreign_corpus_but_cached_price_never_becomes_verified(self):
        self.seed();self.foreign()
        report=self.run_work(self.work(international=True,fields=('variant.specs.ram_gb','price'),tools=('corpus',)))
        self.assertEqual({r.observation.evidence[0].field_path:r.freshness for r in report.observations},
                         {'price':'historical','variant.specs.ram_gb':'fresh'})
        self.assertTrue(report.finding.gaps)
        self.reader.assert_not_called()

    def test_unseeded_runtime_resource_uses_shared_ingestion_and_then_reuses(self):
        report=self.run_work(self.work())
        self.assertEqual(self.reader.call_count,1)
        self.assertEqual(report.observations[0].freshness,'fresh')
        self.assertEqual(report.trace[0].action,'stored')
        self.assertIsNotNone(self.corpus.state(self.key))
        self.run_work(self.work(task_id='second'))
        self.assertEqual(self.reader.call_count,1)

    def test_simultaneous_workers_share_one_exact_resource_fetch(self):
        def slow(*args,**kwargs):
            time.sleep(.1)
            return self.read(*args,**kwargs)
        self.reader.side_effect=slow
        results=self.workers.execute(self.job,[self.work(task_id='a'),self.work(task_id='b')],timeout=10)
        self.assertTrue(all(r['status']=='completed' for r in results))
        self.assertEqual(self.reader.call_count,1)
        self.assertTrue(all(r['result'].observations for r in results))

    def test_source_refusal_and_task_exhaustion_remain_explained_gaps(self):
        self.gate.check_scope.side_effect=ValueError('Denied')
        report=self.run_work(self.work())
        self.reader.assert_not_called()
        self.assertTrue(report.finding.gaps)
        self.gate.check_scope.side_effect=None
        report=self.run_work(self.work(allowance=0))
        self.assertEqual(report.observations,())
        self.assertTrue(any(t.status=='BudgetDenied' for t in report.trace))

    def test_tool_permissions_stop_fetch_and_keep_search_results_as_leads(self):
        provider=Mock();provider.search.return_value=[Mock(url='https://new.example/product')]
        self.workers.discovery=provider
        report=self.run_work(self.work(tools=('corpus','discover')))
        self.assertEqual(report.leads,('https://new.example/product',))
        self.reader.assert_not_called()
        self.assertIsNone(self.directory.get('new.example'))

    def test_model_cannot_invent_a_resource_or_supply_facts(self):
        provider=Mock();provider.generate.return_value=ModelResult('completed',Choice(resource_index=19),{})
        self.workers.provider=provider
        report=self.run_work(self.work(tools=('corpus','fetch','model')))
        self.assertIn('Model selected an out-of-scope resource',report.finding.gaps)
        self.reader.assert_not_called()
        self.assertEqual(report.finding.claims,())

    def test_cancellation_and_wrong_revision_never_dispatch(self):
        self.job.cancel()
        results=self.workers.execute(self.job,[self.work()],timeout=10)
        self.reader.assert_not_called()
        self.assertNotEqual(results[0]['status'],'completed')
        raw=self.work().model_dump(mode='json');raw['context']['task']['run']['request_revision']=2
        with self.assertRaises(ValueError):self.workers.execute(self.job,[Work.model_validate(raw)])

    def test_support_cannot_smuggle_foreign_price_fields(self):
        self.seed();self.foreign()
        report=self.run_work(self.work(content='specification',fields=('price',)))
        self.assertEqual(report.observations,())
        self.reader.assert_not_called()

    def test_default_shared_page_pipeline_ingests_live_capture_without_verification(self):
        self.workers.page_reader=read_page
        self.workers.profiles[SOURCE]=PageProfile(SOURCE,'https://store.example',sku='book-16')
        self.gate._registry_path=self.registry
        body=b'<script type="application/ld+json">{"@type":"Product","sku":"book-16","offers":{"@type":"Offer","price":"900","priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script>'
        self.gate.fetch.return_value=FetchOutcome(SOURCE,URL,(URL,),1,1,final_url=URL,status_code=200,
            content_type='text/html',body=body,observed_at=datetime.now(timezone.utc))
        report=self.run_work(self.work(fields=('price','stock')))
        self.assertEqual(len(report.observations),2)
        self.assertTrue(all(row.freshness=='historical' for row in report.observations))
        self.assertEqual(report.finding.claims,())
        self.assertTrue(all(e.origin=='runtime' and e.run is None for e in report.evidence))

    def test_processing_change_requires_new_work_and_conflicts_stay_visible(self):
        self.seed()
        work=self.work(tools=('corpus',))
        raw=work.model_dump(mode='json');raw['processing']['extraction_version']='new'
        report=self.run_work(Work.model_validate(raw))
        self.assertEqual(report.observations[0].freshness,'historical')
        self.assertEqual(report.finding.claims,())
        self.assertTrue(report.finding.gaps)
