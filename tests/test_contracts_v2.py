"""Contract regressions for downstream API and worker consumers using offline fixtures."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app import collection_contracts as collection
from app import contracts as v1
from app import contracts_v2 as v2
from app.contract_schema import MODELS, schema_bundle

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / 'tests' / 'fixtures' / 'v2'


def fixture(name):
    return json.loads((V2 / f'{name}.json').read_text(encoding='utf-8'))


class ContractsV2Tests(unittest.TestCase):
    def setUp(self):
        self.requests = fixture('requests')
        self.offers = fixture('offers')
        self.workspace = fixture('workspace')

    def test_both_versions_have_explicit_readers_and_roundtrip(self):
        legacy = json.loads((ROOT / 'examples' / 'request-iphone17-beirut.json').read_text())
        self.assertIsInstance(v2.read_request(legacy), v1.ShoppingRequest)
        legacy.pop('schema_version', None)
        self.assertIsInstance(v2.read_request(legacy), v1.ShoppingRequest)
        legacy_bundle = json.loads((ROOT / 'tests' / 'fixtures' / 'contracts.json').read_text())
        self.assertIsInstance(v2.read_offer(legacy_bundle['offer']), v1.Offer)
        legacy_result = v1.RunResult(
            run_id='v1-run', request_id=legacy['request_id'], status='abstained',
            stop_reason='no qualifying offer', started_at='2026-11-15T12:00:00Z',
            finished_at='2026-11-15T12:00:05Z',
            usage={'elapsed_ms':5000, 'external_calls':1, 'planner_turns':0,
                   'spend':{'amount':'0', 'currency':'USD'}},
        )
        self.assertEqual(v2.read_run(legacy_result.model_dump(mode='json')), legacy_result)
        for payload, reader, cls in ((self.requests['needs'], v2.read_request, v2.ShoppingRequest),
                                      (self.offers['phone_pickup'], v2.read_offer, v2.Offer),
                                      (self.workspace['current'], v2.read_run, v2.RunSnapshot)):
            with self.subTest(reader=reader):
                result = reader(payload)
                self.assertIsInstance(result, cls)
                self.assertEqual(reader(result.model_dump(mode='json')), result)
                with self.assertRaisesRegex(ValueError, 'Unsupported'):
                    reader({**payload, 'schema_version': 3})

    def test_requests_show_all_categories_entry_modes_and_optins(self):
        requests = {name: v2.ShoppingRequest.model_validate(data)
                    for name, data in self.requests.items()}
        self.assertEqual({request.requirements.category for request in requests.values()},
                         {'phone', 'laptop', 'tablet', 'monitor', 'audio'})
        self.assertEqual({request.intent.kind for request in requests.values()},
                         {'exact_model', 'product_link', 'needs'})
        self.assertEqual(requests['exact_model'].allowed_conditions, ('new',))
        self.assertEqual(requests['exact_model'].fulfillment.destination.country, 'LB')
        self.assertTrue(requests['exact_model'].market_scope.allows('LB'))
        self.assertFalse(requests['exact_model'].market_scope.allows('US'))
        self.assertTrue(requests['refined'].market_scope.allows('US'))
        self.assertFalse(requests['refined'].market_scope.allows('GB'))
        self.assertIn('refurbished', requests['audio'].allowed_conditions)
        with self.assertRaisesRegex(ValidationError, 'explicit opt-in'):
            v2.ShoppingRequest.model_validate({
                **self.requests['needs'], 'market_scope': {'international_markets': ['US']},
            })
        with self.assertRaises(ValidationError):
            v2.ShoppingRequest.model_validate({
                **self.requests['needs'], 'requirements': {'category':'laptop', 'sim':'esim-only'},
            })

    def test_refinement_preserves_budget_meaning_and_advances_revision(self):
        request = v2.ShoppingRequest.model_validate(self.requests['needs'])
        self.assertEqual(request.budget.target.amount, Decimal(900))
        self.assertIsNone(request.budget.maximum)
        revision = request.refine({
            'budget': {'maximum': {'amount': '950', 'currency': 'USD'}},
            'preferences': ['battery'],
            'market_scope': {'include_international': True, 'international_markets': ['US', 'AE']},
        })
        self.assertEqual(revision, v2.ShoppingRequest.model_validate(self.requests['refined']))
        edited = revision.refine({'budget': {'target': {'amount': '920', 'currency': 'USD'}}})
        self.assertEqual(edited.budget.maximum.amount, Decimal(950))
        self.assertEqual(edited.budget.target.amount, Decimal(920))
        uncapped = edited.refine({'budget': {'maximum': None}})
        self.assertIsNone(uncapped.budget.maximum)
        self.assertEqual(uncapped.budget.target.amount, Decimal(920))
        with self.assertRaisesRegex(ValueError, 'retains the request ID'):
            request.refine({'revision': 12})
        with self.assertRaises(ValidationError):
            request.refine({'budget': {'target': {'amount': 900.1, 'currency': 'USD'}}})

    def test_offer_match_fixtures_preserve_seller_terms(self):
        offers = {data['offer_id']: v2.Offer.model_validate(data) for data in self.offers.values()}
        matches = tuple(map(v2.MatchAssessment.model_validate, fixture('matches')))
        self.assertEqual({o.variant.specs.category for o in offers.values()},
                         {'phone', 'laptop', 'tablet', 'monitor', 'audio'})
        self.assertEqual([m.outcome for m in matches],
                         ['confirmed_equivalent', 'different_configuration', 'unresolved'])
        for match in matches:
            left, right = offers[match.left_offer_id], offers[match.right_offer_id]
            self.assertEqual(left.variant.variant_id, match.left_variant_id)
            self.assertEqual(right.variant.variant_id, match.right_variant_id)
            self.assertLessEqual(set(match.evidence_ids), {e.evidence_id for e in (*left.evidence, *right.evidence)})
        left, right = (offers[matches[0].left_offer_id], offers[matches[0].right_offer_id])
        self.assertEqual(left.variant.variant_id, right.variant.variant_id)
        self.assertNotEqual(left.activation.value, right.activation.value)
        self.assertNotEqual(left.condition.value, right.condition.value)
        self.assertNotEqual(left.warranty.months.value, right.warranty.months.value)

    def test_seller_rule_ignores_the_warehouse_and_requires_the_actual_lebanese_merchant(self):
        """#185: a Lebanese merchant identified for this item qualifies the route; origin plays no part."""
        from app.costs import supports_domestic_cost

        def offer(*, warehouse='ZZ', first_party=True, receipts=(), market='LB', conflict=False):
            data = deepcopy(self.offers['laptop_verified'])
            data['route']['origin_country'] = warehouse
            data['seller'].update(first_party=first_party, market=market)
            price = next(e for e in data['evidence'] if e['field_path'] == 'price')
            for path, url in receipts:
                data['evidence'].append(dict(price, evidence_id='seller-' + path, field_path=path,
                                             origin_url=url, final_url=url, excerpt=path,
                                             conflicts=['Sold by another merchant'] if conflict else []))
            return v2.Offer.model_validate(data)

        listing = self.offers['laptop_verified']['listing_url']
        named = (('seller.name', listing), ('seller.market', listing))
        for warehouse in ('ZZ', 'US', 'LB'):
            own_store = offer(warehouse=warehouse)
            self.assertEqual(own_store.domestic_seller(), 'first_party')
            self.assertTrue(own_store.locally_fulfillable())
            self.assertTrue(supports_domestic_cost(own_store))  # the seller's own terms, not an import
        self.assertEqual(offer(first_party=False, receipts=named).domestic_seller(), 'listing')
        legacy = deepcopy(self.offers['laptop_verified'])
        del legacy['seller']['first_party']  # an older record still reads, and dispatch alone proves nothing
        legacy = v2.Offer.model_validate(legacy)
        self.assertEqual((legacy.route.origin_country, legacy.seller.first_party), ('LB', False))
        unestablished = (
            legacy,
            offer(first_party=False),                                         # unidentified merchant
            offer(first_party=False, receipts=named[:1]),                     # marketplace names no address
            offer(first_party=False, receipts=(('seller.name', listing),
                                               ('seller.market', 'https://market.example/about'))),
            offer(receipts=named, conflict=True),                             # conflicting merchant evidence
        )
        for item in unestablished:
            self.assertIsNone(item.domestic_seller())
            self.assertFalse(item.locally_fulfillable())
            self.assertTrue(item.locally_fulfillable(allow_unverified_seller=True))
        foreign = offer(market='AE', receipts=named)
        self.assertIsNone(foreign.domestic_seller())
        self.assertFalse(foreign.locally_fulfillable(allow_unverified_seller=True))
        self.assertFalse(supports_domestic_cost(foreign))
        forwarded = v2.Offer.model_validate(self.offers['laptop_forwarded'])
        self.assertIsNone(forwarded.domestic_seller())

    def test_scope_errors_for_branch_variant_and_forwarding_hub(self):
        pickup = self.offers['phone_pickup']
        wrong_branch = deepcopy(pickup)
        wrong_branch['route']['branch_id'] = 'different-store'
        with self.assertRaisesRegex(ValidationError, 'Branch evidence'):
            v2.Offer.model_validate(wrong_branch)
        wrong_variant = deepcopy(pickup)
        wrong_variant['source_variant_id'] = 'different-variant'
        with self.assertRaisesRegex(ValidationError, 'source listing and variant'):
            v2.Offer.model_validate(wrong_variant)
        wrong_leg = self.offers['laptop_forwarded']
        wrong_leg['route']['forwarding_leg']['origin']['city'] = 'Different hub'
        with self.assertRaisesRegex(ValidationError, 'overseas hub'):
            v2.Offer.model_validate(wrong_leg)

    def test_workspace_keeps_live_provisional_and_uncertain_separate(self):
        workspace = v2.ComparisonWorkspace.model_validate(self.workspace)
        self.assertTrue(workspace.previous.provisional)
        self.assertFalse(workspace.previous.recommendations)
        self.assertEqual(workspace.current.request.revision, 2)
        primary = workspace.current.recommendations[0].primary
        self.assertEqual(primary.cost.total.amount, Decimal(930))
        cpu_receipt = next(receipt for receipt in primary.offer.evidence
                           if receipt.field_path == 'variant.specs.cpu')
        self.assertEqual(cpu_receipt.origin, 'maintenance')
        self.assertLess(cpu_receipt.observed_at, workspace.current.started_at)
        self.assertEqual(workspace.current.uncertain[0].cost.status, 'incomplete')
        reversed_revisions = {'current': self.workspace['previous'], 'previous': self.workspace['current']}
        with self.assertRaisesRegex(ValidationError, 'older run'):
            v2.ComparisonWorkspace.model_validate(reversed_revisions)
        count = self.workspace['current']['coverage']
        count['qualifying'] = count['retrieved'] + 1
        with self.assertRaisesRegex(ValidationError, 'Retailer coverage'):
            v2.ComparisonWorkspace.model_validate(self.workspace)

    def test_market_scope_applies_to_uncertain_and_provisional_offers(self):
        snapshot = self.workspace['current']
        snapshot['request']['market_scope'] = {'include_international': False}
        with self.assertRaisesRegex(ValidationError, 'Result offers must match'):
            v2.RunSnapshot.model_validate(snapshot)

    def test_at_most_three_recommendation_groups(self):
        current = self.workspace['current']
        current['recommendations'] *= 4
        with self.assertRaises(ValidationError) as captured:
            v2.RunSnapshot.model_validate(current)
        self.assertIn('recommendations', str(captured.exception))

    def test_finalists_require_current_receipts_and_complete_costs(self):
        for defect, expected in (
            ('stale', 'predates this run'),
            ('other_run', 'checked in the current run'),
            ('unknown', 'resolved costs'),
            ('missing_charge', 'mandatory route charges'),
            ('wrong_sum', 'include all applicable'),
            ('over_budget', 'meet the hard budget'),
        ):
            with self.subTest(defect=defect):
                snapshot = deepcopy(self.workspace['current'])
                primary = snapshot['recommendations'][0]['primary']
                if defect in ('stale', 'other_run'):
                    receipt = next(e for e in primary['offer']['evidence'] if e['field_path'] == 'price')
                    if defect == 'stale':
                        receipt['observed_at'] = '2026-11-15T12:00:30Z'
                    else:
                        receipt['run']['run_id'] = 'old-run'
                elif defect == 'unknown':
                    charge = primary['offer']['costs'][0]
                    charge.update(state='unknown', reason='Unknown tax', evidence_ids=[])
                elif defect == 'missing_charge':
                    primary['offer']['costs'].pop()
                elif defect == 'wrong_sum':
                    primary['cost']['total']['amount'] = '924'
                else:
                    snapshot['request']['budget'].update(
                        target={'amount':'850','currency':'USD'}, maximum={'amount':'900','currency':'USD'},
                    )
                with self.assertRaisesRegex(ValidationError, expected):
                    v2.RunSnapshot.model_validate(snapshot)

    def test_cost_range_and_fx_avoid_false_savings(self):
        with self.assertRaisesRegex(ValidationError, 'ordered endpoints'):
            v2.EstimatedCost.model_validate({
                'status':'estimated', 'lower':{'amount':'200','currency':'USD'},
                'upper':{'amount':'100','currency':'USD'}, 'assumptions':['Estimated duty'],
            })
        with self.assertRaisesRegex(ValidationError, 'understate'):
            v2.FxConversion.model_validate({
                'base':{'amount':'100','currency':'USD'}, 'quote':{'amount':'200','currency':'AED'},
                'rate':'3.6725','observed_at':'2026-11-16T12:00:00Z','evidence_id':'fx-1',
            })

    def test_finding_and_consumer_schema_bundle(self):
        finding = v2.AgentFinding.model_validate(fixture('finding'))
        self.assertEqual(finding.claims[0].fact.value.amount, Decimal(920))
        bundle = schema_bundle()
        self.assertEqual(len(bundle['anyOf']), len(MODELS))
        for entry in bundle['anyOf']:
            self.assertIn(entry['$ref'].split('/')[-1], bundle['$defs'])
        discriminators = bundle['$defs']['ShoppingRequest']['properties']
        self.assertEqual(discriminators['intent']['discriminator']['propertyName'], 'kind')
        self.assertEqual(discriminators['requirements']['discriminator']['propertyName'], 'category')
        amount_schema = bundle['$defs']['Money']['properties']['amount']
        self.assertEqual({branch['type'] for branch in amount_schema['anyOf']},
                         {'string', 'integer'})


class CollectionContractTests(unittest.TestCase):
    def test_directory_status_does_not_grant_a_capability(self):
        audited, discovered = map(collection.RetailerDirectoryEntry.model_validate, fixture('retailers'))
        self.assertEqual(audited.discovery_status, 'audited')
        self.assertEqual(discovered.discovery_status, 'discovered')
        self.assertIsNone(discovered.capability)

    def test_manifest_keeps_international_limits_inside_aggregate(self):
        data = fixture('collection')
        records = collection.CollectionSnapshot.model_validate(data)
        self.assertEqual(records.manifest.limits.max_spend_usd, Decimal(50))
        self.assertEqual(records.manifest.international_limits.max_spend_usd, Decimal(15))
        self.assertEqual(records.checkpoints[0].versions, records.job.versions)
        data['manifest']['international_limits']['max_fetch_attempts'] = 3001
        with self.assertRaisesRegex(ValidationError, 'aggregate allocation'):
            collection.CollectionSnapshot.model_validate(data)

    def test_checkpoint_belongs_to_job_and_removal_requires_evidence(self):
        data = fixture('collection')
        checkpoint = data['checkpoints'][0]
        checkpoint['job_id'] = 'other-job'
        with self.assertRaisesRegex(ValidationError, 'belong to this job'):
            collection.CollectionSnapshot.model_validate(data)
        checkpoint.update(status='removed', evidence_ids=[])
        with self.assertRaisesRegex(ValidationError, 'Removal requires'):
            collection.IngestionCheckpoint.model_validate(checkpoint)
