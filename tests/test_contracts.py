from decimal import Decimal
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app import contracts as c


class ContractTests(unittest.TestCase):
    def request(self):
        return dict(request_id='request-1', manufacturer='Apple', model='iPhone 17',
                    storage_gb=256, allowed_colors=['Black'], sim='physical+esim',
                    allowed_regions=['any'], warranty_requirement=None,
                    budget={'amount': '1100.00', 'currency': 'USD'}, payment_basis='cash',
                    fulfillment={'mode': 'delivery', 'country': 'LB', 'city': 'Beirut'},
                    allowed_markets=['LB'], deadline=None)

    def known(self, value, field='price'):
        return dict(state='known', value=value, evidence_ids=['ev-' + field])

    def unknown(self):
        return dict(state='unknown', reason='Not published')

    def evidence(self):
        return dict(evidence_id='ev-1', source_id='seller-1', product_id='parent-1',
                    variant_id='variant-1', scope='variant', field_path='price',
                    origin_url='https://seller.example/product',
                    final_url='https://seller.example/product',
                    observed_at='2026-10-12T17:00:00+03:00', response_sha256='a' * 64,
                    adapter_version='1', json_pointer='/prices/price')

    def offer(self):
        fields = ['variant.manufacturer', 'variant.model', 'variant.storage_gb',
                  'variant.color', 'variant.sim', 'price', 'condition', 'purchasable',
                  'online_stock', 'payment_basis']
        evidence = [dict(self.evidence(), evidence_id='ev-' + field, field_path=field)
                    for field in fields]
        return dict(offer_id='offer-1', source_id='seller-1', seller_id='seller-1',
                    variant=dict(source_id='seller-1', product_id='parent-1',
                                 variant_id='variant-1', manufacturer=self.known('Apple', 'variant.manufacturer'),
                                 model=self.known('iPhone 17', 'variant.model'), storage_gb=self.known(256, 'variant.storage_gb'),
                                 color=self.known('Black', 'variant.color'), sim=self.known('physical+esim', 'variant.sim'),
                                 region=self.unknown(), raw_attributes={'sku': 'shared-sku'}),
                    price=self.known({'amount': '1000.00', 'currency': 'USD'}),
                    payment_basis=self.known('cash', 'payment_basis'),
                    raw_price='100000', price_fractional_digits=2,
                    condition=self.known('new', 'condition'), purchasable=self.known(True, 'purchasable'),
                    online_stock=self.known(True, 'online_stock'), pickup_stock=[],
                    warranty={'provider': self.unknown(), 'months': self.unknown(),
                              'territory': self.unknown()},
                    fulfillment={'mode': 'delivery', 'country': 'LB', 'city': 'Beirut'},
                    arrival=self.unknown(),
                    costs=[dict(kind='delivery', state='unknown', amount=None,
                                reason='Delivery charge is not published')],
                    evidence=evidence)

    def test_request_keeps_explicit_constraints_and_exact_budget(self):
        request = c.ShoppingRequest.model_validate(self.request())
        self.assertEqual(request.budget.amount, Decimal('1100.00'))
        self.assertEqual(request.model_dump(mode='json')['budget']['amount'], '1100.00')
        self.assertEqual(request.allowed_colors, ['Black'])

    def test_missing_ambiguous_and_out_of_scope_requests_need_clarification(self):
        cases = []
        for field in ('model', 'sim', 'allowed_colors', 'allowed_regions', 'deadline',
                      'warranty_requirement', 'payment_basis', 'fulfillment'):
            item = self.request()
            del item[field]
            cases.append(item)
        for changes in ({'model': 'iPhone'}, {'allowed_colors': []},
                        {'allowed_colors': ['any', 'Black']}, {'storage_gb': True},
                        {'condition': 'used'}, {'allowed_markets': ['AE']},
                        {'budget': {'amount': '0', 'currency': 'USD'}},
                        {'budget': {'amount': '1100', 'currency': 'usd'}}):
            cases.append(dict(self.request(), **changes))
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValidationError):
                c.ShoppingRequest.model_validate(item)

    def test_money_rejects_float_nonfinite_negative_and_boolean_values(self):
        for value in (0.1, True, '-1', 'NaN', 'Infinity'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                c.Money(amount=value, currency='USD')

    def test_unknown_fee_and_branch_stock_survive_offer_json_roundtrip(self):
        offer = c.Offer.model_validate(self.offer())
        restored = c.Offer.model_validate_json(offer.model_dump_json())
        self.assertIsNone(restored.costs[0].amount)
        self.assertEqual(restored.costs[0].state, 'unknown')
        self.assertTrue(restored.online_stock.value)
        self.assertEqual(restored.pickup_stock, [])
        self.assertEqual(restored.variant.raw_attributes['sku'], 'shared-sku')

    def test_unknown_cost_cannot_carry_zero_and_included_tax_need_not_have_breakdown(self):
        with self.assertRaises(ValidationError):
            c.CostComponent(kind='delivery', state='unknown',
                            amount={'amount': '0', 'currency': 'USD'}, reason='Missing')
        included = c.CostComponent(kind='tax', state='included', evidence_ids=['ev-1'])
        self.assertIsNone(included.amount)
        with self.assertRaises(ValidationError):
            c.CostComponent(kind='delivery', state='additional', evidence_ids=['ev-1'])

    def test_fact_states_cannot_hide_conflicts_or_claim_unevidenced_values(self):
        for item in (dict(state='known', value=True), dict(state='unknown', value=True),
                     dict(state='conflict', alternatives=[True, True], reason='Conflict'),
                     dict(state='conflict', value=True, alternatives=[True, False],
                          reason='Conflict', evidence_ids=['ev-1'])):
            with self.subTest(item=item), self.assertRaises(ValidationError):
                c.Fact[bool].model_validate(item)
        fact = c.Fact[bool](state='conflict', alternatives=[True, False],
                            reason='Sources disagree', evidence_ids=['ev-1'])
        self.assertIsNone(fact.value)

    def test_evidence_normalizes_utc_and_requires_variant_field_binding(self):
        evidence = c.Evidence.model_validate(self.evidence())
        self.assertEqual(evidence.observed_at.isoformat(), '2026-10-12T14:00:00+00:00')
        for changes in ({'variant_id': None}, {'field_path': ''},
                        {'observed_at': '2026-10-12T14:00:00'}, {'response_sha256': 'bad'},
                        {'json_pointer': None}, {'scope': 'branch', 'branch_id': None}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                c.Evidence.model_validate(dict(self.evidence(), **changes))

    def test_offer_rejects_missing_evidence_wrong_variant_and_fake_branch_stock(self):
        for change in ('missing', 'wrong_variant', 'branch'):
            item = self.offer()
            if change == 'missing':
                item['evidence'] = []
            elif change == 'wrong_variant':
                item['evidence'][0]['variant_id'] = 'different-variant'
            else:
                item['pickup_stock'] = [dict(city='Beirut', stock=self.known(True))]
            with self.subTest(change=change), self.assertRaises(ValidationError):
                c.Offer.model_validate(item)

    def test_deadline_and_arrival_preserve_estimate_versus_guarantee(self):
        item = self.request()
        item['deadline'] = dict(arrive_by='2026-10-20T18:00:00+03:00', basis='guaranteed')
        self.assertEqual(c.ShoppingRequest.model_validate(item).deadline.basis, 'guaranteed')
        arrival = c.ArrivalWindow(earliest='2026-10-14T00:00:00Z',
                                  latest='2026-10-17T00:00:00Z', basis='estimate')
        self.assertEqual(arrival.basis, 'estimate')
        with self.assertRaises(ValidationError):
            c.ArrivalWindow(earliest='2026-10-18T00:00:00Z',
                            latest='2026-10-17T00:00:00Z', basis='estimate')

    def test_source_policy_accepts_technical_review_without_permission_email(self):
        item = dict(id='seller-1', name='Seller', origin='https://seller.example',
                    status='active', reviewed_at='2026-10-12',
                    admission=dict(basis='public endpoint checked', evidence=['https://seller.example/p'],
                                   reviewed_at='2026-10-12T14:00:00Z', scope='public facts'),
                    allowed_endpoints=['https://seller.example/p'],
                    limits=dict(requests_per_minute=6, concurrency=1, max_requests_per_run=4),
                    coverage={'catalog_observation': 'Registry metadata is retained separately'})
        self.assertEqual(c.SourcePolicy.model_validate(item).id, 'seller-1')
        for changes in ({'admission': None}, {'allowed_endpoints': ['https://other.example/p']},
                        {'origin': 'https://user:secret@seller.example'},
                        {'limits': dict(requests_per_minute=0, concurrency=1, max_requests_per_run=4)}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                c.SourcePolicy.model_validate(dict(item, **changes))

    def test_tool_outcomes_cannot_claim_success_with_errors(self):
        error = dict(code='TIMEOUT', message='Request timed out', retryable=True)
        with self.assertRaises(ValidationError):
            c.ToolResult(status='ok', errors=[error], duration_ms=10)
        with self.assertRaises(ValidationError):
            c.ToolResult(status='error', duration_ms=10)
        result = c.ToolResult(status='partial', data={'offers': []}, errors=[error], duration_ms=10)
        self.assertEqual(result.errors[0].code, 'TIMEOUT')

    def test_gate_refusals_have_their_own_codes(self):
        for code, message in (('BLOCKED', 'blocked:redirect to a private address'),
                              ('HTTP_ERROR', 'http:404 product page not found')):
            error = dict(code=code, message=message, retryable=False)
            result = c.ToolResult(status='error', errors=[error], duration_ms=3)
            restored = c.ToolResult.model_validate_json(result.model_dump_json())
            self.assertEqual((restored.schema_version, restored.errors[0].code), (1, code))
        with self.assertRaises(ValidationError):
            c.ToolError(code='FORBIDDEN', message='not a listed code', retryable=False)

    def test_run_abstention_and_verification_time_are_explicit(self):
        item = dict(run_id='run-1', request_id='request-1', status='abstained',
                    stop_reason='No qualifying offers', started_at='2026-10-12T13:59:00Z',
                    finished_at='2026-10-12T14:01:00Z',
                    usage=dict(elapsed_ms=120000, external_calls=3, planner_turns=0,
                               spend={'amount': '0.00', 'currency': 'USD'}))
        self.assertEqual(c.RunResult.model_validate(item).recommendations, [])
        item['recommendations'] = [dict(offer=self.offer(),
                                      payable_total={'amount': '1000', 'currency': 'USD'},
                                      verified_at='2026-10-12T13:00:00Z')]
        item['status'] = 'complete'
        with self.assertRaises(ValidationError):
            c.RunResult.model_validate(item)
        item['recommendations'][0]['verified_at'] = '2026-10-12T14:00:30Z'
        item['recommendations'][0]['offer']['costs'] = []
        self.assertEqual(len(c.RunResult.model_validate(item).recommendations), 1)

    def test_checked_in_examples_preserve_unresolved_costs_and_branch_scope(self):
        examples = json.loads((Path(__file__).parent / 'fixtures/contracts.json').read_text(encoding='utf-8'))
        request = c.ShoppingRequest.model_validate(examples['request'])
        offer = c.Offer.model_validate(examples['offer'])
        self.assertEqual(request.fulfillment.mode, 'pickup')
        self.assertEqual(request.fulfillment.branch_id, 'beirut-branch')
        self.assertTrue(offer.online_stock.value)
        self.assertEqual(offer.pickup_stock[0].stock.state, 'unknown')
        self.assertIsNone(offer.costs[0].amount)
        self.assertEqual(offer.price.value.amount, Decimal('1000.00'))
        restored = c.Offer.model_validate_json(offer.model_dump_json())
        self.assertEqual(restored.variant.variant_id, 'variant-1')
        self.assertEqual(restored.costs[0].reason, 'Mandatory fee is not published')

    def test_evidence_cannot_support_a_different_field_or_pickup_branch(self):
        for mutation in ('field', 'online_as_branch', 'wrong_branch'):
            item = self.offer()
            if mutation == 'field':
                item['price']['evidence_ids'] = ['ev-variant.color']
            else:
                fact = self.known(True, 'online_stock')
                item['pickup_stock'] = [dict(branch_id='beirut-branch', city='Beirut', stock=fact)]
                if mutation == 'wrong_branch':
                    item['evidence'].append(dict(self.evidence(), evidence_id='ev-branch',
                                                scope='branch', branch_id='tripoli-branch',
                                                field_path='pickup_stock.beirut-branch.stock'))
                    fact['evidence_ids'] = ['ev-branch']
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                c.Offer.model_validate(item)
        item = self.offer()
        item['pickup_stock'] = [dict(branch_id='beirut-branch', city='Beirut',
                                    stock=dict(state='known', value=False, evidence_ids=['ev-branch']))]
        item['evidence'].append(dict(self.evidence(), evidence_id='ev-branch',
                                    scope='branch', branch_id='beirut-branch',
                                    field_path='pickup_stock.beirut-branch.stock'))
        self.assertFalse(c.Offer.model_validate(item).pickup_stock[0].stock.value)

    def test_offer_price_retains_cash_card_or_unknown_basis(self):
        for basis in ('cash', 'card'):
            item = self.offer()
            item['payment_basis'] = self.known(basis, 'payment_basis')
            offer = c.Offer.model_validate(item)
            restored = c.Offer.model_validate_json(offer.model_dump_json())
            self.assertEqual(restored.payment_basis.value, basis)
        item['payment_basis'] = self.unknown()
        self.assertIsNone(c.Offer.model_validate(item).payment_basis.value)

    def test_source_coverage_status_cannot_hide_failures(self):
        error = dict(code='TIMEOUT', message='Request timed out', retryable=True)
        for status, errors in (('ok', [error]), ('error', []), ('partial', [])):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                c.SourceCoverage(source_id='seller-1', status=status, candidates_seen=0, errors=errors)
        result = c.SourceCoverage(source_id='seller-1', status='partial', candidates_seen=2, errors=[error])
        self.assertEqual(result.candidates_seen, 2)

    def test_explicitly_broad_manufacturer_or_device_requires_clarification(self):
        for changes in ({'manufacturer': 'any'}, {'manufacturer': '*'},
                        {'model': 'smartphone'}, {'model': 'latest smartphone'},
                        {'manufacturer': 'any', 'model': 'smartphone'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                c.ShoppingRequest.model_validate(dict(self.request(), **changes))

    def test_global_catalog_or_branch_claim_cannot_prove_selected_phone(self):
        item = self.offer()
        item['evidence'][0] = dict(item['evidence'][0], scope='source',
                                   product_id=None, variant_id=None)
        branch_item = self.offer()
        branch_item['pickup_stock'] = [dict(branch_id='beirut-branch', city='Beirut',
                                    stock=dict(state='known', value=True, evidence_ids=['ev-branch']))]
        branch_item['evidence'].append(dict(self.evidence(), evidence_id='ev-branch', scope='branch',
                                    branch_id='beirut-branch', product_id=None, variant_id=None,
                                    field_path='pickup_stock.beirut-branch.stock'))
        for scope, candidate in [('source', item), ('branch', branch_item)]:
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                c.Offer.model_validate(candidate)


if __name__ == '__main__':
    unittest.main()
