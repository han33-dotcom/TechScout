"""Pre-order delivery quotes for buyer chats (#186, and #187 box 2), offline.

Every seller here is synthetic. Its Shopify Storefront replies are scripted and go through the
real SourceGate, a fixture ledger on the test database, and the real accounts and chat API.
Addresses come from tests/fixtures/accounts.json and are invented. Nothing reaches a retailer,
a model or a live service.

Chat (the synthetic workspace's latest search, for one laptop delivered to Beirut, maximum USD 950):
- Book-Local: verified at USD 930 (920 + 10 published delivery).
- City-Shop: USD 915. Its delivery fee is unpublished, and its quote route needs the city only.
- Street-A: USD 905 and Street-B: USD 900. Their delivery fees are unpublished, and their quote
  routes need a street address.
- No-Route: USD 899. Its delivery fee is unpublished, and it has no reviewed quote route.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import httpx2
from sqlalchemy import func, select

from app import contracts_v2 as v
from app import delivery_quotes as dq
from app.accounting import BudgetDenied, Ledger
from app.buyer_accounts import BuyerAccounts
from app.buyer_runs import session_hash
from app.delivery_quotes import QuoteService, wanted_quotes
from app.quote_capabilities import active_quote_routes
from app.sources import SourceGate, SourceLimiter
from app.storage import buyer_quote_plans, buyer_quotes, buyer_runs
import test_buyer_api as api_fixtures
from test_buyer_accounts import A, FAMILY, HOME, OFFICE, Browser
from test_contracts_v2 import fixture
from test_quote_gate import route
from test_source_gate import respond

PUBLIC = '104.18.0.10'
SELLERS = {  # name: (price, Shopify variant, quote route detail or None)
    'City-Shop': ('915', '4103', 'city'),
    'Street-A': ('905', '4101', 'street'),
    'Street-B': ('900', '4102', 'street'),
    'No-Route': ('899', '4104', None),
}


def slug(name):
    return name.lower()


def source_of(name):
    return 'source-' + slug(name)


def host_of(name):
    return slug(name) + '.example'


def registry_entry(name, detail):
    host = host_of(name)
    return {'id': source_of(name), 'name': name, 'origin': f'https://{host}', 'status': 'active',
            'reviewed_at': '2026-11-26',
            'admission': {'basis': 'Synthetic store for offline tests', 'evidence': [f'https://{host}/'],
                          'reviewed_at': '2026-11-26T00:00:00Z', 'scope': 'Synthetic quotes only'},
            'allowed_endpoints': [f'https://{host}/faq'],
            'limits': {'requests_per_minute': 6, 'concurrency': 1, 'max_requests_per_run': 6},
            'quote_capabilities': [route(capability_id=source_of(name) + '-quote', host=host,
                                         destination_detail=detail, max_quantity=1)]}


def clone_offer(base, name, price, variant, arrival=None, shipping=None):
    """A Lebanese seller of Book-Local's exact configuration whose delivery fee is unpublished, or
    published only under the given shipping charge's terms."""
    text = json.dumps(base).replace('book-local', slug(name)).replace('Book-Local', name)
    text = text.replace(f'"sku-{slug(name)}"', f'"{variant}"')
    data = json.loads(text)
    data['price']['value']['amount'] = price
    data['costs'] = [charge for charge in data['costs'] if charge['kind'] != 'shipping'] + [
        {'schema_version': 2, 'leg': 'order', 'kind': 'shipping', 'state': 'unknown',
         'reason': 'Delivery depends on the destination'} if shipping is None else
        {'schema_version': 2, 'leg': 'order', 'kind': 'shipping', 'state': 'additional',
         'evidence_ids': [f'{slug(name)}:costs.order.shipping'], **shipping}]
    if shipping is None:
        data['evidence'] = [e for e in data['evidence'] if e['field_path'] != 'costs.order.shipping']
    if arrival is not None:
        record = dict(data['evidence'][0], evidence_id=f'{slug(name)}:arrival', field_path='arrival',
                      scope='variant', route_id=None)
        data['evidence'].append(record)
        data['arrival'] = {'state': 'known', 'value': arrival, 'evidence_ids': [record['evidence_id']]}
    return data


def chat_snapshot(suffix, *, deadline=None, arrival=None, mode='delivery', street_a=None):
    """street_a: optional 'reasons' and 'shipping' for Street-A's uncertain result."""
    raw = fixture('workspace')['current']
    base = raw['recommendations'][0]['primary']['offer']
    for name, (price, variant, _) in SELLERS.items():
        special = (street_a or {}) if name == 'Street-A' else {}
        raw['uncertain'].append({'status': 'uncertain', 'offer': clone_offer(
                                     base, name, price, variant, arrival, special.get('shipping')),
                                 'reasons': special.get('reasons', ['payable total is incomplete']),
                                 'cost': {'status': 'incomplete', 'known_subtotal': {'amount': price, 'currency': 'USD'},
                                          'missing': ['costs.order.shipping']}})
    # The fixture's CAD suitability need has no evidence on any synthetic offer; drop it so the
    # delivery fee is the only open fact.
    raw['request']['intent']['required_software'] = []
    if deadline is not None:
        raw['request']['fulfillment']['deadline'] = deadline
    text = json.dumps(raw).replace('run-new', f'run-q-{suffix}').replace('"needs-1"', f'"needs-q-{suffix}"')
    snapshot = v.RunSnapshot.model_validate_json(text)
    if mode == 'pickup':
        data = snapshot.model_dump(mode='json')
        data['request']['fulfillment'] = {'mode': 'pickup', 'destination': {'country': 'LB', 'city': 'Beirut'}}
        data.update(recommendations=[], status='partial')
        snapshot = v.RunSnapshot.model_validate(data)
    return snapshot


def refined(snapshot, city):
    """The chat's next search revision, for another city, with the same unpriced offers."""
    data = snapshot.model_dump(mode='json')
    data['run'] = dict(data['run'], run_id=data['run']['run_id'] + '-r', request_revision=3)
    data['request'] = dict(data['request'], revision=3)
    data['request']['fulfillment']['destination']['city'] = city
    data.update(recommendations=[], status='partial')
    return v.RunSnapshot.model_validate(data)


def storefront(amount, options=(('Standard', '5.00'),), *, variant, available=True, errors=(), groups=1):
    nodes = [{'deliveryOptions': [{'handle': option[0].lower(), 'title': option[0],
                                   'deliveryMethodType': option[2] if len(option) > 2 else 'SHIPPING',
                                   'estimatedCost': {'amount': option[1], 'currencyCode': 'USD'}}
                                  for option in options]}] * groups
    return json.dumps({'data': {'cartCreate': {'cart': {
        'lines': {'nodes': [{'quantity': 1, 'merchandise': {'id': f'gid://shopify/ProductVariant/{variant}',
                                                            'availableForSale': available},
                             'cost': {'totalAmount': {'amount': amount, 'currencyCode': 'USD'}}}]},
        'deliveryGroups': {'nodes': nodes}}, 'userErrors': list(errors), 'warnings': []}}}).encode()


class DeliveryQuoteTests(unittest.TestCase):
    def setUp(self):
        api_fixtures.BuyerApiTests.setUp(self)
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.registry = Path(directory) / 'sources.json'
        entries = [registry_entry(name, detail) for name, (_, _, detail) in SELLERS.items() if detail]
        self.registry.write_text(json.dumps({'schema_version': 1, 'live_release': {
            'admitted_source_ids': [e['id'] for e in entries]}, 'sources': entries}), encoding='utf-8')
        self.accounts = BuyerAccounts(self.service, quote_registry=self.registry)
        self.app.state.buyer_accounts = self.accounts
        self.browser = Browser(self, self.client)
        self.sent, self.script = [], {}
        gate = SourceGate._for_tests(self.registry, transport=httpx2.MockTransport(self.storefront),
                                     resolver=lambda host, port: [PUBLIC], limiter=SourceLimiter(),
                                     sleep=lambda seconds: None)
        self.ledger = Ledger(self.storage, 'quote-tests', fixture=True, clock=lambda: self.now)
        self.ledger.provision()
        self.quotes = QuoteService(self.accounts, gate=gate, ledger=self.ledger, registry=self.registry)

    # A scripted Shopify Storefront for every synthetic seller.

    def storefront(self, request):
        data = json.loads(request.content)
        line = data['variables']['input']['lines'][0]
        address = data['variables']['input']['buyerIdentity']['deliveryAddressPreferences'][0]['deliveryAddress']
        variant = line['merchandiseId'].rsplit('/', 1)[1]
        self.sent.append((request.url.host, variant, address))
        name = next(name for name, (_, sku, _) in SELLERS.items() if sku == variant)
        step = self.script.get(name) or respond(200, storefront(SELLERS[name][0], variant=variant),
                                                'application/json')
        return step(request)

    def to(self, name):
        return [address for host, _, address in self.sent if host == host_of(name)]

    def chat(self, suffix='1', token=None, **options):
        return self.store(chat_snapshot(suffix, **options), token=token)

    def store(self, snapshot, previous=None, token=None):
        with self.storage.transaction() as db:
            db.execute(buyer_runs.insert().values(
                run_id=snapshot.run.run_id, session_hash=session_hash(token or self.browser.token),
                request_id=snapshot.request.request_id, request_revision=snapshot.request.revision,
                status=snapshot.status, snapshot=snapshot.model_dump(mode='json'), answers=[], active_ms=0,
                previous_id=previous.run.run_id if previous else None))
        return snapshot

    def destination(self, snapshot):
        response = self.browser.client.get(f'/api/runs/{snapshot.run.run_id}/destination')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def select(self, snapshot, location, *names):
        response = self.browser.post(f'/api/runs/{snapshot.run.run_id}/destination', {
            'location_id': location['location_id'], 'recipients': ['retailer-' + slug(n) for n in names]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def signed_in(self, *locations):
        self.browser.register(A)
        return [self.browser.add(place) for place in locations]

    def settle(self):
        while self.quotes.step():
            pass

    @staticmethod
    def by_seller(state):
        return {quote['seller']: quote for quote in state['quotes']}

    @staticmethod
    def revised(state):
        return {offer['seller']: offer for offer in state['revised']['offers']}

    # Scenario 11: city-level quotes at once; name the sellers that need an address once.

    def test_a_city_level_quote_is_shown_without_asking_and_street_sellers_are_named_once(self):
        snapshot = self.chat()
        before = self.destination(snapshot)
        self.assertEqual([seller['name'] for seller in before['waiting']], ['Street-A', 'Street-B'])
        self.assertEqual((before['quotes'], before['revised'], before['selection']), ([], None, None))
        self.settle()
        self.assertEqual(self.to('City-Shop'), [{'country': 'Lebanon', 'city': 'Beirut'}])
        self.assertEqual(self.to('Street-A') + self.to('Street-B') + self.to('No-Route'), [])
        state = self.destination(snapshot)
        [quote] = state['quotes']
        self.assertEqual((quote['seller'], quote['status'], quote['destination']),
                         ('City-Shop', 'quoted', {'detail': 'city', 'city': 'Beirut', 'location_id': None,
                                                  'label': None}))
        revised = self.revised(state)
        self.assertEqual(revised['City-Shop']['after'], {'status': 'verified', 'total': {
            'amount': '920.00', 'currency': 'USD'}, 'budget_conversion': None})
        self.assertEqual((revised['City-Shop']['rank'], revised['Book-Local']['rank']), (1, 2))
        self.assertIn('City-Shop has the lowest verified total: USD 920', state['revised']['advice'])
        self.assertIn('Before the delivery quotes, Book-Local was the cheapest verified offer at USD 930',
                      state['revised']['advice'])
        # Waiting sellers stay named once, with their totals still unknown.
        self.assertEqual([seller['name'] for seller in state['waiting']], ['Street-A', 'Street-B'])
        self.assertEqual(revised['Street-A']['after']['status'], 'incomplete')

    # Scenarios 12-13 and #187 box 2: resume pending quotes for the chosen sellers, same chat.

    def test_a_selected_address_resumes_quotes_for_the_named_sellers_only_in_the_same_chat(self):
        home, _ = self.signed_in(HOME, OFFICE)
        snapshot = self.chat()
        self.settle()
        runs = self.count(buyer_runs)
        chosen = self.select(snapshot, home, 'Street-A')
        self.assertEqual(chosen['selection']['quote_state'], 'pending')
        self.settle()
        self.assertEqual(self.to('Street-A'), [{'country': 'Lebanon', 'city': 'Beirut', 'address1': HOME['street'],
                                                'address2': HOME['details']}])
        self.assertEqual(self.to('Street-B'), [])
        state = self.destination(snapshot)
        self.assertEqual(state['selection']['quote_state'], 'quoted')
        revised = self.revised(state)
        self.assertEqual({seller: revised[seller]['rank'] for seller in revised},
                         {'Book-Local': 3, 'Book-Forward': None, 'City-Shop': 2, 'Street-A': 1, 'Street-B': None,
                          'No-Route': None})
        self.assertEqual(revised['Street-A']['after']['total'], {'amount': '910.00', 'currency': 'USD'})
        self.assertEqual(revised['Street-B']['after']['status'], 'incomplete')
        self.assertTrue(state['revised']['advice'].startswith(
            'For delivery to Test home · Beirut, Street-A has the lowest verified total: USD 910.'))
        # Same chat and same search: no new run, and the search snapshot is unchanged.
        self.assertEqual(self.count(buyer_runs), runs)
        read = self.browser.client.get(f'/api/runs/{snapshot.run.run_id}').json()['current']
        self.assertEqual(v.RunSnapshot.model_validate(read), snapshot)

    def test_a_quote_records_source_time_price_included_charges_and_the_fee_once(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        self.script['Street-A'] = respond(200, storefront('905.00', (('Express', '12'), ('Standard', '5.0')),
                                                          variant='4101'), 'application/json')
        self.select(snapshot, home, 'Street-A')
        self.settle()
        quote = self.by_seller(self.destination(snapshot))['Street-A']
        self.assertEqual({key: quote[key] for key in ('status', 'capability', 'endpoint', 'quantity', 'included',
                                                      'item_price', 'fee', 'quoted_total', 'reason')},
                         {'status': 'quoted', 'capability': 'source-street-a-quote@1',
                          'endpoint': 'https://street-a.example/api/2025-01/graphql.json', 'quantity': 1,
                          'included': ['tax'], 'item_price': {'amount': '905.00', 'currency': 'USD'},
                          'fee': {'label': 'Standard', 'amount': {'amount': '5.0', 'currency': 'USD'}},
                          'quoted_total': {'amount': '910.00', 'currency': 'USD'}, 'reason': None})
        self.assertEqual(len(quote['response_sha256']), 64)
        self.assertEqual((quote['source_variant_id'], quote['destination']['location_id']), ('4101', home['location_id']))
        self.assertTrue(datetime.fromisoformat(quote['observed_at'].replace('Z', '+00:00')) >= datetime.fromisoformat(
            quote['requested_at'].replace('Z', '+00:00')))
        offer = next(item.offer for item in snapshot.uncertain if item.offer.seller.name == 'Street-A')
        quoted = dq.with_quote(offer, dq.DeliveryQuote.model_validate(quote))
        self.assertEqual([c.kind for c in quoted.costs].count('shipping'), 1)
        [evidence] = [e for e in quoted.evidence if e.field_path == 'costs.order.shipping']
        self.assertEqual((evidence.scope, evidence.route_id, evidence.response_sha256),
                         ('route', offer.route.route_id, quote['response_sha256']))

    def test_changing_the_destination_invalidates_the_quote_and_no_request_is_repeated(self):
        home, office = self.signed_in(HOME, OFFICE)
        snapshot = self.chat()
        self.select(snapshot, home, 'Street-A')
        self.settle()
        moved = self.select(snapshot, office, 'Street-A')
        self.assertEqual((moved['selection']['quote_state'], [q['seller'] for q in moved['quotes']]),
                         ('pending', ['City-Shop']))
        self.assertEqual(self.revised(moved)['Street-A']['after']['status'], 'incomplete')
        self.settle()
        self.assertEqual([address['address1'] for address in self.to('Street-A')], [HOME['street'], OFFICE['street']])
        self.assertEqual(self.by_seller(self.destination(snapshot))['Street-A']['destination']['label'], 'Test office')
        self.select(snapshot, home, 'Street-A')
        self.settle()
        self.assertEqual(len(self.to('Street-A')), 2)
        self.assertEqual(len(self.to('City-Shop')), 1)
        self.assertEqual(self.by_seller(self.destination(snapshot))['Street-A']['destination']['label'], 'Test home')

    # Scenarios 14-15: no usable quote, a conflicting quote, a declined address.

    def test_no_usable_quote_keeps_the_total_unknown_the_links_and_is_not_repeated(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        self.script['Street-B'] = respond(200, storefront('900', (), variant='4102', groups=0), 'application/json')
        self.select(snapshot, home, 'Street-A', 'Street-B')
        self.settle()
        state = self.destination(snapshot)
        quote = self.by_seller(state)['Street-B']
        self.assertEqual((quote['status'], quote['quoted_total'], quote['reason']),
                         ('unavailable', None, 'The seller could not deliver to this destination.'))
        self.assertEqual(state['selection']['quote_state'], 'partial')
        revised = self.revised(state)
        self.assertEqual(revised['Street-B']['after'], revised['Street-B']['before'])
        self.assertEqual(revised['Street-B']['listing_url'], 'https://street-b.example/street-b')
        self.assertIn(quote['reason'], revised['Street-B']['doubts'])
        self.assertEqual(revised['Street-A']['rank'], 1)
        self.assertIn('Street-B gave no usable delivery quote, so its total stays unknown.', state['revised']['advice'])
        for _ in range(3):
            self.quotes.step()
        self.assertEqual(len(self.to('Street-B')), 1)

    def test_a_failed_reply_is_unusable_and_is_not_retried(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        self.script['Street-A'] = respond(503, b'{}', 'application/json')
        self.select(snapshot, home, 'Street-A')
        self.settle()
        state = self.destination(snapshot)
        self.assertEqual((self.by_seller(state)['Street-A']['status'], state['selection']['quote_state']),
                         ('unavailable', 'unavailable'))
        self.assertEqual(len(self.to('Street-A')), 1)

    def test_a_conflicting_quote_keeps_the_total_unknown(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        self.script['Street-B'] = respond(200, storefront('950', variant='4102'), 'application/json')
        self.select(snapshot, home, 'Street-B')
        self.settle()
        state = self.destination(snapshot)
        quote = self.by_seller(state)['Street-B']
        self.assertEqual((quote['status'], quote['quoted_total'], quote['item_price']['amount']),
                         ('conflicting', None, '950'))
        self.assertEqual(quote['reason'], 'The pre-order step charged USD 950 for the item, but the listing shows '
                                          'USD 900. Its total stays unknown.')
        self.assertEqual(state['selection']['quote_state'], 'unavailable')
        revised = self.revised(state)
        self.assertEqual((revised['Street-B']['after']['status'], revised['Street-B']['rank']), ('incomplete', None))
        self.assertIn("Street-B's quote disagreed with its listing", state['revised']['advice'])

    def test_a_declined_address_sends_nothing_and_keeps_the_offers_and_links(self):
        snapshot = self.chat()
        declined = self.browser.post(f'/api/runs/{snapshot.run.run_id}/destination/decline').json()
        self.assertEqual(declined['selection']['quote_state'], 'none')
        self.settle()
        self.assertEqual([host for host, _, _ in self.sent], ['city-shop.example'])
        self.assertIsNone(self.accounts.release_address(snapshot.run, 'retailer-street-a'))
        state = self.destination(snapshot)
        revised = self.revised(state)
        for seller in ('Street-A', 'Street-B', 'No-Route'):
            self.assertEqual((revised[seller]['after']['status'], revised[seller]['rank']), ('incomplete', None))
            self.assertTrue(revised[seller]['listing_url'].startswith('https://'))
        self.assertEqual(revised['Book-Local']['rank'], 2)

    def test_a_named_seller_without_a_reviewed_route_is_never_sent_the_address(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        with mock.patch.object(self.accounts, 'release_address', wraps=self.accounts.release_address) as release:
            self.select(snapshot, home, 'No-Route')
            self.settle()
        release.assert_not_called()
        self.assertEqual(self.to('No-Route'), [])
        state = self.destination(snapshot)
        self.assertEqual((self.by_seller(state)['No-Route']['reason'], state['selection']['quote_state']),
                         (dq.NO_ROUTE, 'unavailable'))

    def test_an_address_in_another_city_is_not_sent(self):
        [family] = self.signed_in(FAMILY)
        snapshot = self.chat()
        self.select(snapshot, family, 'Street-A')
        self.settle()
        self.assertEqual(self.to('Street-A'), [])
        self.assertEqual(self.by_seller(self.destination(snapshot))['Street-A']['reason'], dq.OTHER_CITY)

    def test_a_delivery_estimate_never_satisfies_a_guaranteed_deadline(self):
        [home] = self.signed_in(HOME)
        by = datetime(2026, 11, 20, tzinfo=timezone.utc)
        self.script['Street-A'] = respond(200, storefront('905', (('Guaranteed next-day delivery', '5'),),
                                                          variant='4101'), 'application/json')
        ranks = {}
        for suffix, basis in (('e', 'estimate'), ('g', 'guaranteed')):
            snapshot = self.chat(suffix, deadline={'arrive_by': by.isoformat(), 'basis': 'guaranteed'}, arrival={
                'earliest': (by - timedelta(days=2)).isoformat(), 'latest': (by - timedelta(days=1)).isoformat(),
                'basis': basis})
            self.select(snapshot, home, 'Street-A')
            self.settle()
            revised = self.revised(self.destination(snapshot))['Street-A']
            self.assertEqual(revised['after']['status'], 'verified')
            ranks[basis] = (revised['rank'], revised['doubts'])
        # The quote's "Guaranteed next-day" wording sets no arrival: only the listing's window counts,
        # and an estimated window keeps the offer out of the ranking for a guaranteed deadline.
        self.assertEqual(ranks['estimate'], (None, ['deadline: required evidence is missing or conflicting']))
        self.assertEqual(ranks['guaranteed'], (1, []))

    def test_pickup_and_unfinished_searches_need_no_quote(self):
        routes = active_quote_routes(self.registry)
        self.assertEqual(set(routes), {source_of(n) for n, (_, _, d) in SELLERS.items() if d})
        self.assertEqual(wanted_quotes(chat_snapshot('p', mode='pickup'), None, routes), [])
        running = chat_snapshot('r').model_dump(mode='json')
        running.update(status='running', finished_at=None, stop_reason=None, recommendations=[], explanation=None)
        self.assertEqual(wanted_quotes(v.RunSnapshot.model_validate(running), None, routes), [])

    def test_an_interrupted_quote_is_settled_unusable_and_not_retried(self):
        snapshot = self.chat()
        self.quotes.plan()
        claimed = self.quotes._claim()
        self.assertEqual(claimed[4].status, 'requested')
        self.now += timedelta(seconds=61)
        self.settle()
        [quote] = self.destination(snapshot)['quotes']
        self.assertEqual((quote['status'], quote['reason']), ('unavailable', dq.INTERRUPTED))
        self.assertEqual(self.sent, [])

    def test_quotes_follow_the_chat_when_the_buyer_signs_in(self):
        snapshot = self.chat()
        self.settle()
        self.browser.register(A)
        self.assertEqual([q['seller'] for q in self.destination(snapshot)['quotes']], ['City-Shop'])

    def test_quotes_use_the_shared_ledger_and_a_refusal_is_paced_then_given_up_unsent(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        self.settle()
        attempts = self.ledger.snapshot()['attempts'].values()
        self.assertEqual([(a['source'], a['job'].startswith('quote:')) for a in attempts],
                         [('source-city-shop', True)])
        self.select(snapshot, home, 'Street-A')
        with mock.patch.object(Ledger, 'reserve', side_effect=BudgetDenied('Shared source workload limit')):
            for _ in range(dq.MAX_DEFERRALS):
                self.settle()
                self.assertEqual(self.destination(snapshot)['selection']['quote_state'], 'pending')
                self.now += dq.DEFER + timedelta(seconds=1)
            self.settle()
        self.assertEqual(self.to('Street-A'), [])
        state = self.destination(snapshot)
        self.assertEqual((self.by_seller(state)['Street-A']['reason'], state['selection']['quote_state']),
                         (dq.WORKLOAD, 'unavailable'))
        # Never sent, so a later choice may ask again.
        self.select(snapshot, home, 'Street-A')
        self.settle()
        self.assertEqual(len(self.to('Street-A')), 1)

    def test_a_busy_workload_limit_defers_the_quote_and_then_sends_it(self):
        snapshot = self.chat()
        real, calls = Ledger.reserve, []

        def busy_once(ledger, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise BudgetDenied('Shared source workload limit')
            return real(ledger, *args, **kwargs)
        with mock.patch.object(Ledger, 'reserve', autospec=True, side_effect=busy_once):
            self.settle()
            self.assertEqual((self.sent, self.destination(snapshot)['quotes'][0]['status']), ([], 'pending'))
            self.now += dq.DEFER + timedelta(seconds=1)
            self.settle()
        [quote] = self.destination(snapshot)['quotes']
        self.assertEqual((quote['status'], len(self.to('City-Shop'))), ('quoted', 1))

    def test_each_search_has_a_bounded_quote_allowance_across_destination_choices(self):
        home, office = self.signed_in(HOME, OFFICE)
        snapshot = self.chat()
        with mock.patch.object(dq, 'MAX_QUOTES_PER_SEARCH', 2):
            self.select(snapshot, home, 'Street-A', 'Street-B')
            self.settle()
            self.assertEqual(self.by_seller(self.destination(snapshot))['Street-B']['reason'], dq.OVER_LIMIT)
            # Choosing other addresses does not renew the allowance for the same search.
            self.select(snapshot, office, 'Street-A')
            self.settle()
            self.assertEqual(self.by_seller(self.destination(snapshot))['Street-A']['reason'], dq.OVER_LIMIT)
        self.assertEqual(sorted(host for host, _, _ in self.sent), ['city-shop.example', 'street-a.example'])

    def test_an_unsent_quote_is_reconsidered_when_the_search_changes(self):
        [family] = self.signed_in(FAMILY)
        snapshot = self.chat()
        self.select(snapshot, family, 'Street-A')
        self.settle()
        self.assertEqual(self.by_seller(self.destination(snapshot))['Street-A']['reason'], dq.OTHER_CITY)
        moved = self.store(refined(snapshot, 'Jounieh'), previous=snapshot)
        self.settle()
        self.assertEqual(self.to('Street-A'), [{'country': 'Lebanon', 'city': 'Jounieh', 'address1': FAMILY['street'],
                                                'address2': FAMILY['details']}])
        self.assertEqual([address['city'] for address in self.to('City-Shop')], ['Beirut', 'Jounieh'])
        state = self.destination(moved)
        self.assertEqual((self.by_seller(state)['Street-A']['status'], state['selection']['quote_state']),
                         ('quoted', 'quoted'))
        # Its price and stock evidence comes from the earlier search, so the quote cannot rank it.
        revised = self.revised(state)['Street-A']
        self.assertEqual((revised['rank'], revised['doubts'][0]), (None, dq.NOT_CURRENT))

    def test_a_quote_settles_the_fee_but_keeps_the_searchs_other_doubts(self):
        [home] = self.signed_in(HOME)
        doubt = 'The selected listing and configuration could not be rechecked.'
        snapshot = self.chat(street_a={'reasons': ['payable total is incomplete', doubt]})
        self.select(snapshot, home, 'Street-A')
        self.settle()
        revised = self.revised(self.destination(snapshot))['Street-A']
        self.assertEqual(revised['after']['total'], {'amount': '910.00', 'currency': 'USD'})
        self.assertEqual((revised['rank'], revised['doubts']), (None, [doubt]))

    def test_a_published_fee_that_does_not_apply_never_conflicts_with_a_quote(self):
        [home] = self.signed_in(HOME)
        for suffix, terms in (('t', {'conditions': ['city:Tripoli']}),
                              ('x', {'valid_until': '2026-11-16T12:00:40Z'})):
            with self.subTest(suffix):
                snapshot = self.chat(suffix, street_a={'shipping': {'amount': {'amount': '10', 'currency': 'USD'},
                                                                    **terms}})
                self.select(snapshot, home, 'Street-A')
                self.settle()
                state = self.destination(snapshot)
                self.assertEqual((self.by_seller(state)['Street-A']['status'], self.revised(state)['Street-A']['rank']),
                                 ('quoted', 1))

    def test_a_pending_quote_that_no_longer_applies_is_not_sent(self):
        snapshot = self.chat()
        self.quotes.plan()
        self.store(refined(snapshot, 'Jounieh'), previous=snapshot)
        self.quotes.execute()
        self.assertEqual(self.sent, [])
        self.settle()
        self.assertEqual([address['city'] for address in self.to('City-Shop')], ['Jounieh'])

    def test_a_malformed_reply_settles_only_that_quote(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        bad = json.loads(storefront('905', variant='4101'))
        bad['data']['cartCreate']['cart']['lines']['nodes'][0]['merchandise'] = 'not an object'
        self.script['Street-A'] = respond(200, json.dumps(bad).encode(), 'application/json')
        self.select(snapshot, home, 'Street-A', 'Street-B')
        self.settle()
        quotes = self.by_seller(self.destination(snapshot))
        self.assertEqual({seller: (quote['status'], quote['reason']) for seller, quote in quotes.items()},
                         {'City-Shop': ('quoted', None), 'Street-A': ('unavailable', dq.FAILED),
                          'Street-B': ('quoted', None)})

    def test_any_reply_shape_settles_the_quote_without_raising(self):
        offer = next(item.offer for item in chat_snapshot('u').uncertain if item.offer.seller.name == 'Street-A')
        rule = active_quote_routes(self.registry)['source-street-a']
        quote = dq.new_quote(dq.Wanted(offer, dq.QuoteDestination(detail='city', city='Beirut'), rule),
                             chat_snapshot('u').run, self.now)
        good = json.loads(storefront('905', variant='4101'))
        cart = ['data', 'cartCreate', 'cart']

        def reply(path, value):
            data = json.loads(json.dumps(good))
            node = data
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            return json.dumps(data).encode()
        bodies = [b'null', b'[]', b'"text"', b'{"data": null}', reply(['data', 'cartCreate'], 'x'),
                  reply([*cart, 'lines'], []), reply([*cart, 'lines', 'nodes'], ['x']),
                  reply([*cart, 'lines', 'nodes', 0, 'cost'], 'x'),
                  reply([*cart, 'deliveryGroups', 'nodes'], ['x']),
                  reply([*cart, 'deliveryGroups', 'nodes', 0, 'deliveryOptions'], ['x']),
                  reply([*cart, 'deliveryGroups', 'nodes', 0, 'deliveryOptions', 0, 'estimatedCost'], 'x'),
                  reply(['data', 'cartCreate', 'userErrors'], ['x']),
                  reply([*cart, 'lines', 'nodes', 0, 'cost', 'totalAmount', 'amount'], 'NaN')]
        for body in bodies:
            with self.subTest(body=body[:60]):
                outcome = SimpleNamespace(errors=(), body=body, observed_at=self.now, response_sha256='a' * 64)
                result = dq.read_storefront_reply(outcome, quote, offer, rule)
                self.assertEqual((result.status, result.quoted_total), ('unavailable', None))

    def test_a_free_pickup_option_is_never_the_delivery_fee(self):
        snapshot = self.chat()
        self.script['City-Shop'] = respond(200, storefront('915', (
            ('Pickup in store', '0.00', 'PICK_UP'), ('Standard', '5.00', 'SHIPPING')), variant='4103'),
            'application/json')
        self.settle()
        [quote] = self.destination(snapshot)['quotes']
        self.assertEqual((quote['fee']['label'], quote['fee']['amount']['amount']), ('Standard', '5.00'))
        only = self.chat('2')
        self.script['City-Shop'] = respond(200, storefront('915', (('Pickup in store', '0.00', 'PICK_UP'),),
                                                           variant='4103'), 'application/json')
        self.settle()
        [quote] = self.destination(only)['quotes']
        self.assertEqual((quote['status'], quote['fee']), ('unavailable', None))

    def test_street_addresses_stay_out_of_storage_logs_and_responses(self):
        [home] = self.signed_in(HOME)
        snapshot = self.chat()
        with self.assertLogs('techscout', level=logging.DEBUG) as logs:
            logging.getLogger('techscout').debug('capture start')
            self.select(snapshot, home, 'Street-A', 'Street-B')
            self.settle()
        self.assertEqual(self.to('Street-A')[0]['address1'], HOME['street'])
        state = self.destination(snapshot)
        with self.storage.engine.connect() as db:
            stored = [dict(row) for row in db.execute(select(buyer_quotes)).mappings()]
            stored += [dict(row) for row in db.execute(select(buyer_quote_plans)).mappings()]
        texts = [json.dumps(stored, default=str), json.dumps(state), '\n'.join(logs.output),
                 self.browser.client.get(f'/api/history/{snapshot.run.run_id}/export').text,
                 repr(self.accounts.release_address(snapshot.run, 'retailer-street-a'))]
        for text in texts:
            for secret in (HOME['street'], HOME['details']):
                self.assertNotIn(secret, text)

    def count(self, table):
        with self.storage.engine.connect() as db:
            return db.execute(select(func.count()).select_from(table)).scalar_one()


if __name__ == '__main__':
    unittest.main()
