import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.contract_interfaces import PlanningContext
from app.contracts_v2 import ShoppingRequest
from app.model_provider import ModelResult
from app.orchestrator import Proposal, Selection, investigate
import test_research_workers as fixtures


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work = self.fixture.work()
        self.context = PlanningContext(run=self.work.context.task.run, request=self.work.context.request)
        self.provider = Mock()

    def investigate(self, *replies, **opts):
        self.provider.generate.side_effect = [ModelResult('completed', value, {}) for value in replies]
        return investigate(self.context, job=self.fixture.job, workers=self.fixture.workers,
            provider=self.provider, resources=self.work.resources, processing=self.work.processing,
            deadline=datetime.now(timezone.utc)+timedelta(seconds=80), **opts)

    def test_selects_only_matching_evidenced_specifications(self):
        self.fixture.seed()
        proposal = Proposal(investigations=[dict(question='Check laptop memory and seller terms',resources=[0])],
                            clarification=None)
        result = self.investigate(proposal, Selection(candidates=[
            dict(target_id='unrelated',claim_indexes=[0]),
            dict(target_id=self.work.resources[0].key.variant_id,claim_indexes=[0])]))
        self.assertEqual(len(result.candidates),1)
        self.assertTrue(result.candidates[0].supporting_specifications[0].fact.evidence_ids)
        self.assertEqual(len(result.reports),1)
        self.assertIn('Candidate lacks matching specification evidence',result.gaps)

    def test_clarification_only_for_unanswered_needs(self):
        request = ShoppingRequest(request_id=self.context.run.request_id,
            intent=dict(kind='needs',description='Engineering laptop around $1200 with long battery life'),
            requirements=dict(category='laptop'))
        self.context = PlanningContext(run=self.context.run,request=request)
        question = Proposal(investigations=[],clarification=dict(clarification_id='software',
            question='Which engineering software must run locally?',
            material_reason='A required CAD application changes supported operating systems and GPUs.'))
        self.assertEqual(self.investigate(question).clarification,question.clarification)
        self.assertIsNone(self.investigate(question,Selection(candidates=[]),answers=['SolidWorks']).clarification)
        self.context = PlanningContext(run=self.context.run,request=ShoppingRequest(
            request_id=request.request_id, intent=dict(kind='exact_model',model='Latitude 7440'),
            requirements=dict(category='laptop')))
        self.assertIsNone(self.investigate(question).clarification)

    def test_unavailable_resources_and_cancelled_jobs_do_not_fetch(self):
        bad = Proposal(investigations=[dict(question='query',resources=[999])],clarification=None)
        self.assertTrue(self.investigate(bad).gaps)
        self.fixture.job.cancel()
        self.provider.reset_mock()
        self.assertTrue(self.investigate().gaps)
        self.provider.generate.assert_not_called()
        self.fixture.reader.assert_not_called()

