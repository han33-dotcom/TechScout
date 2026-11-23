import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

from app.accounting import Charge, Ledger
from app.contract_interfaces import PlanningContext
from app.contracts_v2 import AgentFinding, FindingClaim, ShoppingRequest
from app.domestic_discovery import DomesticDiscovery
from app.search_connectors import Hit, Response
from app.evidence_critique import CritiqueChoice
from app.model_provider import ModelResult, OpenAIProvider
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
            provider=self.provider, resources=opts.pop('resources', self.work.resources),
            processing=self.work.processing,
            deadline=datetime.now(timezone.utc)+timedelta(seconds=80), **opts)

    def test_selects_only_matching_evidenced_specifications(self):
        self.fixture.seed()
        proposal = Proposal(investigations=[dict(question='Check laptop memory and seller terms',resources=[0])],
                            clarification=None)
        result = self.investigate(proposal, CritiqueChoice(issue_index=None), Selection(candidates=[
            dict(target_id='unrelated',claim_indexes=[0]),
            dict(target_id=self.work.resources[0].key.variant_id,claim_indexes=[0])]))
        self.assertEqual(len(result.candidates),1)
        self.assertTrue(result.candidates[0].supporting_specifications[0].fact.evidence_ids)
        self.assertEqual(len(result.reports),1)
        self.assertIn('Candidate lacks matching specification evidence',result.gaps)

    def test_planner_can_revise_domestic_query_language_and_channel_from_previous_results(self):
        workers = self.fixture.workers
        connectors = {}
        for channel in ('tavily', 'brave'):
            connector = Mock(rate=Decimal('0.001'))
            connector.quote.return_value = connector.rate
            connector.fetch.return_value = Response((Hit('https://shop.example/laptop', 'Laptop'),),
                                                     request_id=channel + '-id', units=1)
            connectors[channel] = connector
        workers.domestic_tools = DomesticDiscovery(workers.gate, workers.directory, connectors)
        first = Proposal(investigations=[dict(question='Who sells this laptop?', resources=[],
            search=dict(query='Lebanon university laptop', objective='Find a domestic seller'))],
            clarification=None)
        second = Proposal(investigations=[dict(question='Is there a second seller?', resources=[],
            search=dict(query='حاسوب محمول بيروت', objective='Find another seller',
                        channel='brave', language='ar'))], clarification=None)
        result = self.investigate(first, second, CritiqueChoice(issue_index=None), resources=())
        previous = self.provider.generate.call_args_list[1].args[1]['previous_searches']
        self.assertEqual(previous, [dict(attempts=[('tavily', 'ok')], sellers=['host:shop.example'])])
        self.assertEqual([r.discovery_report.attempts[0].channel for r in result.reports], ['tavily', 'brave'])
        self.assertEqual(connectors['brave'].fetch.call_args.args[0].language, 'ar')
        self.assertFalse(result.candidates)
        self.assertTrue(all(not report.finding.claims for report in result.reports))

    def test_clarification_only_for_unanswered_needs(self):
        request = ShoppingRequest(request_id=self.context.run.request_id,
            intent=dict(kind='needs',description='Engineering laptop around $1200 with long battery life'),
            requirements=dict(category='laptop'))
        self.context = PlanningContext(run=self.context.run,request=request)
        question = Proposal(investigations=[],clarification=dict(clarification_id='software',
            question='Which engineering software must run locally?',
            material_reason='A required CAD application changes supported operating systems and GPUs.'))
        self.assertEqual(self.investigate(question).clarification,question.clarification)
        self.assertIsNone(self.investigate(question,CritiqueChoice(issue_index=None),
            Selection(candidates=[]),answers=['SolidWorks']).clarification)
        self.context = PlanningContext(run=self.context.run,request=ShoppingRequest(
            request_id=request.request_id, intent=dict(kind='exact_model',model='Latitude 7440'),
            requirements=dict(category='laptop')))
        self.assertIsNone(self.investigate(question,CritiqueChoice(issue_index=None)).clarification)

    def test_unavailable_resources_and_cancelled_jobs_do_not_fetch(self):
        bad = Proposal(investigations=[dict(question='query',resources=[999])],clarification=None)
        self.assertTrue(self.investigate(bad).gaps)
        self.fixture.job.cancel()
        self.provider.reset_mock()
        self.assertTrue(self.investigate().gaps)
        self.provider.generate.assert_not_called()
        self.fixture.reader.assert_not_called()

    def test_one_scoped_follow_up_and_reevaluation_share_the_job(self):
        self.fixture.seed()
        first = self.work.resources[0]
        second = first.model_copy(update={'key': first.key.model_copy(update={'representation': 'alternate'})})
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0])], clarification=None)
        responses = []

        def respond(_prompt, data, schema, *, budget, cancelled):
            self.assertFalse(cancelled())
            self.assertIs(budget.job, self.fixture.job)
            responses.append(schema)
            if schema is Proposal:
                reply = proposal
            elif schema is CritiqueChoice:
                hold = budget.reserve_spend(Decimal('0.0001'))
                hold.dispatch()
                hold.commit(Decimal('0.0001'))
                if data['can_follow_up']:
                    self.assertEqual(data['issues'][0]['resource_index'], 1)
                    self.assertEqual(data['issues'][0]['field_path'], 'variant.specs.ram_gb')
                    reply = CritiqueChoice(issue_index=0)
                else:
                    self.assertEqual(data['issues'], [])
                    reply = CritiqueChoice(issue_index=0)  # a second request is rejected
            else:
                reply = Selection(candidates=[dict(target_id=first.key.variant_id, claim_indexes=[0])])
            return ModelResult('completed', reply, {})

        self.provider.generate.side_effect = respond
        result = investigate(self.context, job=self.fixture.job, workers=self.fixture.workers,
            provider=self.provider, resources=(first, second), processing=self.work.processing,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=80))
        self.assertEqual(responses, [Proposal, CritiqueChoice, CritiqueChoice, Selection])
        self.assertTrue(result.critique.follow_up_attempted)
        self.assertEqual(result.critique.status, 'degraded')
        self.assertEqual(result.critique.reason, 'invalid follow-up selection')
        self.assertEqual(len(result.reports), 2)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(self.fixture.reader.call_count, 1)
        self.assertEqual(self.fixture.reader.call_args.args[2], second.key)

    def test_conflicts_and_unsupported_values_are_excluded_even_when_evaluator_fails(self):
        self.fixture.seed()
        first = self.work.resources[0]
        second = first.model_copy(update={'key': first.key.model_copy(update={'representation': 'alternate'})})
        now = datetime.now(timezone.utc)
        capture = self.fixture.capture(key=second.key).model_copy(update={
            'body': b'{"price":"100.25","ram":32}', 'observed_at': now, 'checked_at': now})
        self.fixture.corpus.ingest(capture, fixtures.extract)
        report = self.fixture.run_work(self.work)
        genuine = report.finding.claims[0]
        fabricated = FindingClaim.model_validate(dict(genuine.model_dump(mode='json'),
            fact=dict(genuine.fact.model_dump(mode='json'), value=64)))
        self.context = self.context.model_copy(update={'findings': (AgentFinding(
            finding_id='fabricated', task_id='prior', run=self.context.run, role='researcher',
            claims=(fabricated,)),)})
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0, 1])],
                            clarification=None)
        result = self.investigate(proposal, None, resources=(first, second))
        self.assertEqual(result.critique.status, 'degraded')
        self.assertFalse(result.critique.follow_up_attempted)
        self.assertEqual(result.candidates, ())
        self.assertEqual(len(result.reports[0].observations), 2)
        self.assertEqual({gap.kind for gap in result.critique.issues}, {'contradiction', 'unsupported'})
        self.fixture.reader.assert_not_called()

    def test_budget_denial_preserves_existing_evidence_without_follow_up(self):
        self.fixture.seed()
        target = self.work.resources[0].key.variant_id
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0])], clarification=None)
        self.provider.generate.side_effect = [
            ModelResult('completed', proposal, {}),
            ModelResult('budget_denied', None, {}),
            ModelResult('completed', Selection(candidates=[dict(target_id=target, claim_indexes=[0])]), {}),
        ]
        result = investigate(self.context, job=self.fixture.job, workers=self.fixture.workers,
            provider=self.provider, resources=self.work.resources, processing=self.work.processing,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=80))
        self.assertEqual(result.critique.status, 'degraded')
        self.assertFalse(result.critique.follow_up_attempted)
        self.assertEqual(len(result.candidates), 1)
        self.fixture.reader.assert_not_called()

    def test_requirement_outside_resource_menu_is_flagged_without_authorizing_a_fetch(self):
        self.fixture.seed()
        raw = self.context.request.model_dump(mode='json')
        raw['requirements']['gpu'] = 'Dedicated GPU'
        request = ShoppingRequest.model_validate(raw)
        self.context = PlanningContext(run=self.context.run, request=request)
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0])], clarification=None)
        result = self.investigate(proposal, CritiqueChoice(issue_index=0), Selection(candidates=[]))
        issue, = result.critique.issues
        self.assertEqual((issue.kind, issue.field_path), ('missing', 'variant.specs.gpu'))
        self.assertIsNone(issue.resource_index)
        self.assertFalse(result.critique.follow_up_attempted)
        self.assertEqual(result.critique.reason, 'invalid follow-up selection')
        self.fixture.reader.assert_not_called()

    def test_evaluator_cannot_dispatch_against_a_shared_in_flight_reservation(self):
        self.fixture.seed()
        target = self.work.resources[0].key.variant_id
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0])], clarification=None)
        client = Mock()
        evaluator = OpenAIProvider(client=client)
        other_ledger = Ledger(self.fixture.storage, 'research-fixture', fixture=True)
        other_owner = other_ledger.open_job(self.fixture.job.run_id)
        hold = other_owner.reserve('model', Charge(Decimal('.75')), 30)
        self.addCleanup(hold.release)

        def respond(prompt, data, schema, **options):
            if schema is CritiqueChoice:
                reply = evaluator.generate(prompt, data, schema, **options)
                self.assertEqual(reply.status, 'budget_denied')
                return reply
            value = proposal if schema is Proposal else Selection(
                candidates=[dict(target_id=target, claim_indexes=[0])])
            return ModelResult('completed', value, {})

        self.provider.generate.side_effect = respond
        result = investigate(self.context, job=self.fixture.job, workers=self.fixture.workers,
            provider=self.provider, resources=self.work.resources, processing=self.work.processing,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=80))
        self.assertEqual(result.critique.reason, 'evaluator response unavailable')
        self.assertEqual(len(result.candidates), 1)
        client.assert_not_called()
        self.fixture.reader.assert_not_called()

    def test_cancellation_discards_late_critique_and_stops_new_research(self):
        self.fixture.seed()
        first = self.work.resources[0]
        second = first.model_copy(update={'key': first.key.model_copy(update={'representation': 'alternate'})})
        proposal = Proposal(investigations=[dict(question='Check RAM', resources=[0])], clarification=None)

        def respond(_prompt, data, schema, *, budget, cancelled):
            if schema is Proposal:
                return ModelResult('completed', proposal, {})
            self.assertIs(schema, CritiqueChoice)
            self.assertTrue(data['can_follow_up'])
            budget.job.cancel()
            self.assertTrue(cancelled())
            return ModelResult('completed', CritiqueChoice(issue_index=0), {})

        self.provider.generate.side_effect = respond
        result = investigate(self.context, job=self.fixture.job, workers=self.fixture.workers,
            provider=self.provider, resources=(first, second), processing=self.work.processing,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=80))
        self.assertEqual(result.critique.reason, 'deadline or cancellation')
        self.assertFalse(result.critique.follow_up_attempted)
        self.assertEqual(result.candidates, ())
        self.assertEqual(len(result.reports), 1)
        self.assertEqual(self.provider.generate.call_count, 2)
        self.fixture.reader.assert_not_called()

