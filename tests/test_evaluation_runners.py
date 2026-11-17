from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.contract_interfaces import PlanningContext
from app.model_provider import ModelResult
from app.orchestrator import Proposal, Selection
from eval.runners import run_strategy, trial_request
from eval.scoring import PriceSide, Trial, compare_price, score
from eval.session import manual_capture
import test_research_workers as fixtures


class BaselineTests(unittest.TestCase):
    def test_controlled_strategies_share_evidence_and_charge_no_model_to_fixed(self):
        fixture=fixtures.ResearchWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.seed()
        work=fixture.work()
        provider=Mock()
        args=dict(job=fixture.job,workers=fixture.workers,provider=provider,resources=work.resources,
                  processing=work.processing,deadline=datetime.now(timezone.utc)+timedelta(seconds=80))
        context=PlanningContext(run=work.context.task.run,request=work.context.request)
        fixed=run_strategy('fixed-heuristic-warm',context,**args)
        provider.generate.assert_not_called()
        provider.generate.side_effect=[ModelResult('completed',Proposal(
            investigations=[dict(question='Check the RAM requirement',resources=[0])],clarification=None),{}),
            ModelResult('completed',Selection(candidates=[dict(target_id=work.resources[0].key.variant_id,claim_indexes=[0])]),{})]
        solo=run_strategy('single-agent-warm',context,**args)
        self.assertEqual(solo.reports[0].evidence,fixed.reports[0].evidence)
        self.assertEqual(len(solo.reports),1)
        self.assertEqual(solo.candidates[0].target_id,work.resources[0].key.variant_id)
        self.assertFalse(run_strategy('no-investigation',context,**args).reports)

    def test_trial_through_owned_api_records_abstention(self):
        from eval.run_trial import run
        import test_buyer_api as api_fixture
        fixture=api_fixture.BuyerApiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        @fixture.app.get('/_evaluation/binding')
        def binding():
            return dict(side='fixed-heuristic-warm',corpus='warm')
        case=json.loads(Path('eval/calibration.json').read_text())['cases'][0]
        def finish(_):
            claim=fixture.service.claim()
            fixture.service.publish(claim,status='abstained',stop_reason='No verified offers in fixture')
        receipt=run(fixture.client,case,'fixed-heuristic-warm',1,sleep=finish)
        self.assertEqual(receipt['outcome'],'abstained')
        self.assertEqual(receipt['trace'][-1]['snapshot']['request']['request_id'],'eval-c01-fixed-heuristic-warm-r1')

    def test_manual_inputs_and_freeze(self):
        case=json.loads(Path('eval/calibration.json').read_text(encoding='utf-8'))['cases'][0]
        capture=manual_capture(case,'chatgpt-browsing')
        self.assertIn(case['buyer_text'],capture['assistant_prompt'])
        self.assertIn('extra_verification',capture['timing_minutes'])
        self.assertIsNone(capture['record']['outcome'])
        from unittest.mock import patch
        with patch('eval.freeze.load',return_value={'pre_run':None}),self.assertRaises(ValueError):
            trial_request(dict(case,case_id='H00'),'full-warm',1)

    def test_denominators_censoring_and_paired_price(self):
        valid=Trial(case_id='C01',side='single-agent-warm',repetition=1,run_id='trial-1',
            outcome='recommended',adjudicated_class='answerable',selected=[dict(offer_id='a',verdict='valid')],
            stated_blocking_reason=None,constraints_preserved=True,timely=True,invented_offer=False,
            first_useful_seconds=3,verified_seconds=6,spend_usd='0.02',trace_id='t',corpus_version='warm',
            ledger_reference='receipt',needs_fit=None)
        failed=valid.model_copy(update=dict(case_id='C02',run_id='trial-2',outcome='timeout',selected=(),
            first_useful_seconds=None,verified_seconds=None,timely=False))
        stats=score([valid,failed],acquisition_usd=Decimal(1),maintenance_usd=Decimal(0))['configurations'][valid.side]
        self.assertEqual(stats['request_success_rate'],.5)
        self.assertEqual(stats['valid_offer_rate'],1)
        self.assertIsNone(stats['verified_seconds']['p95'])
        self.assertEqual(stats['cost_per_success_usd'],'0.04')
        a=PriceSide(valid=True,verified_total_usd='100',condition='new',fulfillment_basis='Beirut delivery',
                    verification_pass='pair',warranty='1 year',evidence_reference='receipt')
        opts=dict(market='domestic',equivalence='confirmed_equivalent',same_case=True)
        self.assertEqual(compare_price(a,a.model_copy(update={'verified_total_usd':Decimal(101)}),**opts),'tie')
        self.assertEqual(compare_price(a,a.model_copy(update={'verified_total_usd':None}),**opts),'inconclusive')
        self.assertEqual(compare_price(a,a,**dict(opts,equivalence='unresolved')),'not_comparable')
