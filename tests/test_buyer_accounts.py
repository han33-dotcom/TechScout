"""TechScout accounts, private saved locations and per-chat destinations (#187).

Offline only: synthetic accounts and invented addresses from tests/fixtures/accounts.json,
and the synthetic v2 workspace fixture as the chat. No retailer, model or quote is called.
"""
from datetime import timedelta
import json
import logging
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from app.buyer_accounts import (
    BuyerAccounts, PreciseAddress, chat_sellers, hash_password, normalize_username, verify_password,
)
from app.buyer_runs import RunError, session_hash
from app.contracts_v2 import ComparisonWorkspace
from app.storage import (
    buyer_accounts, buyer_attempts, buyer_destinations, buyer_locations, buyer_runs, buyer_sessions,
    buyer_signins,
)
import test_buyer_api as api_fixtures
from test_contracts_v2 import fixture

DATA = json.loads((Path(__file__).parent / 'fixtures' / 'accounts.json').read_text(encoding='utf-8'))
A, B, NEW = DATA['accounts']
HOME, OFFICE, FAMILY = DATA['locations']
ORIGIN = 'https://testserver'


def seed_chat(storage, token, suffix):
    """Store the synthetic two-revision workspace as a chat owned by this session token.

    The current revision shows two Lebanese sellers (Book-Local verified, Book-Other
    provisional) and one overseas uncertain seller. No live result is claimed."""
    raw = fixture('workspace')
    raw['current']['provisional'] = [raw['previous']['provisional'][0]]
    text = (json.dumps(raw).replace('run-new', f'run-new-{suffix}').replace('run-old', f'run-old-{suffix}')
            .replace('"needs-1"', f'"needs-1-{suffix}"'))
    workspace = ComparisonWorkspace.model_validate_json(text)
    with storage.transaction() as db:
        for snapshot, previous in ((workspace.previous, None), (workspace.current, workspace.previous.run.run_id)):
            db.execute(insert(buyer_runs).values(
                run_id=snapshot.run.run_id, session_hash=session_hash(token), request_id=snapshot.request.request_id,
                request_revision=snapshot.request.revision, status=snapshot.status,
                snapshot=snapshot.model_dump(mode='json'), answers=[], previous_id=previous, active_ms=0))
    return workspace


class Browser:
    """One browser with its own cookie jar and current CSRF token."""
    def __init__(self, test, client=None):
        self.test = test
        self.client = client or test.enterContext(TestClient(test.app, base_url=ORIGIN))
        self.refresh()

    def refresh(self):
        response = self.client.get('/api/session')
        self.test.assertEqual(response.status_code, 200, response.text)
        self.info = response.json()

    @property
    def token(self):
        return self.client.cookies.get('techscout_session')

    @property
    def headers(self):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.info['csrf_token']}

    def post(self, path, body=None):
        return self.client.post(path, json={} if body is None else body, headers=self.headers)

    def issued(self, response, status=200):
        self.test.assertEqual(response.status_code, status, response.text)
        self.info = response.json()
        return response

    def register(self, account):
        return self.issued(self.post('/api/account/register', account), 201)

    def sign_in(self, account):
        return self.issued(self.post('/api/account/sign-in', account))

    def add(self, location):
        response = self.post('/api/account/locations', location)
        self.test.assertEqual(response.status_code, 201, response.text)
        return response.json()


class BuyerAccountTests(unittest.TestCase):
    def setUp(self):
        api_fixtures.BuyerApiTests.setUp(self)
        self.accounts = self.app.state.buyer_accounts
        self.browser = Browser(self, self.client)

    def count(self, table):
        with self.storage.engine.connect() as db:
            return db.execute(select(func.count()).select_from(table)).scalar_one()

    # Passwords

    def test_passwords_are_salted_scrypt_hashes_verified_in_constant_time(self):
        first, second = hash_password(A['password']), hash_password(A['password'])
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith('scrypt$16384$8$1$'))
        self.assertNotIn(A['password'], first)
        self.assertTrue(verify_password(A['password'], first))
        self.assertFalse(verify_password(B['password'], first))
        for stored in ('', 'plain', 'scrypt$1$1$1$AA==$AA==', 'pbkdf2$16384$8$1$AA==$AA==',
                       first.replace('$8$', '$99$'), first[:-3] + '!!=', 'scrypt$x$8$1$a$b'):
            self.assertFalse(verify_password(A['password'], stored), stored)
        self.browser.register(A)
        with self.storage.engine.connect() as db:
            row = db.execute(select(buyer_accounts)).mappings().one()
        self.assertEqual(row['username'], A['username'])
        self.assertTrue(verify_password(A['password'], row['password_hash']))
        self.assertNotIn(A['password'], json.dumps(dict(row), default=str))

    def test_registration_rules_never_echo_the_submitted_password(self):
        weak = [('Test-Buyer-A', 'short'), (A['username'], 'x' * 10), (A['username'], 'test-buyer-a-123'),
                (A['username'], 'password123'), ('a', A['password']), ('bad name', A['password']),
                ('ünïcode', A['password']), (A['username'], 'p' * 129)]
        for username, password in weak:
            with self.subTest(username=username):
                response = self.browser.post('/api/account/register', {'username': username, 'password': password})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(password, response.text)
        for body in ({'username': A['username']}, {'username': 5, 'password': A['password']},
                     {'username': A['username'], 'password': A['password'], 'admin': True}):
            response = self.browser.post('/api/account/register', body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(A['password'], response.text)
            self.assertEqual(response.json(), {'detail': 'The account request is not valid'})
        self.assertEqual(self.count(buyer_accounts), 0)
        self.assertEqual(normalize_username('  Test-Buyer-A '), 'test-buyer-a')

    def test_registration_signs_in_with_a_rotated_session_and_keeps_the_anonymous_chat(self):
        anonymous = self.browser.token
        chat = seed_chat(self.storage, anonymous, 'anon')
        before = self.browser.client.get('/api/runs/' + chat.current.run.run_id).json()
        response = self.browser.register({'username': '  Test-Buyer-A ', 'password': A['password']})
        self.assertEqual(response.json()['account'], {'username': A['username']})
        cookie = response.headers['set-cookie']
        for flag in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/'):
            self.assertIn(flag, cookie)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotEqual(self.browser.token, anonymous)
        self.assertNotEqual(self.browser.info['csrf_token'], self.headers['X-CSRF-Token'])
        # The same chat, its earlier revision and offers, now under the signed-in session.
        after = self.browser.client.get('/api/runs/' + chat.current.run.run_id)
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json(), before)
        self.assertEqual(after.json()['previous']['run']['run_id'], chat.previous.run.run_id)
        self.assertEqual([item['run_id'] for item in self.browser.client.get('/api/history').json()['items']],
                         [chat.current.run.run_id])
        self.assertEqual(self.browser.client.get('/api/session').json()['account'], {'username': A['username']})
        # The anonymous cookie no longer works anywhere.
        stale = self.enterContext(TestClient(self.app, base_url=ORIGIN))
        stale.cookies.set('techscout_session', anonymous)
        self.assertEqual(stale.get('/api/runs/' + chat.current.run.run_id).status_code, 401)
        with self.storage.engine.connect() as db:
            self.assertIsNone(db.execute(select(buyer_sessions).where(
                buyer_sessions.c.session_hash == session_hash(anonymous))).first())
            self.assertEqual(db.execute(select(buyer_signins.c.session_hash)).scalar_one(),
                             session_hash(self.browser.token))

    def test_taken_username_is_refused_without_changing_the_existing_account(self):
        self.browser.register(A)
        other = Browser(self)
        response = other.post('/api/account/register', {'username': A['username'].upper(), 'password': B['password']})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(B['password'], response.text)
        self.assertEqual(self.count(buyer_accounts), 1)
        other.sign_in(A)

    def test_sign_in_failures_are_identical_for_unknown_names_and_wrong_passwords(self):
        Browser(self).register(A)
        responses = [self.browser.post('/api/account/sign-in', body) for body in (
            {'username': A['username'], 'password': B['password']},
            {'username': 'test-nobody', 'password': B['password']},
            {'username': 'Not a valid name!', 'password': B['password']},
            {'username': A['username'], 'password': ''})]
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {'detail': 'The username or password is incorrect'})
        self.assertIsNone(self.browser.client.get('/api/session').json()['account'])

    def test_sign_in_is_throttled_per_name_even_for_names_without_accounts(self):
        Browser(self).register(A)
        for username in (A['username'], 'test-nobody'):
            browser = Browser(self)
            for _ in range(5):
                self.assertEqual(browser.post('/api/account/sign-in', {
                    'username': username, 'password': B['password']}).status_code, 401)
            refused = browser.post('/api/account/sign-in', {'username': username, 'password': A['password']})
            self.assertEqual(refused.status_code, 429, username)
            self.assertEqual(refused.json()['detail'], 'Too many attempts. Wait a few minutes before trying again.')
        # Another browser is refused for the same name; the window then expires.
        late = Browser(self)
        self.assertEqual(late.post('/api/account/sign-in', A).status_code, 429)
        self.now += timedelta(minutes=16)
        late.sign_in(A)
        with self.storage.engine.connect() as db:
            keys = db.execute(select(buyer_attempts.c.key)).scalars().all()
        self.assertTrue(all(A['username'] not in key and 'nobody' not in key for key in keys))

    def test_sign_in_is_throttled_per_browser_session_across_names(self):
        for index in range(10):
            self.assertEqual(self.browser.post('/api/account/sign-in', {
                'username': f'test-guess-{index}', 'password': B['password']}).status_code, 401)
        self.assertEqual(self.browser.post('/api/account/sign-in', {
            'username': 'test-guess-x', 'password': B['password']}).status_code, 429)

    def test_registration_is_throttled_per_session_and_globally(self):
        accounts = BuyerAccounts(self.service, registrations_per_session=2, registrations_per_hour=4)
        accounts.register(Browser(self).token, 'test-taken', A['password'])
        token = self.browser.token
        for _ in range(2):
            with self.assertRaises(RunError) as taken:
                accounts.register(token, 'test-taken', A['password'])
            self.assertEqual(taken.exception.status, 409)
        with self.assertRaises(RunError) as refused:
            accounts.register(token, 'test-third', A['password'])
        self.assertEqual(refused.exception.status, 429)
        accounts.register(Browser(self).token, 'test-fourth', A['password'])
        with self.assertRaises(RunError) as busy:
            accounts.register(Browser(self).token, 'test-fifth', A['password'])
        self.assertEqual(busy.exception.status, 429)
        self.assertEqual(self.count(buyer_accounts), 2)
        with self.assertRaises(ValueError):
            BuyerAccounts(self.service, name_attempts=0)

    def test_sign_out_ends_the_session_and_its_chats_but_keeps_saved_locations(self):
        self.browser.register(A)
        self.browser.add(HOME)
        signed_in = self.browser.token
        chat = seed_chat(self.storage, signed_in, 'out')
        response = self.browser.issued(self.browser.post('/api/account/sign-out'))
        self.assertIsNone(response.json()['account'])
        self.assertNotEqual(self.browser.token, signed_in)
        self.assertEqual(self.browser.client.get('/api/runs/' + chat.current.run.run_id).status_code, 404)
        self.assertEqual(self.browser.client.get('/api/account/locations').status_code, 401)
        stale = self.enterContext(TestClient(self.app, base_url=ORIGIN))
        stale.cookies.set('techscout_session', signed_in)
        self.assertEqual(stale.get('/api/account/locations').status_code, 401)
        self.assertEqual(self.count(buyer_runs), 0)
        self.assertEqual(self.count(buyer_signins), 0)
        # A later sign-in on a new session finds the saved location again.
        self.browser.sign_in(A)
        self.assertEqual([item['label'] for item in self.browser.client.get('/api/account/locations').json()['items']],
                         [HOME['label']])

    def test_signing_in_as_another_account_never_carries_the_previous_accounts_chats(self):
        self.browser.register(A)
        chat = seed_chat(self.storage, self.browser.token, 'a')
        Browser(self).register(B)
        self.browser.sign_in(B)
        self.assertEqual(self.browser.client.get('/api/runs/' + chat.current.run.run_id).status_code, 404)
        self.assertEqual(self.browser.client.get('/api/history').json()['items'], [])
        self.assertEqual(self.count(buyer_runs), 0)

    def test_account_mutations_require_same_origin_and_csrf(self):
        self.browser.register(A)
        location = self.browser.add(HOME)
        chat = seed_chat(self.storage, self.browser.token, 'csrf')
        run = '/api/runs/' + chat.current.run.run_id
        calls = [('/api/account/register', B), ('/api/account/sign-in', A), ('/api/account/sign-out', {}),
                 ('/api/account/locations', OFFICE), (f"/api/account/locations/{location['location_id']}/delete", {}),
                 (run + '/destination', {'location_id': location['location_id'], 'recipients': ['retailer-book-local']}),
                 (run + '/destination/decline', {})]
        for path, body in calls:
            for headers in ({}, {'Origin': 'https://evil.example', 'X-CSRF-Token': self.browser.info['csrf_token']},
                            {'Origin': ORIGIN, 'X-CSRF-Token': 'wrong'}, {'Origin': ORIGIN}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.browser.client.post(path, json=body, headers=headers).status_code, 403)
        self.assertEqual(self.browser.client.get('/api/session').json()['account'], {'username': A['username']})
        self.assertEqual(len(self.browser.client.get('/api/account/locations').json()['items']), 1)

    # Saved locations

    def test_saved_locations_are_private_summaries_with_bounds(self):
        self.assertEqual(self.browser.client.get('/api/account/locations').status_code, 401)
        self.assertEqual(self.browser.post('/api/account/locations', HOME).status_code, 401)
        self.browser.register(A)
        added = [self.browser.add(HOME), self.browser.add(OFFICE)]
        listing = self.browser.client.get('/api/account/locations')
        self.assertEqual(listing.headers['cache-control'], 'no-store')
        self.assertEqual([item['label'] for item in listing.json()['items']], [HOME['label'], OFFICE['label']])
        self.assertEqual(set(listing.json()['items'][0]), {'location_id', 'label', 'city', 'created_at'})
        for response in (listing.text, json.dumps(added)):
            for private in (HOME['street'], HOME['details'], OFFICE['street']):
                self.assertNotIn(private, response)
        for bad in ({**HOME, 'street': 'x'}, {**HOME, 'label': ''}, {**HOME, 'city': 'B'},
                    {**HOME, 'street': 'Main <script>'}, {**HOME, 'label': 'Home‮'},
                    {**HOME, 'details': 'x' * 201}, {**HOME, 'street': None}):
            with self.subTest(bad=bad):
                response = self.browser.post('/api/account/locations', bad)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(HOME['street'], response.text)
        for index in range(8):
            self.browser.add({**FAMILY, 'label': f'Test {index}'})
        self.assertEqual(self.browser.post('/api/account/locations', FAMILY).status_code, 409)
        with self.storage.engine.connect() as db:
            stored = db.execute(select(buyer_locations).where(
                buyer_locations.c.location_id == added[0]['location_id'])).mappings().one()
        self.assertEqual((stored['street'], stored['details']), (HOME['street'], HOME['details']))

    def test_another_account_can_never_read_select_or_change_locations_or_chats(self):
        self.browser.register(A)
        a_location = self.browser.add(HOME)
        a_chat = seed_chat(self.storage, self.browser.token, 'a')
        other = Browser(self)
        other.register(B)
        b_location = other.add(OFFICE)
        b_chat = seed_chat(self.storage, other.token, 'b')
        listing = other.client.get('/api/account/locations').json()['items']
        self.assertEqual([item['location_id'] for item in listing], [b_location['location_id']])
        self.assertEqual(other.post(f"/api/account/locations/{a_location['location_id']}/delete").status_code, 404)
        # B's own chat cannot use A's location.
        b_run = '/api/runs/' + b_chat.current.run.run_id
        response = other.post(b_run + '/destination', {'location_id': a_location['location_id'],
                                                       'recipients': ['retailer-book-local']})
        self.assertEqual(response.status_code, 404)
        # A's chat is invisible to B, whatever B sends.
        a_run = '/api/runs/' + a_chat.current.run.run_id
        self.assertEqual(other.client.get(a_run).status_code, 404)
        self.assertEqual(other.client.get(a_run + '/destination').status_code, 404)
        for path, body in ((a_run + '/destination', {'location_id': b_location['location_id'],
                                                     'recipients': ['retailer-book-local']}),
                           (a_run + '/destination/decline', {})):
            self.assertEqual(other.post(path, body).status_code, 404)
        state = self.browser.client.get(a_run + '/destination').json()
        self.assertIsNone(state['selection'])
        self.assertEqual(len(self.browser.client.get('/api/account/locations').json()['items']), 1)
        # Even through the service, an account cannot remove another account's location.
        with self.assertRaises(RunError) as error:
            self.accounts.delete_location(other.token, a_location['location_id'])
        self.assertEqual(error.exception.status, 404)

    # Destination for a chat

    def test_selecting_a_location_names_only_the_chosen_sellers_for_this_chat(self):
        self.browser.register(A)
        home, office = self.browser.add(HOME), self.browser.add(OFFICE)
        chat = seed_chat(self.storage, self.browser.token, 'select')
        run = '/api/runs/' + chat.current.run.run_id
        state = self.browser.client.get(run + '/destination').json()
        self.assertTrue(state['signed_in'])
        self.assertIsNone(state['selection'])
        # Overseas sellers are never offered as recipients.
        self.assertEqual(state['sellers'], [{'retailer_id': 'retailer-book-local', 'name': 'Book-Local'},
                                            {'retailer_id': 'retailer-book-other', 'name': 'Book-Other'}])
        for recipients in ([], ['retailer-book-forward'], ['unknown'], ['retailer-book-local'] * 2, 'retailer-book-local'):
            with self.subTest(recipients=recipients):
                response = self.browser.post(run + '/destination', {'location_id': home['location_id'],
                                                                    'recipients': recipients})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.browser.post(run + '/destination', {'location_id': 'missing',
                         'recipients': ['retailer-book-local']}).status_code, 404)
        response = self.browser.post(run + '/destination', {'location_id': office['location_id'],
                                                            'recipients': ['retailer-book-other']})
        self.assertEqual(response.status_code, 200, response.text)
        selection = response.json()['selection']
        self.assertEqual(selection['status'], 'selected')
        self.assertEqual(selection['location']['label'], OFFICE['label'])
        self.assertEqual(selection['recipients'], [{'retailer_id': 'retailer-book-other', 'name': 'Book-Other'}])
        self.assertEqual(selection['quote_state'], 'pending')
        self.assertNotIn(OFFICE['street'], response.text)
        # Only the named seller can obtain the address, and only for this chat.
        address = self.accounts.release_address(chat.current.run, 'retailer-book-other')
        self.assertEqual((address.city, address.street), (OFFICE['city'], OFFICE['street']))
        self.assertIsNone(self.accounts.release_address(chat.current.run, 'retailer-book-local'))
        self.assertIsNone(self.accounts.release_address(chat.current.run, 'retailer-book-forward'))
        # Changing the choice replaces it; the earlier revision is part of the same chat.
        self.browser.post(run + '/destination', {'location_id': home['location_id'],
                                                 'recipients': ['retailer-book-local', 'retailer-book-other']})
        self.assertEqual(self.accounts.release_address(chat.previous.run, 'retailer-book-local').street,
                         HOME['street'])
        pending = self.accounts.pending_quotes()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].run, chat.current.run)
        self.assertEqual(pending[0].recipients, ('retailer-book-local', 'retailer-book-other'))
        self.assertEqual(self.count(buyer_destinations), 1)

    def test_selection_follows_the_chat_to_its_next_revision(self):
        self.browser.register(A)
        home = self.browser.add(HOME)
        chat = seed_chat(self.storage, self.browser.token, 'refine')
        run = '/api/runs/' + chat.current.run.run_id
        self.browser.post(run + '/destination', {'location_id': home['location_id'],
                                                 'recipients': ['retailer-book-local']})
        domestic = {'include_international': False, 'international_markets': ()}
        refined = self.service.refine(self.browser.token, chat.current.run,
                                      chat.current.request.refine({'market_scope': domestic}))
        self.assertEqual(refined.previous.run, chat.current.run)
        state = self.browser.client.get('/api/runs/' + refined.current.run.run_id + '/destination').json()
        # The new revision has no offers yet, so it names no sellers, but the choice is kept.
        self.assertEqual(state['sellers'], [])
        self.assertEqual(state['selection']['recipients'], [{'retailer_id': 'retailer-book-local',
                                                             'name': 'Book-Local'}])
        self.assertEqual(self.accounts.pending_quotes()[0].run, refined.current.run)
        self.assertEqual(self.accounts.release_address(refined.current.run, 'retailer-book-local').street,
                         HOME['street'])

    def test_anonymous_buyers_must_sign_in_to_select_but_may_decline(self):
        chat = seed_chat(self.storage, self.browser.token, 'decline')
        run = '/api/runs/' + chat.current.run.run_id
        state = self.browser.client.get(run + '/destination').json()
        self.assertFalse(state['signed_in'])
        response = self.browser.post(run + '/destination', {'location_id': 'any', 'recipients': ['retailer-book-local']})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'Sign in to use saved locations')
        declined = self.browser.post(run + '/destination/decline')
        self.assertEqual(declined.status_code, 200, declined.text)
        self.assertEqual(declined.json()['selection'], {
            'status': 'declined', 'location': None, 'recipients': [], 'quote_state': 'none',
            'decided_at': declined.json()['selection']['decided_at']})
        # Shopping continues: the comparison, its exact links and unknown costs are unchanged.
        workspace = self.browser.client.get(run).json()
        self.assertEqual(workspace['current'], chat.current.model_dump(mode='json'))
        offer = workspace['current']['recommendations'][0]['primary']['offer']
        self.assertTrue(offer['listing_url'].startswith('https://'))
        self.assertIsNone(self.accounts.release_address(chat.current.run, 'retailer-book-local'))
        self.assertEqual(self.accounts.pending_quotes(), ())
        # The decline survives signing in, and the buyer can still change their mind.
        self.browser.register(A)
        self.assertEqual(self.browser.client.get(run + '/destination').json()['selection']['status'], 'declined')
        home = self.browser.add(HOME)
        chosen = self.browser.post(run + '/destination', {'location_id': home['location_id'],
                                                          'recipients': ['retailer-book-local']})
        self.assertEqual(chosen.json()['selection']['status'], 'selected')

    def test_deleting_a_location_or_the_chat_withdraws_the_selection(self):
        self.browser.register(A)
        home = self.browser.add(HOME)
        chat = seed_chat(self.storage, self.browser.token, 'withdraw')
        run = '/api/runs/' + chat.current.run.run_id
        self.browser.post(run + '/destination', {'location_id': home['location_id'],
                                                 'recipients': ['retailer-book-local']})
        deleted = self.browser.post(f"/api/account/locations/{home['location_id']}/delete")
        self.assertEqual(deleted.json(), {'deleted': True})
        self.assertIsNone(self.browser.client.get(run + '/destination').json()['selection'])
        self.assertIsNone(self.accounts.release_address(chat.current.run, 'retailer-book-local'))
        self.assertEqual(self.browser.post(f"/api/account/locations/{home['location_id']}/delete").status_code, 404)
        office = self.browser.add(OFFICE)
        self.browser.post(run + '/destination', {'location_id': office['location_id'],
                                                 'recipients': ['retailer-book-local']})
        self.assertEqual(self.browser.post('/api/history/' + chat.current.run.run_id + '/delete').status_code, 200)
        self.assertEqual(self.count(buyer_destinations), 0)
        self.assertEqual(self.count(buyer_locations), 1)

    def test_a_later_signed_in_search_reuses_a_saved_location_with_a_new_choice(self):
        self.browser.register(A)
        home = self.browser.add(HOME)
        first = seed_chat(self.storage, self.browser.token, 'first')
        self.browser.post('/api/runs/' + first.current.run.run_id + '/destination',
                          {'location_id': home['location_id'], 'recipients': ['retailer-book-local']})
        self.browser.issued(self.browser.post('/api/account/sign-out'))
        laptop = Browser(self)
        laptop.sign_in(A)
        later = seed_chat(self.storage, laptop.token, 'later')
        run = '/api/runs/' + later.current.run.run_id
        self.assertIsNone(laptop.client.get(run + '/destination').json()['selection'])
        saved = laptop.client.get('/api/account/locations').json()['items']
        self.assertEqual([item['location_id'] for item in saved], [home['location_id']])
        response = laptop.post(run + '/destination', {'location_id': home['location_id'],
                                                      'recipients': ['retailer-book-other']})
        self.assertEqual(response.json()['selection']['recipients'][0]['retailer_id'], 'retailer-book-other')

    def test_precise_addresses_stay_out_of_logs_snapshots_history_and_exports(self):
        records = []
        handler = logging.Handler(logging.DEBUG)
        handler.emit = lambda record: records.append(record.getMessage() + repr(record.args))
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous_level)
        self.browser.register(A)
        home = self.browser.add(HOME)
        chat = seed_chat(self.storage, self.browser.token, 'private')
        run_id = chat.current.run.run_id
        self.browser.post(f'/api/runs/{run_id}/destination', {'location_id': home['location_id'],
                                                             'recipients': ['retailer-book-local']})
        visible = [self.browser.client.get(path).text for path in (
            f'/api/runs/{run_id}', '/api/history', f'/api/history/{run_id}', f'/api/history/{run_id}/export',
            f'/api/runs/{run_id}/destination', '/api/account/locations', '/api/session')]
        with self.storage.engine.connect() as db:
            visible += [json.dumps(row) for row in db.execute(select(buyer_runs.c.snapshot)).scalars()]
        address = self.accounts.release_address(chat.current.run, 'retailer-book-local')
        visible += [repr(address), str(address), *records]
        for text in visible:
            self.assertNotIn(HOME['street'], text)
            self.assertNotIn(HOME['details'], text)
        self.assertNotIn(HOME['label'], visible[3])
        self.assertEqual(address.street, HOME['street'])
        self.assertIsInstance(address, PreciseAddress)

    def test_expired_sessions_drop_their_sign_in_and_destination_but_not_the_account(self):
        self.browser.register(A)
        home = self.browser.add(HOME)
        chat = seed_chat(self.storage, self.browser.token, 'expiry')
        self.browser.post('/api/runs/' + chat.current.run.run_id + '/destination',
                          {'location_id': home['location_id'], 'recipients': ['retailer-book-local']})
        self.now += timedelta(hours=24, seconds=1)
        self.service.recover()
        self.assertIsNone(self.accounts.release_address(chat.current.run, 'retailer-book-local'))
        for table, expected in ((buyer_signins, 0), (buyer_destinations, 0), (buyer_runs, 0),
                                (buyer_accounts, 1), (buyer_locations, 1)):
            self.assertEqual(self.count(table), expected, table.name)

    def test_chat_sellers_skip_overseas_and_repeat_sellers(self):
        workspace = ComparisonWorkspace.model_validate(fixture('workspace'))
        self.assertEqual([seller.retailer_id for seller in chat_sellers(workspace.current)], ['retailer-book-local'])
        self.assertEqual([seller.name for seller in chat_sellers(workspace.previous)], ['Book-Other'])


if __name__ == '__main__':
    unittest.main()
