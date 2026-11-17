from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import unittest

from pydantic import ValidationError
from app import contracts_v2 as v
from app.assessment import assess_full_product
from app.costs import calculate_domestic_cost, FxRate
from app.eligibility import check_full_product, NewOnlyPolicy, apply_new_only_policy
from test_contracts_v2 import fixture

NOW = datetime(2026,11,16,12,5,tzinfo=timezone.utc)


class FullProductAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.offers, self.requests = fixture('offers'), fixture('requests')

    def offer(self,key='phone_pickup',**changes):
        return v.Offer.model_validate(self.offers[key] | changes)

    def request(self,key='exact_model',**changes):
        return v.ShoppingRequest.model_validate(self.requests[key] | changes)

    def assess(self,offer=None,request=None,**kwargs):
        return assess_full_product(request or self.request(),offer or self.offer(),now=NOW,**kwargs)

    def test_all_five_categories_with_only_explicit_requirements(self):
        for key,request in [('phone_pickup',self.request()),
                ('laptop_verified',self.request('needs',intent={'kind':'needs','description':'Laptop'},
                    requirements={'category':'laptop','min_ram_gb':16})),
                ('tablet',self.request('tablet')),('monitor',self.request('monitor')),('audio',self.request('audio'))]:
            with self.subTest(category=key):
                result=self.assess(self.offer(key),request)
                self.assertEqual(result.eligibility.outcome,'pass',result)
        o=self.offers['laptop_verified']
        o['variant']['specs']['gpu']=None
        o['variant']['specs']['operating_system']=None
        self.assertEqual(self.assess(self.offer('laptop_verified'),self.request('needs',
            intent={'kind':'needs','description':'Laptop'})).eligibility.outcome,'pass')

    def test_hard_specs_unknown_conflicts_and_mismatches(self):
        cases=[('phone_pickup',{'category':'phone','storage_gb':512}),
            ('laptop_verified',{'category':'laptop','min_ram_gb':128}),
            ('tablet',{'category':'tablet','stylus_support':False}),
            ('monitor',{'category':'monitor','min_refresh_hz':1000}),
            ('audio',{'category':'audio','active_noise_cancellation':False})]
        for name,requirements in cases:
            request=self.request(intent={'kind':'needs','description':'specific specs'},requirements=requirements)
            self.assertEqual(check_full_product(request,self.offer(name)).outcome,'fail')
        unknown={'state':'unknown','reason':'not supplied'}
        data=deepcopy(self.offers['phone_pickup'])
        data['variant']['specs']['storage_gb']=unknown
        self.assertEqual(self.assess(v.Offer.model_validate(data)).eligibility.outcome,'unresolved')
        data['variant']['specs']['storage_gb']={'state':'conflict','alternatives':[128,256],'reason':'conflicting listings',
            'evidence_ids':['phone-one:variant.specs.storage_gb']}
        self.assertEqual(self.assess(v.Offer.model_validate(data)).eligibility.outcome,'unresolved')

    def test_model_suffixes_are_preserved_and_software_not_invented(self):
        self.assertEqual(self.assess(request=self.request(intent={'kind':'exact_model','model':'Scout Phone+'})).decision,'rejected')
        result=self.assess(self.offer('laptop_verified'),self.request('needs'))
        self.assertEqual(result.eligibility.outcome,'unresolved')
        self.assertTrue(any(c.name=='required software suitability' for c in result.eligibility.checks))

    def test_payment_optional_until_requested_and_condition_opt_in(self):
        offer=self.offer(payment=None)
        self.assertEqual(self.assess(offer).decision,'recommendable')
        self.assertEqual(self.assess(offer,self.request(payment='card')).decision,'unresolved')
        data=deepcopy(self.offers['phone_pickup'])
        data['condition']['value']='used'
        offer=v.Offer.model_validate(data)
        self.assertEqual(self.assess(offer).decision,'rejected')
        self.assertEqual(self.assess(offer,self.request(allowed_conditions=['new','used'])).decision,'recommendable')

    def test_market_route_branch_and_destination(self):
        for fulfill in [dict(mode='delivery'),dict(mode='pickup',branch_id='another'),
                        dict(mode='pickup',destination={'country':'LB','city':'Tripoli'})]:
            self.assertNotEqual(self.assess(request=self.request(fulfillment=fulfill)).eligibility.outcome,'pass')
        overseas=self.offer('laptop_direct_import')
        request=self.request('needs',intent={'kind':'needs','description':'Laptop'})
        self.assertEqual(check_full_product(request,overseas).outcome,'fail')
        self.assertEqual(calculate_domestic_cost(request,overseas,now=NOW).status,'incomplete')

    def test_estimate_never_satisfies_guaranteed_deadline(self):
        data=deepcopy(self.offers['phone_pickup'])
        receipt=deepcopy(data['evidence'][0])
        receipt.update(evidence_id='arrival',field_path='arrival')
        data['evidence'].append(receipt)
        data['arrival']=dict(state='known',value=dict(earliest=NOW.isoformat(),
            latest=(NOW+timedelta(hours=1)).isoformat(),basis='estimate'),evidence_ids=['arrival'])
        request=self.request(fulfillment={'deadline':{'arrive_by':(NOW+timedelta(hours=2)).isoformat(),'basis':'guaranteed'}})
        self.assertEqual(self.assess(v.Offer.model_validate(data),request).decision,'unresolved')
        data['arrival']['value']['basis']='guaranteed'
        self.assertEqual(self.assess(v.Offer.model_validate(data),request).decision,'recommendable')

    def test_new_only_policy_is_scoped_dated_and_keeps_observation(self):
        unknown={'state':'unknown','reason':'not stated'}
        offer=self.offer(condition=unknown)
        e=deepcopy(self.offers['phone_pickup']['evidence'][0])
        e.update(evidence_id='policy-new',scope='source',product_id=None,variant_id=None,
            branch_id=None,route_id=None,field_path='condition',excerpt='This store sells only new phones.')
        policy=NewOnlyPolicy(seller_id=offer.seller.seller_id,categories=('phone',),
            evidence=v.Evidence.model_validate(e),valid_until=NOW+timedelta(days=1))
        resolved=apply_new_only_policy(offer,policy,now=NOW)
        self.assertEqual(resolved.condition.value,'new')
        self.assertEqual(resolved.evidence[-1].observed_at,policy.evidence.observed_at)
        self.assertEqual(resolved.evidence[-1].response_sha256,policy.evidence.response_sha256)
        for changes in ({'seller_id':'other'}, {'categories':('laptop',)},
                        {'excluded_products':(offer.source_product_id,)}, {'valid_until':NOW-timedelta(seconds=1)}):
            self.assertEqual(apply_new_only_policy(offer,policy.model_copy(update=changes),now=NOW),offer)
        conflict=self.offer(condition=dict(state='conflict',reason='conflicting condition',alternatives=['new','used'],evidence_ids=['phone-one:condition']))
        self.assertEqual(apply_new_only_policy(conflict,policy,now=NOW),conflict)

    def test_included_tax_and_delivery_are_not_added_twice(self):
        result=self.assess()
        self.assertEqual(result.cost.total.amount,Decimal('900'))
        self.assertEqual(result.decision,'recommendable')
        data=deepcopy(self.offers['phone_pickup'])
        data['costs'][0].update(state='additional',amount={'amount':'0.005','currency':'USD'})
        with localcontext() as ctx:
            ctx.prec=2
            cost=calculate_domestic_cost(self.request(),v.Offer.model_validate(data),now=NOW)
        self.assertEqual(cost.total.amount,Decimal('900.005'))

    def test_missing_fee_incomplete_or_explicit_estimated_never_verified(self):
        offer=self.offer(costs=[])
        result=self.assess(offer)
        self.assertEqual(result.cost.status,'incomplete')
        self.assertEqual(result.cost.known_subtotal.amount,Decimal(900))
        estimate=v.EstimatedCost(status='estimated',lower={'amount':'1','currency':'USD'},
            upper={'amount':'20','currency':'USD'},assumptions=('Synthetic fee range; must be verified',))
        result=self.assess(offer,estimates={'costs.order.tax':estimate})
        self.assertEqual(result.cost.status,'estimated')
        self.assertEqual((result.cost.lower.amount,result.cost.upper.amount),(Decimal(901),Decimal(920)))
        self.assertEqual(result.decision,'unresolved')

    def test_expired_conditional_and_unknown_fees_remain_open(self):
        for changes in ({'valid_until':(NOW-timedelta(days=1)).isoformat()},
                {'conditions':['city:Tripoli']},{'conditions':['unrecognized:waiver']},
                {'state':'unknown','reason':'not published','evidence_ids':[]}):
            data=deepcopy(self.offers['phone_pickup'])
            data['costs'][0].update(changes)
            self.assertEqual(self.assess(v.Offer.model_validate(data)).cost.status,'incomplete')

    def test_target_is_preference_maximum_is_hard_and_fx_is_conservative(self):
        self.assertEqual(self.assess(request=self.request(budget={'target':{'amount':'100','currency':'USD'}})).decision,'recommendable')
        self.assertEqual(self.assess(request=self.request(budget={'maximum':{'amount':'899','currency':'USD'}})).decision,'rejected')
        request=self.request(budget={'maximum':{'amount':'90000000','currency':'LBP'}})
        self.assertEqual(self.assess(request=request).decision,'unresolved')
        rates=[FxRate(base='USD',quote='LBP',rate=Decimal(n),observed_at=NOW,evidence_id='fx-'+n)
               for n in ('100000','110000')]
        result=self.assess(request=request,fx=rates)
        self.assertEqual(result.cost.budget_conversion.quote.amount,Decimal('99000000'))
        self.assertEqual(result.decision,'rejected')

    def test_pickup_stock_still_requires_branch_receipts(self):
        data=deepcopy(self.offers['phone_pickup'])
        for e in data['evidence']:
            if e['field_path']=='stock': e.update(scope='variant',branch_id=None)
        with self.assertRaises(ValidationError): v.Offer.model_validate(data)
