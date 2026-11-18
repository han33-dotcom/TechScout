"""International landed costs (#63) against the synthetic route fixtures in fixtures/landed/routes.json."""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from app import contracts_v2 as v
from app.assessment import assess_full_product, shortlist
from app.costs import FxRate
from app.landed_costs import QuoteLine, RouteInputs, RouteQuote, calculate_landed_cost, requires_landed_cost
from app.writer import safe_text
from test_contracts_v2 import fixture

ROUTES = json.loads((Path(__file__).parent / 'fixtures' / 'landed' / 'routes.json').read_text(encoding='utf-8'))
NOW = datetime.fromisoformat(ROUTES['now'])
FREIGHT, BATTERY_FEE = 'forwarder freight to Lebanon', 'forwarder battery (dangerous goods) fee'
CUSTOMS = ('customs duty', 'import VAT', 'customs clearance')


def usd(amount):
    return {'amount': amount, 'currency': 'USD'}


class LandedCostTests(unittest.TestCase):
    def setUp(self):
        self.routes, self.offers, self.requests = deepcopy(ROUTES), fixture('offers'), fixture('requests')

    # Fixture access: every helper returns the mutable fixture dict, so a test edits then runs.
    def case(self, key='forwarded'):
        return self.routes[key]

    def quote(self, quote_id, key='forwarded'):
        return next(q for q in self.case(key)['inputs']['quotes'] if q['quote_id'] == quote_id)

    def line(self, quote_id, leg, component, key='forwarded'):
        return next(l for l in self.quote(quote_id, key)['lines'] if (l['leg'], l['component']) == (leg, component))

    def record(self, evidence_id, key='forwarded'):
        return next(e for e in self.case(key)['inputs']['evidence'] if e['evidence_id'] == evidence_id)

    def offer(self, key='forwarded'):
        case = self.case(key)
        return v.Offer.model_validate(deepcopy(self.offers[case['offer_fixture']]) | case['offer_changes'])

    def request(self, key='forwarded'):
        return v.ShoppingRequest.model_validate(self.requests['needs'] | self.case(key)['request_changes'])

    def inputs(self, key='forwarded'):
        return RouteInputs.model_validate(self.case(key)['inputs'])

    def fx(self, key='forwarded'):
        return [FxRate.model_validate(rate) for rate in self.case(key)['fx']]

    def landed(self, key='forwarded', *, now=NOW, **kwargs):
        return calculate_landed_cost(self.request(key), self.offer(key), self.inputs(key), fx=self.fx(key),
                                     now=now, **kwargs)

    def assess(self, key='forwarded', **kwargs):
        return assess_full_product(self.request(key), self.offer(key), fx=self.fx(key), now=NOW,
                                   route=self.inputs(key), **kwargs)

    @staticmethod
    def part(result, label):
        return next(c for c in result.components if c.label == label)

    def assert_open(self, result, *labels, reason=None):
        self.assertEqual(result.cost.status, 'incomplete', result.cost)
        for label in labels:
            self.assertIn(label, result.cost.missing)
            self.assertEqual(self.part(result, label).status, 'missing')
        if reason is not None:
            reasons = [r for c in result.components for r in c.reasons] + list(result.reasons)
            self.assertTrue(any(reason in r for r in reasons), reasons)

    # Acceptance 1: route components without duplication.
    def test_direct_route_counts_every_component_once_in_the_price_currency(self):
        result = self.landed('direct')
        self.assertEqual((result.route, result.support, result.cost.status), ('direct', 'supported', 'verified'))
        self.assertEqual(result.cost.total, v.Money(amount=Decimal('3693.50'), currency='AED'))
        added = {c.label: c.added.amount for c in result.components}
        self.assertEqual(added, {'retailer shipping to Lebanon': Decimal('120.00'), 'customs duty': Decimal('148'),
                                 'import VAT': Decimal('333'), 'customs clearance': Decimal('74'),
                                 'final delivery in Lebanon': Decimal('18.5'), 'sales tax on the order': 0})
        self.assertEqual(sum(added.values()) + Decimal('3000'), result.cost.total.amount)
        duty = self.part(result, 'customs duty')
        self.assertEqual(duty.quoted, v.Money(amount=Decimal('40.00'), currency='USD'))
        self.assertEqual(duty.evidence_ids, ('route-book-direct:customs', 'fx:usd-aed'))
        conversion = result.cost.budget_conversion
        self.assertEqual((conversion.quote.amount, conversion.evidence_id), (Decimal('1034.18'), 'fx:aed-usd'))
        self.assertIn('excludes any fee', result.basis)
        self.assertEqual(self.assess('direct').decision, 'recommendable')

    def test_forwarded_route_counts_both_legs_import_and_final_delivery(self):
        result = self.landed()
        self.assertEqual((result.route, result.support), ('forwarded', 'supported'))
        self.assertEqual(result.cost.total, v.Money(amount=Decimal('1015.00'), currency='USD'))
        self.assertEqual({c.label: c.added.amount for c in result.components}, {
            'retailer shipping to the forwarding address': 0, FREIGHT: Decimal('60.00'),
            'forwarder consolidation fee': Decimal('3.00'), 'forwarding insurance': Decimal('12.00'),
            BATTERY_FEE: Decimal('10.00'), 'customs duty': Decimal('30.00'), 'import VAT': Decimal('85.00'),
            'customs clearance': Decimal('15.00'), 'final delivery in Lebanon': 0, 'sales tax on the order': 0})
        assessment = self.assess()
        self.assertEqual((assessment.decision, assessment.route_support), ('recommendable', 'supported'))

    def test_fee_listed_by_two_providers_counts_once(self):
        # The forwarder offers to prepay the duty that the customs estimate also states.
        forwarder = self.quote('forward:forwarder')
        forwarder['declared_value'] = usd('800.00')
        forwarder['lines'].append({'leg': 'import', 'component': 'duty', 'state': 'additional',
                                   'amount': usd('30.00')})
        result = self.landed()
        self.assertEqual(result.cost.total.amount, Decimal('1015.00'))
        duty = self.part(result, 'customs duty')
        self.assertEqual(duty.sources, ('forward:forwarder', 'route-book-forward:customs'))
        self.assertEqual(duty.added.amount, Decimal('30.00'))
        for disagreement in ({'amount': usd('35.00')}, {'state': 'included', 'amount': None}):
            with self.subTest(disagreement=disagreement):
                forwarder['lines'][-1] = {k: val for k, val in (forwarder['lines'][-1] | disagreement).items()
                                          if val is not None}
                self.assert_open(self.landed(), 'customs duty', reason='providers disagree on the customs duty')
        # The retailer and the forwarder's buy-for-me quote both state the retailer's shipping to the hub.
        self.setUp()
        shipping = {'leg': 'retailer', 'component': 'shipping', 'state': 'additional', 'amount': usd('15.00')}
        self.line('forward:retailer', 'retailer', 'shipping').update(shipping)
        forwarder = self.quote('forward:forwarder')
        forwarder.update(seller_id='book-forward', source_variant_id='sku-book-forward')
        forwarder['lines'].append(dict(shipping))
        result = self.landed()
        self.assertEqual(result.cost.total.amount, Decimal('1030.00'))
        self.assertEqual(self.part(result, 'retailer shipping to the forwarding address').sources,
                         ('forward:retailer', 'forward:forwarder'))

    def test_separately_listed_other_charges_are_never_merged(self):
        other = {'leg': 'forwarding', 'component': 'other', 'state': 'additional', 'amount': usd('5.00')}
        self.quote('forward:forwarder')['lines'].append(other)
        self.assertEqual(self.landed().cost.total.amount, Decimal('1020.00'))
        second = deepcopy(self.quote('forward:forwarder')) | {'quote_id': 'forward:second', 'lines': [dict(other)]}
        second['evidence'] = second['evidence'] | {'evidence_id': 'forward:second'}
        self.case()['inputs']['quotes'].append(second)
        # Two equal amounts may be one fee restated or two fees: never merged into one.
        self.assert_open(self.landed(), 'other forwarder charge', reason='several other charges are listed separately')

    def test_retailer_cannot_settle_charges_beyond_the_forwarding_address(self):
        self.quote('forward:retailer')['lines'].append({'leg': 'final', 'component': 'delivery',
                                                        'state': 'included'})
        result = self.landed()
        self.assert_open(result, 'retailer shipping to the forwarding address', 'sales tax on the order',
                         reason='is from the retailer, which ships only to the forwarding address')
        self.assertEqual(self.part(result, 'final delivery in Lebanon').status, 'verified')  # forwarder's

    def test_listed_charges_are_read_unknowns_give_way_and_agreement_counts_once(self):
        listed = deepcopy(self.offers['laptop_forwarded']['costs'])  # retailer shipping included, freight 40
        self.case()['offer_changes']['costs'] = listed
        started = datetime.fromisoformat('2026-11-16T12:00:00+00:00')
        # Not read in this run and undated: the listing cannot verify, the current quotes settle it.
        self.assertEqual(self.landed().cost.total.amount, Decimal('1015.00'))
        # Read in this run: unpublished tax and import charges give way to the quotes; the listed
        # shipping agrees with the retailer quote and counts once; the listed forwarding freight is
        # beyond what the retailer ships, so only the forwarder's quote prices it.
        result = self.landed(run_started_at=started)
        self.assertEqual(result.cost.total.amount, Decimal('1015.00'))
        self.assertEqual(self.part(result, 'retailer shipping to the forwarding address').sources,
                         ('forward:retailer', 'listing'))
        self.assertEqual(self.part(result, FREIGHT).sources, ('forward:forwarder',))
        quotes = self.case()['inputs']['quotes']
        forwarder = quotes.pop(1)
        self.assert_open(self.landed(run_started_at=started), FREIGHT,
                         reason='comes from the retailer, which ships only to the forwarding address')
        quotes.insert(1, forwarder)
        retailer_shipping = next(c for c in listed if c['kind'] == 'shipping' and c['leg'] == 'retailer')
        retailer_shipping.update(state='additional', amount=usd('15.00'))
        self.assert_open(self.landed(run_started_at=started), 'retailer shipping to the forwarding address',
                         reason='providers disagree')
        retailer_shipping.update(state='included')
        del retailer_shipping['amount']
        receipt = deepcopy(self.offers['laptop_forwarded']['evidence'][0])
        receipt.update(evidence_id='book-forward:costs.order.payment', field_path='costs.order.payment')
        self.case()['offer_changes']['evidence'] = [*self.offers['laptop_forwarded']['evidence'], receipt]
        listed.append({'schema_version': 2, 'leg': 'order', 'kind': 'payment', 'state': 'additional',
                       'amount': usd('5'), 'conditions': ['payment:card'], 'evidence_ids': [receipt['evidence_id']]})
        self.assert_open(self.landed(run_started_at=started), 'payment fee', reason='depends on a condition')

    # Acceptance 2: quote scope, expiry and supported item route; sourced, timestamped FX.
    def test_expired_quote_keeps_its_charges_open(self):
        for until in (NOW - timedelta(seconds=1), NOW):
            with self.subTest(until=until):
                self.quote('forward:forwarder')['valid_until'] = until.isoformat()
                result = self.landed()
                self.assert_open(result, FREIGHT, BATTERY_FEE, 'forwarder consolidation fee',
                                 'forwarding insurance', 'final delivery in Lebanon', reason='expired at')
                self.assertEqual(result.cost.known_subtotal.amount, Decimal('930.00'))
                self.assertEqual(self.assess().decision, 'unresolved')

    def test_quote_without_stated_validity_counts_only_when_read_in_this_run(self):
        del self.quote('forward:forwarder')['valid_until']
        self.assert_open(self.landed(), FREIGHT, reason='states no validity period')
        before, after = (datetime.fromisoformat(t) for t in ('2026-11-16T12:00:00+00:00', '2026-11-16T12:02:00+00:00'))
        self.assertEqual(self.landed(run_started_at=before).cost.status, 'verified')
        self.assert_open(self.landed(run_started_at=after), FREIGHT)

    def test_unsupported_battery_rejects_the_route_unless_another_quote_carries_it(self):
        forwarder = self.quote('forward:forwarder')
        forwarder['battery_accepted'] = False
        result = self.landed()
        self.assertEqual(result.support, 'unsupported')
        self.assertIn('quote forward:forwarder service does not accept the item battery', result.reasons)
        self.assert_open(result, FREIGHT)
        self.assertEqual(self.assess().decision, 'rejected')
        other = deepcopy(forwarder) | {'quote_id': 'forward:other-service', 'battery_accepted': True,
                                       'service': 'synthetic-economy'}
        other['evidence'] = other['evidence'] | {'evidence_id': 'forward:other-service'}
        self.case()['inputs']['quotes'].append(other)
        self.assertEqual(self.landed().support, 'supported')
        # Another service that only estimates its freight still might carry it: uncertain, not rejected.
        other['lines'][0] = {'leg': 'forwarding', 'component': 'shipping', 'state': 'estimated',
                             'lower': usd('55'), 'upper': usd('70'), 'note': 'Synthetic rate card'}
        result = self.landed()
        self.assertEqual((result.support, result.cost.status), ('unresolved', 'estimated'))
        self.assertEqual(self.assess().decision, 'unresolved')
        # The same service both refusing and accepting the item contradicts itself.
        other['lines'][0] = deepcopy(forwarder['lines'][0])
        other['service'] = forwarder['service']
        result = self.landed()
        self.assertEqual(result.support, 'unresolved')
        self.assert_open(result, FREIGHT, reason='disagree on carrying the item')
        # The same service carrying only another leg does not contradict a freight refusal.
        other['lines'] = [line for line in forwarder['lines'] if line['leg'] == 'final']
        forwarder['lines'] = [line for line in forwarder['lines'] if line['leg'] != 'final']
        result = self.landed()
        self.assertEqual(result.support, 'unsupported')
        self.assertEqual(self.part(result, 'final delivery in Lebanon').status, 'verified')
        forwarder['lines'] += other['lines']
        self.case()['inputs']['quotes'].pop()
        forwarder['battery_accepted'] = None
        result = self.landed()
        self.assertEqual(result.support, 'unresolved')
        self.assert_open(result, FREIGHT, reason='does not confirm that the item battery is accepted')

    def test_only_a_current_matching_quote_can_rule_the_route_out(self):
        forwarder = self.quote('forward:forwarder')
        forwarder['battery_accepted'] = False
        for change in ({'valid_until': (NOW - timedelta(seconds=1)).isoformat()}, {'valid_until': None},
                       {'provider_id': 'another-forwarder'}):
            with self.subTest(change=change):
                quote = forwarder | change
                self.case()['inputs']['quotes'][1] = {k: val for k, val in quote.items() if val is not None}
                result = self.landed()
                self.assertEqual(result.support, 'unresolved')
                self.assert_open(result, FREIGHT, reason='service does not accept the item battery')
                self.assertEqual(self.assess().decision, 'unresolved')

    def test_retailer_refusal_is_not_overridden_by_its_listing(self):
        listed = deepcopy(self.offers['laptop_direct_import']['costs'])  # includes listed shipping
        self.case('direct')['offer_changes']['costs'] = listed
        started = datetime.fromisoformat('2026-11-16T12:00:00+00:00')
        self.assert_open(self.landed('direct', run_started_at=started), 'retailer shipping to Lebanon',
                         reason='providers disagree')
        self.quote('direct:checkout', 'direct')['battery_accepted'] = False
        result = self.landed('direct', run_started_at=started)
        self.assertEqual(result.support, 'unsupported')
        self.assert_open(result, 'retailer shipping to Lebanon',
                         reason='the listing does not show that the refused item is carried')
        self.assertEqual(self.assess('direct', run_started_at=started).decision, 'rejected')

    def test_two_listed_charges_on_one_slot_are_never_counted_once(self):
        data = deepcopy(self.offers['laptop_forwarded'])
        receipts = []
        for leg in ('order', 'retailer'):
            receipt = deepcopy(data['evidence'][0]) | {'evidence_id': f'book-forward:costs.{leg}.payment',
                                                         'field_path': f'costs.{leg}.payment'}
            receipts.append(receipt)
        self.case()['offer_changes'].update(evidence=[*data['evidence'], *receipts], costs=[
            {'schema_version': 2, 'leg': leg, 'kind': 'payment', 'state': 'additional', 'amount': usd('5'),
             'evidence_ids': [f'book-forward:costs.{leg}.payment']} for leg in ('order', 'retailer')])
        started = datetime.fromisoformat('2026-11-16T12:00:00+00:00')
        self.assert_open(self.landed(run_started_at=started), 'payment fee',
                         reason='several listed charges fall on the payment fee')
        self.case()['offer_changes']['costs'].pop()
        self.assertEqual(self.landed(run_started_at=started).cost.total.amount, Decimal('1020.00'))

    def test_quote_naming_the_listing_is_still_checked_unless_it_is_the_retailer_receipt(self):
        def opens(quote_id, changes, label, reason, evidence=None):
            self.setUp()
            quote = self.quote(quote_id)
            quote.update(changes)
            for key, value in (changes.get('_drop') or {}).items():
                del quote[key]
            quote.pop('_drop', None)
            if evidence:
                quote['evidence'] = quote['evidence'] | evidence
            self.assert_open(self.landed(), label, reason=reason)
        listing = {'seller_id': 'book-forward', 'source_variant_id': 'sku-book-forward'}
        opens('route-book-forward:customs', listing | {'declared_value': usd('100.00')}, 'customs duty',
              'declares less than the device price')
        opens('forward:forwarder', listing | {'_drop': {'package': None}}, FREIGHT,
              'does not state the package it priced')
        opens('forward:retailer', {}, 'retailer shipping to the forwarding address', 'belongs to another listing',
              evidence={'variant_id': 'another-sku'})
        opens('forward:retailer', {}, 'sales tax on the order', "is not backed by the retailer's receipt",
              evidence={'source_id': 'another-source', 'product_id': None, 'variant_id': None})
        self.setUp()
        seller = deepcopy(self.quote('forward:forwarder')) | listing | {
            'quote_id': 'forward:seller', 'provider_id': 'book-forward', 'service': None,
            'lines': [{'leg': 'final', 'component': 'delivery', 'state': 'included'}]}
        seller['evidence'] = seller['evidence'] | {'evidence_id': 'forward:seller'}
        forwarder = self.quote('forward:forwarder')
        forwarder['lines'] = [l for l in forwarder['lines'] if l['leg'] != 'final']
        self.case()['inputs']['quotes'].append(seller)
        self.assert_open(self.landed(), 'final delivery in Lebanon',
                         reason='is from the retailer, which ships only to the forwarding address')

    def test_battery_declaration_must_match_the_item(self):
        self.quote('forward:forwarder')['package']['battery'] = 'none'
        self.assert_open(self.landed(), FREIGHT, reason='declared a different battery configuration')
        self.case()['inputs']['item']['battery'] = {'state': 'unknown', 'reason': 'not stated'}
        result = self.landed()
        self.assert_open(result, FREIGHT, BATTERY_FEE, reason='item battery configuration is not established')

    def test_missing_dimensions_leave_freight_open_never_zero(self):
        self.case()['inputs']['item']['dimensions'] = {'state': 'unknown', 'reason': 'not published'}
        result = self.landed()
        self.assert_open(result, FREIGHT, reason='item dimensions are not established')
        self.assertEqual(result.cost.known_subtotal.amount, Decimal('930.00'))  # freight is not counted as 0
        forwarder = self.quote('forward:forwarder')
        del forwarder['limits']['max_dimension_sum_mm']  # priced by size, no size limit: still open
        self.assert_open(self.landed(), FREIGHT, reason='item dimensions are not established')
        # Weight-only pricing with no size limit does not need the size.
        del forwarder['package']['dimensions']
        self.assertEqual(self.landed().cost.status, 'verified')
        self.case()['inputs']['item']['weight_g'] = {'state': 'unknown', 'reason': 'not published'}
        self.assert_open(self.landed(), FREIGHT, reason='item weight is not established')

    def test_quote_must_cover_the_actual_item_seller_and_value(self):
        cases = [
            ('forward:forwarder', ('package', 'weight_g'), 2000, FREIGHT, 'priced a lighter package'),
            ('forward:forwarder', ('package', 'dimensions'), {'length_mm': 300, 'width_mm': 200, 'height_mm': 50},
             FREIGHT, 'priced a smaller package'),
            ('forward:forwarder', ('package',), None, FREIGHT, 'does not state the package it priced'),
            ('forward:forwarder', ('provider_id',), 'another-forwarder', FREIGHT, 'is not from the route forwarder'),
            ('forward:retailer', ('seller_id',), 'another-seller', 'retailer shipping to the forwarding address',
             'issued for another seller or listing'),
            ('forward:retailer', ('source_variant_id',), 'another-sku', 'sales tax on the order',
             'issued for another seller or listing'),
            ('route-book-forward:customs', ('declared_value',), usd('700.00'), 'customs duty',
             'declares less than the device price'),
            ('route-book-forward:customs', ('declared_value',), {'amount': '800.00', 'currency': 'AED'},
             'import VAT', 'declares a value in another currency'),
            ('route-book-forward:customs', ('declared_value',), None, 'customs clearance',
             'does not state the declared value'),
        ]
        for quote_id, path, value, label, reason in cases:
            with self.subTest(reason=reason):
                self.setUp()
                target = self.quote(quote_id)
                for key in path[:-1]:
                    target = target[key]
                if value is None:
                    del target[path[-1]]
                else:
                    target[path[-1]] = value
                self.assert_open(self.landed(), label, reason=reason)
        self.setUp()
        customs = self.quote('route-book-forward:customs')
        customs['lines'].append({'leg': 'order', 'component': 'payment_fee', 'state': 'additional',
                                 'amount': usd('1')})
        self.assert_open(self.landed(), *CUSTOMS, reason='does not name the actual seller and listing')

    def test_service_limits_refuse_an_oversize_item(self):
        for limits, reason in (({'max_weight_g': 2000}, 'item weight is over the limit'),
                               ({'max_side_mm': 300}, 'item size is over the limit'),
                               ({'max_dimension_sum_mm': 600}, 'item size is over the limit')):
            with self.subTest(limits=limits):
                self.quote('forward:forwarder')['limits'] = limits
                result = self.landed()
                self.assertEqual(result.support, 'unsupported')
                self.assertTrue(any(reason in r for r in result.reasons), result.reasons)

    def test_fx_rate_needs_evidence_its_own_time_and_a_current_age(self):
        def breaks(change):
            self.setUp()
            change()
            result = self.landed('direct')
            self.assert_open(result, *CUSTOMS, 'final delivery in Lebanon',
                             reason='no current sourced exchange rate for the customs duty')
            self.assertEqual(result.cost.known_subtotal.amount, Decimal('3120.00'))
            self.assertNotIn('fx:usd-aed', [e for c in result.components for e in c.evidence_ids])
        rate = self.case('direct')['fx'][0]
        breaks(lambda: self.case('direct')['inputs'].update(
            evidence=[e for e in self.case('direct')['inputs']['evidence'] if e['evidence_id'] != 'fx:usd-aed']))
        breaks(lambda: self.record('fx:usd-aed', 'direct').update(field_path='price'))
        breaks(lambda: self.case('direct')['fx'][0].update(observed_at='2026-11-16T09:30:00Z'))
        stale = (NOW - timedelta(hours=24, seconds=1)).isoformat()
        breaks(lambda: (self.case('direct')['fx'][0].update(observed_at=stale),
                        self.record('fx:usd-aed', 'direct').update(observed_at=stale)))
        future = (NOW + timedelta(minutes=1)).isoformat()
        breaks(lambda: (self.case('direct')['fx'][0].update(observed_at=future),
                        self.record('fx:usd-aed', 'direct').update(observed_at=future)))
        # A receipt backs one currency pair and one value: an inverted rate citing the other pair's
        # receipt, or two values citing one receipt, are both unsourced.
        breaks(lambda: self.case('direct').update(fx=[rate | {'rate': '0.27', 'evidence_id': 'fx:aed-usd'}]))
        breaks(lambda: self.case('direct')['fx'].append(rate | {'rate': '3.50'}))
        with self.assertRaises(ValidationError):
            FxRate.model_validate(rate | {'observed_at': '2026-11-16T09:00:00'})  # naive time
        # The least favourable fresh sourced rate is used, so a choice of rates cannot lower a total.
        self.setUp()
        dearer = deepcopy(self.record('fx:usd-aed', 'direct')) | {'evidence_id': 'fx:usd-aed-2'}
        self.case('direct')['inputs']['evidence'].append(dearer)
        self.case('direct')['fx'].append(self.case('direct')['fx'][0] | {'rate': '3.80', 'evidence_id': 'fx:usd-aed-2'})
        self.assertEqual(self.landed('direct').cost.total.amount, Decimal('3709.00'))

    def test_item_facts_need_receipts_bound_to_the_field_and_listing(self):
        for change in (lambda r: r.update(field_path='package.weight'),
                       lambda r: r.update(variant_id='another-sku'),
                       lambda r: r.update(observed_at=(NOW + timedelta(minutes=1)).isoformat())):
            with self.subTest():
                self.setUp()
                change(self.record('book-forward:package.weight_g'))
                self.assert_open(self.landed(), FREIGHT, reason='item weight is not established')

    def test_route_eligibility_evidence_decides_support(self):
        data = self.case()['offer_changes']
        route = deepcopy(self.offers['laptop_forwarded']['route'])
        route['forwarding_leg']['item_eligible']['value'] = False
        data['route'] = route
        result = self.landed()
        self.assertEqual(result.support, 'unsupported')
        self.assertEqual(self.assess().decision, 'rejected')
        route['forwarding_leg']['item_eligible'] = {'state': 'unknown', 'reason': 'not stated'}
        result = self.landed()
        self.assertEqual((result.cost.status, result.support), ('verified', 'unresolved'))
        self.assertEqual(self.assess().decision, 'unresolved')
        # Even with eligibility passing and a verified total, an unresolved route is never recommendable.
        self.setUp()
        passing = self.assess()
        self.assertEqual(passing.decision, 'recommendable')
        self.assertEqual(passing.model_copy(update={'route_support': 'unresolved'}).decision, 'unresolved')

    # Acceptance 3: estimated or missing required costs stay out of the verified primary ranking.
    def test_unknown_required_costs_stay_missing_never_zero(self):
        quotes = self.case()['inputs']['quotes']
        quotes[:] = [q for q in quotes if q['quote_id'] != 'route-book-forward:customs']
        result = self.landed()
        self.assertEqual(result.cost.missing, CUSTOMS)
        self.assertEqual(result.cost.known_subtotal.amount, Decimal('885.00'))
        self.assertEqual(self.assess().decision, 'unresolved')
        self.setUp()
        self.quote('forward:forwarder')['lines'].append(
            {'leg': 'forwarding', 'component': 'storage', 'state': 'unknown', 'note': 'charged separately'})
        self.assert_open(self.landed(), 'forwarder storage fee', reason='does not state this charge')
        # Another quote's 'included' cannot turn a charge a current quote states into zero; an amount can.
        storage = {'leg': 'forwarding', 'component': 'storage', 'state': 'included'}
        other = deepcopy(self.quote('forward:forwarder')) | {'quote_id': 'forward:storage', 'lines': [storage]}
        other['evidence'] = other['evidence'] | {'evidence_id': 'forward:storage'}
        self.case()['inputs']['quotes'].append(other)
        self.assert_open(self.landed(), 'forwarder storage fee',
                         reason='quote forward:forwarder states the forwarder storage fee without an amount')
        storage.update(state='additional', amount=usd('4.00'))
        self.assertEqual(self.landed().cost.total.amount, Decimal('1019.00'))
        bare = calculate_landed_cost(self.request(), self.offer(), now=NOW)
        self.assertEqual(bare.support, 'unresolved')
        self.assertEqual(bare.cost.missing, ('retailer shipping to the forwarding address', FREIGHT, BATTERY_FEE,
                                             *CUSTOMS, 'final delivery in Lebanon', 'sales tax on the order'))
        self.assertTrue(all(safe_text(label, 60) == label for label in bare.cost.missing))

    def test_estimates_and_incomplete_totals_stay_out_of_the_primary_ranking(self):
        customs = self.quote('route-book-forward:customs')
        customs['lines'] = [{'leg': 'import', 'component': c, 'state': 'estimated', 'lower': usd('0'),
                             'upper': usd(u), 'note': 'Synthetic published rate range'}
                            for c, u in (('duty', '40'), ('import_vat', '100'), ('clearance', '20'))]
        result = self.landed()
        self.assertEqual(result.cost.status, 'estimated')
        self.assertEqual((result.cost.lower.amount, result.cost.upper.amount), (Decimal('885.00'), Decimal('1045.00')))
        self.assertIn('customs duty: Synthetic published rate range', result.cost.assumptions)
        estimated = (self.offer(), self.assess())
        self.assertEqual(estimated[1].decision, 'unresolved')
        domestic = v.Offer.model_validate(self.offers['laptop_verified'])
        verified = (domestic, assess_full_product(self.request(), domestic, now=NOW))
        self.assertEqual(verified[1].cost.total.amount, Decimal('930'))  # above the estimate's lower end
        customs['lines'] = []
        self.case()['inputs']['quotes'].remove(customs)
        incomplete = (self.offer(), self.assess())
        self.assertEqual(incomplete[1].cost.known_subtotal.amount, Decimal('885.00'))
        self.quote('forward:forwarder')['battery_accepted'] = False
        refused = self.offer().model_copy(update={'offer_id': 'book-forward-refused'})
        refused = (refused, assess_full_product(self.request(), refused, fx=self.fx(), now=NOW, route=self.inputs()))
        result = shortlist([estimated, incomplete, verified, refused])
        self.assertEqual([offer.offer_id for offer, _ in result.primary], ['book-local'])
        self.assertEqual([item.cost.status for item in result.uncertain], ['estimated', 'incomplete'])
        self.assertIn('payable total is only estimated', result.uncertain[0].reasons)
        self.assertEqual(result.rejected, ('book-forward-refused',))

    def test_shortlist_ranks_in_one_currency_cheapest_first(self):
        domestic = v.Offer.model_validate(self.offers['laptop_verified'])
        for key in ('direct', 'forwarded'):  # a target only: a preference, converted when a rate exists
            self.case(key)['request_changes']['budget'] = {'target': usd('1000')}
        pairs = [(self.offer(key), self.assess(key)) for key in ('direct', 'forwarded')]
        pairs.append((domestic, assess_full_product(self.request(), domestic, now=NOW)))
        conversion = pairs[0][1].cost.budget_conversion
        self.assertEqual((conversion.quote.amount, conversion.evidence_id), (Decimal('1034.18'), 'fx:aed-usd'))
        ranked = shortlist(pairs)
        self.assertEqual([offer.offer_id for offer, _ in ranked.primary], ['book-local', 'book-forward', 'book-direct'])
        # Without a budget currency the AED total is verified but cannot be ranked against USD totals.
        for key in ('direct', 'forwarded'):
            self.case(key)['request_changes']['budget'] = None
        pairs = [(self.offer(key), self.assess(key)) for key in ('direct', 'forwarded')]
        pairs.append((domestic, assess_full_product(self.request(), domestic, now=NOW)))
        self.assertEqual(pairs[0][1].decision, 'recommendable')
        ranked = shortlist(pairs)
        self.assertEqual([offer.offer_id for offer, _ in ranked.primary], ['book-local', 'book-forward'])
        self.assertEqual([item.offer.offer_id for item in ranked.uncertain], ['book-direct'])
        self.assertEqual(ranked.uncertain[0].reasons, ('verified total in AED is not compared with totals in USD',))
        self.assertEqual([offer.offer_id for offer, _ in shortlist(pairs, 'AED').primary], ['book-direct'])
        # One offer per currency: the tie goes to the domestic offer's currency.
        tied = shortlist([pairs[0], pairs[2]])
        self.assertEqual(([o.offer_id for o, _ in tied.primary], [i.offer.offer_id for i in tied.uncertain]),
                         (['book-local'], ['book-direct']))
        # A hard maximum in another currency still needs a sourced conversion.
        self.case('direct')['request_changes']['budget'] = {'maximum': usd('1100')}
        self.case('direct')['fx'].pop()
        self.assertEqual(self.landed('direct').cost.missing, ('current budget FX quote',))
        self.case('direct')['request_changes']['budget'] = {'target': usd('1000')}
        self.assertIsNone(self.landed('direct').cost.budget_conversion)

    def test_inputs_must_belong_to_the_offer_route(self):
        request, offer = self.request(), self.offer()
        quote = self.quote('forward:forwarder')
        moved = deepcopy(quote) | {'route_id': 'another-route'}
        moved['evidence']['route_id'] = 'another-route'
        with self.assertRaisesRegex(ValueError, 'offer route'):
            calculate_landed_cost(request, offer, RouteInputs(quotes=(RouteQuote.model_validate(moved),)), now=NOW)
        courier = self.quote('direct:courier', 'direct')
        courier['lines'].append({'leg': 'forwarding', 'component': 'shipping', 'state': 'included'})
        with self.assertRaisesRegex(ValueError, 'forwarded route'):
            self.landed('direct')
        domestic = v.Offer.model_validate(self.offers['phone_pickup'])
        self.assertFalse(requires_landed_cost(domestic))
        with self.assertRaises(ValueError):
            calculate_landed_cost(request, domestic, now=NOW)
        clash = deepcopy(self.record('book-forward:package.weight_g')) | {'evidence_id': 'book-forward:price'}
        self.case()['inputs']['evidence'].append(clash)
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.landed()

    def test_quote_and_line_shapes_are_validated(self):
        base = deepcopy(self.quote('forward:forwarder'))
        bad_quotes = [
            {'lines': base['lines'] + [base['lines'][0]]},
            {'evidence': base['evidence'] | {'scope': 'source', 'route_id': None}},
            {'valid_until': '2026-11-16T12:00:00Z'},  # before its observation
            {'seller_id': 'book-forward'},  # seller without the listing variant
        ]
        for change in bad_quotes:
            with self.subTest(change=sorted(change)), self.assertRaises(ValidationError):
                RouteQuote.model_validate(base | change)
        bad_lines = [
            {'leg': 'import', 'component': 'shipping', 'state': 'included'},
            {'leg': 'final', 'component': 'delivery', 'state': 'additional'},
            {'leg': 'final', 'component': 'delivery', 'state': 'included', 'amount': usd('1')},
            {'leg': 'final', 'component': 'delivery', 'state': 'estimated', 'lower': usd('1'), 'upper': usd('2')},
            {'leg': 'final', 'component': 'delivery', 'state': 'estimated', 'lower': usd('3'), 'upper': usd('2'),
             'note': 'x'},
            {'leg': 'final', 'component': 'delivery', 'state': 'unknown'},
        ]
        for line in bad_lines:
            with self.subTest(line=line), self.assertRaises(ValidationError):
                QuoteLine.model_validate(line)

    def test_unknown_price_keeps_the_total_open(self):
        self.case()['offer_changes']['price'] = {'state': 'unknown', 'reason': 'not shown'}
        result = self.landed()
        self.assertEqual(result.cost.status, 'incomplete')
        # Customs computed on a declared value cannot be checked against an unknown price either.
        self.assertEqual(result.cost.missing, ('positive, unambiguous price', *CUSTOMS))
        self.assertIsNone(result.cost.known_subtotal)
        self.assertEqual(self.part(result, FREIGHT).reasons, ('the device price is not established',))

    def test_domestic_assessment_is_unchanged(self):
        assessment = assess_full_product(v.ShoppingRequest.model_validate(self.requests['exact_model']),
                                         v.Offer.model_validate(self.offers['phone_pickup']), now=NOW)
        self.assertEqual((assessment.decision, assessment.route_support), ('recommendable', None))


if __name__ == '__main__':
    unittest.main()
