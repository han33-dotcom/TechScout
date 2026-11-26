"""The pre-order delivery quote exception in the source gate (#186), offline.

Synthetic stores and a scripted transport only. The quote route is the gate's one POST: these
tests pin that nothing else can be sent that way, that it shares the gate's other controls, and
that cart, checkout and account paths stay closed to every read.
"""
from copy import deepcopy
import json
import os
from unittest import mock

from app.contracts import SourcePolicy
from app.quote_capabilities import (
    STOREFRONT_DOCUMENT, QuoteCapability, active_quote_routes, quote_url, storefront_body,
    storefront_request_allowed,
)
from app.source_capabilities import CapabilityRef
from app.source_registry import DEFAULT_REGISTRY, get_active_source
from app.sources import SourceGate
from test_source_gate import EXPECTED_HEADERS, GateCase, alpha, json_ok, redirect, respond

QUOTE = 'https://alpha.example/api/2025-01/graphql.json'
REF = CapabilityRef(capability_id='alpha-quote', version='1')
QUOTE_HEADERS = {*EXPECTED_HEADERS, 'content-type', 'content-length'}


def route(**changes):
    rule = {'capability_id': 'alpha-quote', 'version': '1', 'host': 'alpha.example',
            'protocol': 'shopify_storefront_cart', 'api_version': '2025-01', 'status': 'active',
            'destination_detail': 'city', 'currency': 'USD', 'max_quantity': 2,
            'review': {'basis': 'Synthetic store for offline tests', 'evidence': ['https://alpha.example/'],
                       'reviewed_at': '2026-11-26'}}
    rule.update(changes)
    return rule


def quoting(**changes):
    source = alpha()
    source['quote_capabilities'] = [route(**changes)]
    return source


def rule_of(**changes):
    return QuoteCapability.model_validate(route(**changes))


def body(**changes):
    return storefront_body(rule_of(**changes), '4101', 1, city='Beirut')


def reply(amount='38.22', fee='5.00'):
    return json.dumps({'data': {'cartCreate': {'cart': {
        'lines': {'nodes': [{'quantity': 1, 'merchandise': {'id': 'gid://shopify/ProductVariant/4101',
                                                            'availableForSale': True},
                             'cost': {'totalAmount': {'amount': amount, 'currencyCode': 'USD'}}}]},
        'deliveryGroups': {'nodes': [{'deliveryOptions': [
            {'handle': 'std', 'title': 'Standard', 'estimatedCost': {'amount': fee, 'currencyCode': 'USD'}}]}]}},
        'userErrors': [], 'warnings': []}}}).encode()


class QuoteGateTests(GateCase):
    def setUp(self):
        super().setUp()
        self.use_registry([quoting()])
        self.reset()

    def check_request(self, method, url, content, headers):
        # Send-time invariant for this file: a plain GET of an allowed endpoint, or exactly the
        # reviewed quote POST of an active route from this thread's latest registry read.
        if method == 'POST':
            source = json.loads(self.path.read_text(encoding='utf-8'))['sources'][0]
            rules = [QuoteCapability.model_validate(r) for r in source.get('quote_capabilities', ())]
            ok = any(r.status == 'active' and storefront_request_allowed(r, url, content) for r in rules)
            extra = set(headers) - QUOTE_HEADERS
            if not ok or (extra and extra != {'x-shopify-storefront-access-token'}) or 'cookie' in headers:
                self.violations.append(('request', method, url, sorted(headers)))
            return
        super().check_request(method, url, content, headers)

    def quote(self, payload=None, ref=REF, budget=None, source='alpha'):
        return self.gate.quote(budget or self.run, source, ref, body() if payload is None else payload)

    def test_an_active_route_sends_exactly_one_reviewed_post_and_no_cookie(self):
        self.transport.route(QUOTE, respond(200, reply(), 'application/json',
                                            headers=[('Set-Cookie', 'cart=abc; Path=/')]))
        outcome = self.quote()
        self.assertOk(outcome)
        [sent] = self.transport.requests
        self.assertEqual((sent.method, sent.url, sent.content), ('POST', QUOTE, body()))
        self.assertEqual(sent.headers['content-type'], 'application/json')
        self.assertNotIn('cookie', sent.headers)
        self.assertEqual((outcome.attempts, outcome.final_url, outcome.status_code), (1, QUOTE, 200))
        self.assertEqual(len(outcome.response_sha256), 64)
        self.assertEqual(self.run.commits, 1)
        # A second quote carries nothing from the first reply.
        self.assertOk(self.quote())
        self.assertNotIn('cookie', self.transport.requests[1].headers)

    def test_a_route_pending_its_live_check_or_disabled_sends_nothing(self):
        for status in ('pending_live_check', 'disabled'):
            with self.subTest(status):
                self.use_registry([quoting(status=status)])
                self.reset()
                self.assertRefused(self.quote(), 'BLOCKED', 'blocked:url')
                self.assertNothingSent()
        self.reset()
        self.assertRefused(self.quote(ref=CapabilityRef(capability_id='alpha-quote', version='2')), 'BLOCKED')
        self.assertRefused(self.quote(ref=CapabilityRef(capability_id='other', version='1')), 'BLOCKED')
        self.assertNothingSent()

    def test_the_real_itworks_route_ships_switched_off(self):
        policy = SourcePolicy.model_validate(get_active_source('itworks'))
        [rule] = policy.quote_capabilities
        self.assertEqual((rule.status, rule.destination_detail, rule.host),
                         ('pending_live_check', 'city', 'shop.itworkslb.net'))
        self.assertEqual(active_quote_routes(DEFAULT_REGISTRY), {})
        gate = SourceGate._for_tests(DEFAULT_REGISTRY, transport=self.transport.mock, resolver=self.resolver,
                                     clock=self.clock.monotonic, wall_clock=self.clock.now, sleep=self.sleep)
        outcome = gate.quote(self.run, 'itworks', CapabilityRef(capability_id='itworks-delivery-quote', version='1'),
                             storefront_body(rule, '51991159963879', 1, city='Beirut'))
        self.assertRefused(outcome, 'BLOCKED', 'blocked:url')
        self.assertNothingSent()

    def test_any_other_request_shape_is_refused_before_anything_is_sent(self):
        good = json.loads(body())
        lines = good['variables']['input']['lines']
        address = good['variables']['input']['buyerIdentity']['deliveryAddressPreferences'][0]['deliveryAddress']

        def variant(change):
            data = deepcopy(good)
            change(data)
            return json.dumps(data, separators=(',', ':')).encode()

        bad = {
            'checkout document': variant(lambda d: d.update(query=STOREFRONT_DOCUMENT.replace(
                'cartCreate', 'checkoutCreate'))),
            'checkout url asked for': variant(lambda d: d.update(query=STOREFRONT_DOCUMENT.replace(
                'cart { ', 'cart { checkoutUrl ', 1))),
            'second mutation': variant(lambda d: d.update(query=STOREFRONT_DOCUMENT + ' mutation X { cartLinesAdd }')),
            'note on the cart': variant(lambda d: d['variables']['input'].update(note='hello')),
            'buyer email': variant(lambda d: d['variables']['input']['buyerIdentity'].update(email='a@b.example')),
            'two lines': variant(lambda d: d['variables']['input'].update(lines=lines * 2)),
            'quantity above the route': variant(lambda d: d['variables']['input']['lines'][0].update(quantity=3)),
            'quantity zero': variant(lambda d: d['variables']['input']['lines'][0].update(quantity=0)),
            'quantity text': variant(lambda d: d['variables']['input']['lines'][0].update(quantity='1')),
            'product instead of variant': variant(lambda d: d['variables']['input']['lines'][0].update(
                merchandiseId='gid://shopify/Product/4101')),
            'street on a city route': variant(lambda d: d['variables']['input']['buyerIdentity'][
                'deliveryAddressPreferences'][0]['deliveryAddress'].update(address1='Synthetic Street 1')),
            'phone in the address': variant(lambda d: d['variables']['input']['buyerIdentity'][
                'deliveryAddressPreferences'][0]['deliveryAddress'].update(phone='+961000000')),
            'no city': variant(lambda d: d['variables']['input']['buyerIdentity'][
                'deliveryAddressPreferences'][0]['deliveryAddress'].pop('city')),
            'null city': variant(lambda d: d['variables']['input']['buyerIdentity'][
                'deliveryAddressPreferences'][0]['deliveryAddress'].update(city=None)),
            'markup in the city': variant(lambda d: d['variables']['input']['buyerIdentity'][
                'deliveryAddressPreferences'][0]['deliveryAddress'].update(city='<b>Beirut</b>')),
            'another country': variant(lambda d: d['variables']['input']['buyerIdentity'].update(countryCode='US')),
            'duplicate key': body().replace(b'{"query":', b'{"query":"x","query":', 1),
            'not JSON': b'mutation { cartCreate }',
            'empty': b'',
            'too large': variant(lambda d: d['variables']['input']['buyerIdentity']['deliveryAddressPreferences'][0][
                'deliveryAddress'].update(city='B' * 5000)),
        }
        self.assertEqual(address, {'country': 'Lebanon', 'city': 'Beirut'})
        for name, payload in bad.items():
            with self.subTest(name):
                self.reset()
                self.assertRefused(self.quote(payload), 'BLOCKED', 'blocked:url')
                self.assertNothingSent()

    def test_the_body_builder_refuses_out_of_scope_inputs(self):
        city, street = rule_of(), rule_of(destination_detail='street')
        for call in (lambda: storefront_body(city, 'sku-1', 1, city='Beirut'),
                     lambda: storefront_body(city, '4101', 3, city='Beirut'),
                     lambda: storefront_body(city, '4101', 1, city='Beirut', street='Synthetic Street 1'),
                     lambda: storefront_body(street, '4101', 1, city='Beirut'),
                     lambda: storefront_body(street, '4101', 1, city='Beirut', street='x‮y'),
                     lambda: storefront_body(city, '4101', 1, city='')):
            with self.assertRaises(ValueError):
                call()
        sent = json.loads(storefront_body(street, '4101', 2, city='Beirut', street='Synthetic  Street 1',
                                          details='Floor 2'))
        self.assertEqual(sent['variables']['input']['buyerIdentity']['deliveryAddressPreferences'][0][
            'deliveryAddress'], {'country': 'Lebanon', 'city': 'Beirut', 'address1': 'Synthetic Street 1',
                                 'address2': 'Floor 2'})

    def test_a_redirect_is_refused_not_followed_and_failures_are_not_retried(self):
        cases = {
            'redirect': (redirect('https://alpha.example/checkouts/1'), 'BLOCKED'),
            'rate limited': (respond(429, b'{}', 'application/json'), 'RATE_LIMIT'),
            'server error': (respond(503, b'{}', 'application/json'), 'NETWORK_ERROR'),
            'refused': (respond(401, b'{}', 'application/json'), 'HTTP_ERROR'),
            'html': (respond(200, b'<html></html>', 'text/html'), 'INVALID_SCHEMA'),
            'over the quote cap': (json_ok(b'"' + b'a' * (300 * 1024) + b'"'), 'BLOCKED'),
        }
        for name, (step, code) in cases.items():
            with self.subTest(name):
                self.reset()
                self.transport.route(QUOTE, step)
                outcome = self.quote()
                self.assertEqual(outcome.errors[0].code, code)
                self.assertIsNone(outcome.body)
                self.assertEqual(self.transport.urls, [QUOTE])
                self.assertEqual(self.sleep.calls, [])

    def test_the_run_budget_and_workload_limits_apply(self):
        self.assertRefused(self.quote(budget=self.budget('quote-x', max_calls=0)), 'BUDGET_EXHAUSTED')
        self.assertEqual(self.transport.requests, [])
        self.use_registry([quoting() | {'limits': {'requests_per_minute': 6, 'concurrency': 1,
                                                   'max_requests_per_run': 1}}])
        self.reset()
        self.transport.route(QUOTE, json_ok(reply()))
        self.assertOk(self.quote())
        self.assertEqual(self.quote().errors[0].code, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(self.transport.requests), 1)

    def test_a_route_that_needs_a_token_reads_it_from_the_environment_only(self):
        self.use_registry([quoting(token_env='TECHSCOUT_ALPHA_STOREFRONT_TOKEN')])
        self.reset()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TECHSCOUT_ALPHA_STOREFRONT_TOKEN', None)
            self.assertRefused(self.quote(), 'SOURCE_DISABLED')
            self.assertNothingSent()
        self.reset()
        self.transport.route(QUOTE, json_ok(reply()))
        with mock.patch.dict(os.environ, {'TECHSCOUT_ALPHA_STOREFRONT_TOKEN': 'public0123456789abcdef'}):
            self.assertOk(self.quote())
        self.assertEqual(self.transport.requests[0].headers['x-shopify-storefront-access-token'],
                         'public0123456789abcdef')

    def test_reads_still_exclude_cart_checkout_and_the_quote_endpoint(self):
        self.reset()
        for url in (QUOTE, 'https://alpha.example/cart', 'https://alpha.example/checkout',
                    'https://alpha.example/cart/add.js', 'https://alpha.example/account/login'):
            with self.subTest(url):
                self.assertRefused(self.fetch('alpha', url, expect='json'), 'BLOCKED')
        self.assertRefused(self.gate.fetch(self.run, 'alpha', QUOTE, expect='json', capability=REF), 'BLOCKED')
        self.assertNothingSent()
        self.assertEqual(SourcePolicy.model_validate(get_active_source('alpha', self.path)).allowed_methods, ('GET',))

    def test_an_unsafe_quote_route_disables_the_source(self):
        mutations = {
            'another host': lambda r: r.update(host='beta.example'),
            'unknown protocol': lambda r: r.update(protocol='checkout'),
            'unknown status': lambda r: r.update(status='live'),
            'bad API version': lambda r: r.update(api_version='latest'),
            'quantity above five': lambda r: r.update(max_quantity=6),
            'no review evidence': lambda r: r['review'].update(evidence=[]),
            'a URL template': lambda r: r.update(path='/cart/{id}'),
            'token variable outside TECHSCOUT_': lambda r: r.update(token_env='PATH'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                source = quoting()
                mutate(source['quote_capabilities'][0])
                self.use_registry([source])
                self.reset()
                self.assertRefused(self.fetch('alpha', 'https://alpha.example/faq/'), 'SOURCE_DISABLED')
                self.assertRefused(self.quote(), 'SOURCE_DISABLED')
                self.assertNothingSent()
        source = quoting()
        source['quote_capabilities'].append(route())
        self.use_registry([source])
        self.reset()
        self.assertRefused(self.quote(), 'SOURCE_DISABLED')

    def test_quote_url_is_fixed_by_the_route(self):
        self.assertEqual(quote_url(rule_of()), QUOTE)
        self.assertFalse(storefront_request_allowed(rule_of(), QUOTE.replace('2025-01', '2024-10'), body()))
        self.assertTrue(storefront_request_allowed(rule_of(), QUOTE, body()))
