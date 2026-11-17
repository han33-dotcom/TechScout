import json
from pathlib import Path
import unittest
from pydantic import ValidationError
from app.contracts_v2 import ComparisonWorkspace, SourceOutcome
ROOT=Path(__file__).resolve().parents[1]
class ComparisonContractTests(unittest.TestCase):
    def test_legacy_fixture_and_generated_browser_fixture_agree(self):
        old=json.loads((ROOT/'tests/fixtures/v2/workspace.json').read_text())
        parsed=ComparisonWorkspace.model_validate(old)
        self.assertEqual(parsed.current.source_outcomes,())
        self.assertIsNone(parsed.current.request.refinement_note)
        self.assertEqual(parsed.model_dump(mode='json'),json.loads((ROOT/'frontend/src/fixtures/workspace.json').read_text()))
    def test_refinement_note_keeps_exact_intent_and_constraints(self):
        from app.contracts_v2 import ShoppingRequest
        request=ShoppingRequest.model_validate(dict(request_id='r',intent=dict(kind='exact_model',model='iPhone 17'),requirements=dict(category='phone',storage_gb=256),budget=dict(maximum=dict(amount='1000',currency='USD'))))
        updated=request.refine({'refinement_note':'Prefer longer warranty'})
        self.assertEqual(updated.intent,request.intent)
        self.assertEqual(updated.requirements,request.requirements)
        self.assertEqual(updated.budget,request.budget)
        self.assertEqual(updated.revision,2)
    def test_source_failures_need_explanations_and_ids_are_unique(self):
        with self.assertRaises(ValidationError):SourceOutcome(source_id='s',name='Shop',status='failed')
        data=json.loads((ROOT/'tests/fixtures/v2/workspace.json').read_text())
        data['current']['source_outcomes']=[dict(source_id='s',name='Shop',status='unchecked')]*2
        with self.assertRaises(ValidationError):ComparisonWorkspace.model_validate(data)
