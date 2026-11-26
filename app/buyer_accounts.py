"""TechScout buyer accounts, private saved locations and per-chat destination choices (#187).

Accounts sit beside the cookie-owned buyer chats in app.buyer_runs and share its serialized
transaction. Passwords are kept only as salted scrypt hashes (standard library, no new
dependency) and are hashed outside database transactions. Signing in rotates the browser
session; an anonymous session's chats move into the new signed-in session so the buyer
returns to the same chat. Signing out ends the session, which deletes its private chats
exactly as expiry does.

A saved location belongs to one account. Its street details never enter shopping requests,
run snapshots, history, exports, API listings or logs. Selecting a location for a chat names
the retailers allowed to receive it for that chat only; release_address is the single
internal path by which the pre-order quote operation (#186, app.delivery_quotes) may obtain it.
The chat's destination state also carries the sellers waiting for an address, the delivery
quotes that apply and the revised comparison.
"""
import base64
import binascii
from datetime import timedelta
import hashlib
import hmac
import re
import secrets
import unicodedata
import uuid
from typing import Literal

from pydantic import Field
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import contracts_v2 as v
from app.buyer_runs import BuyerRunService, RunError, session_hash
from app.contracts import Model, Text, UtcTime
from app.delivery_quotes import (
    DeliveryQuote, QuoteState, RevisedComparison, chat_quotes, read_decision, waiting_sellers,
)
from app.quote_capabilities import active_quote_routes
from app.storage import (
    buyer_accounts, buyer_attempts, buyer_destinations, buyer_locations, buyer_quote_plans, buyer_quotes,
    buyer_runs, buyer_sessions, buyer_signins,
)

# About 16 MiB and tens of milliseconds per hash. Parameters are stored with each hash so
# they can be raised later without invalidating existing accounts.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
_MAXMEM = 160 * 1024 * 1024  # Enough for the largest accepted parameters, N=2**16 and r=8 (64 MiB).
USERNAME = re.compile(r'^[a-z0-9][a-z0-9._-]{2,31}$')
PASSWORD_LENGTH = (10, 128)
# A few of the most common passwords long enough to pass the length rule.
COMMON_PASSWORDS = frozenset({
    '1234567890', '12345678910', '0123456789', 'qwertyuiop', 'password12', 'password123',
    'password1234', 'iloveyou12', 'qwerty1234', 'qwerty12345', '1q2w3e4r5t', 'abcdefghij',
    'abc1234567', 'techscout1', 'techscout123', 'letmein123', 'welcome123', 'admin12345',
})
SIGN_IN_FAILED = 'The username or password is incorrect'
TOO_MANY = 'Too many attempts. Wait a few minutes before trying again.'
SIGN_IN_REQUIRED = 'Sign in to use saved locations'
MAX_LOCATIONS = 10
MAX_ACCOUNTS = 10000
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩<>]')


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=_MAXMEM, dklen=32)
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split('$')
        n, r, p = int(n), int(r), int(p)
        if scheme != 'scrypt' or not (2**10 <= n <= 2**16 and 1 <= r <= 8 and 1 <= p <= 4):
            return False
        expected = base64.b64decode(digest, validate=True)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt, validate=True),
                                n=n, r=r, p=p, maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_username(value) -> str | None:
    """The canonical username, or None when it cannot belong to an account."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    name = unicodedata.normalize('NFKC', value).strip().casefold()
    return name if USERNAME.fullmatch(name) else None


def _password(value) -> str | None:
    if not isinstance(value, str) or len(value) > 4 * PASSWORD_LENGTH[1]:
        return None
    return unicodedata.normalize('NFKC', value)


def check_new_password(password: str, username: str) -> None:
    low, high = PASSWORD_LENGTH
    if not low <= len(password) <= high:
        raise RunError(422, f'Use a password of {low}–{high} characters')
    folded = password.casefold()
    if username in folded or folded in COMMON_PASSWORDS or len(set(folded)) < 4:
        raise RunError(422, 'Choose a less predictable password that does not contain your username')


def _clean(value, field, low, high, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise RunError(422, f'{field} is required')
    text = ' '.join(unicodedata.normalize('NFKC', value).split())
    if not text and not required:
        return None
    if _CONTROL.search(text) or not low <= len(text) <= high:
        raise RunError(422, f'{field} must be {low}–{high} characters of plain text')
    return text


class AccountSummary(Model):
    username: Text


class SavedLocation(Model):
    """A buyer-facing location summary. The street and building details are never returned."""
    location_id: Text
    label: Text
    city: Text
    created_at: UtcTime


class LocationList(Model):
    items: tuple[SavedLocation, ...]
    limit: int = MAX_LOCATIONS


class DestinationSeller(Model):
    retailer_id: Text
    name: Text


class DestinationSelection(Model):
    status: Literal['selected', 'declined']
    location: SavedLocation | None = None
    recipients: tuple[DestinationSeller, ...] = ()
    # What happened to the street-level quotes for this choice (docs/delivery-quotes.md):
    # pending, requested, quoted, partial, unavailable, not_needed; none after a decline.
    quote_state: QuoteState
    decided_at: UtcTime


class DestinationState(Model):
    """The current chat's destination choice and the Lebanese sellers it may name."""
    request_id: Text
    signed_in: bool
    sellers: tuple[DestinationSeller, ...]
    selection: DestinationSelection | None = None
    # Additive since #186. waiting: sellers whose open delivery total a street address could
    # settle, to name once in the prompt. quotes: the delivery quotes that apply to the latest
    # search and destination. revised: totals, ranking and advice after those quotes.
    waiting: tuple[DestinationSeller, ...] = ()
    quotes: tuple[DeliveryQuote, ...] = ()
    revised: RevisedComparison | None = None


class PendingQuote(Model):
    """Hand-off to the pre-order quote operation (#186). Carries no address."""
    run: v.RunReference
    recipients: tuple[Text, ...]


class PreciseAddress(Model):
    """Released only to a named recipient. repr() masks the private fields."""
    city: Text
    street: str = Field(repr=False)
    details: str | None = Field(default=None, repr=False)


def chat_sellers(snapshot: v.RunSnapshot) -> tuple[DestinationSeller, ...]:
    """Distinct Lebanese-market sellers with pickup or direct delivery in a chat, in display order.

    Import and forwarding routes are never offered as recipients (Offer.domestic_route, #185)."""
    offers = [checked.offer for group in snapshot.recommendations for checked in group.offers()]
    offers += [item.offer for item in (*snapshot.provisional, *snapshot.uncertain)]
    sellers = {}
    for offer in offers:
        if offer.domestic_route():
            sellers.setdefault(offer.retailer_id, DestinationSeller(retailer_id=offer.retailer_id,
                                                                    name=offer.seller.name))
    return tuple(sellers.values())


class BuyerAccounts:
    def __init__(self, runs: BuyerRunService, *, name_attempts=5, session_attempts=10,
                 registrations_per_session=5, registrations_per_hour=60, window_seconds=900,
                 quote_registry=None):
        if min(name_attempts, session_attempts, registrations_per_session, registrations_per_hour,
               window_seconds) <= 0:
            raise ValueError('Positive throttling limits are required')
        self.runs = runs
        # The source registry whose active quote routes decide which sellers wait for an
        # address (#186). None: no route is active, so no seller waits.
        self.quote_registry = quote_registry
        self.name_attempts, self.session_attempts = name_attempts, session_attempts
        self.registrations_per_session = registrations_per_session
        self.registrations_per_hour = registrations_per_hour
        self.window = timedelta(seconds=window_seconds)
        # Unknown usernames are checked against this hash so that both paths cost the same.
        self._decoy = hash_password(secrets.token_urlsafe(24))

    # Sessions and accounts

    def _signed_in(self, db, session):
        row = db.execute(select(buyer_accounts).join(buyer_signins).where(
            buyer_signins.c.session_hash == session['session_hash'])).mappings().first()
        return row

    def session(self, token):
        """Return token, CSRF, expiry and the signed-in account of a new or existing session."""
        with self.runs.guarded() as (db, now):
            row = self.runs.owner(db, now, token, required=False)
            if row is None:
                token, csrf, expires = self.runs.issue(db, now)
                return token, csrf, expires, None
            account = self._signed_in(db, row)
            summary = AccountSummary(username=account['username']) if account else None
            return token, row['csrf'], row['expires_at'], summary

    def _throttle(self, db, now, checks):
        """Count one attempt against each (key, limit, window); refuse when any is exhausted."""
        oldest = now - max(timedelta(hours=1), self.window)
        db.execute(delete(buyer_attempts).where(buyer_attempts.c.window_start <= oldest))
        rows = {}
        for key, limit, window in checks:
            row = db.execute(select(buyer_attempts).where(buyer_attempts.c.key == key)).mappings().first()
            if row is not None and row['window_start'] > now - window:
                if row['attempts'] >= limit:
                    raise RunError(429, TOO_MANY)
                rows[key] = row
        for key, _, _ in checks:
            if key in rows:
                db.execute(update(buyer_attempts).where(buyer_attempts.c.key == key).values(
                    attempts=rows[key]['attempts'] + 1))
            else:
                db.execute(delete(buyer_attempts).where(buyer_attempts.c.key == key))
                db.execute(insert(buyer_attempts).values(key=key, attempts=1, window_start=now))

    @staticmethod
    def _name_key(name):
        return 'name:' + hashlib.sha256(name.encode()).hexdigest()

    def _rotate(self, db, now, session, account_id):
        """Issue a new signed-in session. Chats move only from an anonymous or same-account session."""
        previous = session['session_hash']
        owner = db.execute(select(buyer_signins.c.account_id).where(
            buyer_signins.c.session_hash == previous)).scalar_one_or_none()
        token, csrf, expires = self.runs.issue(db, now)
        current = session_hash(token)
        db.execute(insert(buyer_signins).values(session_hash=current, account_id=account_id, signed_in_at=now))
        if owner in (None, account_id):
            db.execute(update(buyer_runs).where(buyer_runs.c.session_hash == previous).values(session_hash=current))
            db.execute(update(buyer_destinations).where(buyer_destinations.c.session_hash == previous)
                       .values(session_hash=current))
            db.execute(update(buyer_quotes).where(buyer_quotes.c.session_hash == previous)
                       .values(session_hash=current))
        db.execute(delete(buyer_sessions).where(buyer_sessions.c.session_hash == previous))
        return token, csrf, expires

    def register(self, token, username, password):
        name, secret = normalize_username(username), _password(password)
        if name is None:
            raise RunError(422, 'Use 3–32 lowercase letters, digits, dots, dashes or underscores, '
                                'starting with a letter or digit')
        if secret is None:
            raise RunError(422, f'Use a password of {PASSWORD_LENGTH[0]}–{PASSWORD_LENGTH[1]} characters')
        check_new_password(secret, name)
        with self.runs.guarded() as (db, now):
            session = self.runs.owner(db, now, token)
            # Committed before the outcome, so refused registrations are throttled too.
            self._throttle(db, now, [('register:' + session['session_hash'], self.registrations_per_session,
                                      timedelta(hours=1)),
                                     ('register:all', self.registrations_per_hour, timedelta(hours=1))])
            taken = db.execute(select(buyer_accounts.c.account_id).where(
                buyer_accounts.c.username == name)).first() is not None
        if taken:
            raise RunError(409, 'That username is not available. Choose another.')
        digest = hash_password(secret)
        try:
            with self.runs.guarded() as (db, now):
                session = self.runs.owner(db, now, token)
                if db.execute(select(func.count()).select_from(buyer_accounts)).scalar_one() >= MAX_ACCOUNTS:
                    raise RunError(429, 'Account capacity is busy')
                account_id = str(uuid.uuid4())
                db.execute(insert(buyer_accounts).values(account_id=account_id, username=name,
                                                         password_hash=digest, created_at=now))
                issued = self._rotate(db, now, session, account_id)
        except IntegrityError as exc:
            raise RunError(409, 'That username is not available. Choose another.') from exc
        return (*issued, AccountSummary(username=name))

    def sign_in(self, token, username, password):
        name, secret = normalize_username(username), _password(password)
        with self.runs.guarded() as (db, now):
            session = self.runs.owner(db, now, token)
            # Unknown and malformed names consume the same per-name budget as real ones.
            name_key = self._name_key(name or str(username)[:64])
            self._throttle(db, now, [(name_key, self.name_attempts, self.window),
                                     ('session:' + session['session_hash'], self.session_attempts, self.window)])
            account = None if name is None else db.execute(select(buyer_accounts).where(
                buyer_accounts.c.username == name)).mappings().first()
        stored = account['password_hash'] if account else self._decoy
        valid = verify_password(secret or '', stored) and account is not None and secret is not None
        if not valid:
            raise RunError(401, SIGN_IN_FAILED)
        with self.runs.guarded() as (db, now):
            session = self.runs.owner(db, now, token)
            if db.execute(select(buyer_accounts.c.account_id).where(
                    buyer_accounts.c.account_id == account['account_id'],
                    buyer_accounts.c.password_hash == stored)).first() is None:
                raise RunError(401, SIGN_IN_FAILED)
            db.execute(delete(buyer_attempts).where(buyer_attempts.c.key == name_key))
            issued = self._rotate(db, now, session, account['account_id'])
        return (*issued, AccountSummary(username=account['username']))

    def sign_out(self, token):
        """End this session and its private chats; return a new anonymous session."""
        with self.runs.guarded() as (db, now):
            session = self.runs.owner(db, now, token)
            db.execute(delete(buyer_sessions).where(buyer_sessions.c.session_hash == session['session_hash']))
            return self.runs.issue(db, now)

    def _account_id(self, db, now, token):
        session = self.runs.owner(db, now, token)
        account = self._signed_in(db, session)
        if account is None:
            raise RunError(401, SIGN_IN_REQUIRED)
        return session, account['account_id']

    # Saved locations

    @staticmethod
    def _summary(row):
        return SavedLocation(location_id=row['location_id'], label=row['label'], city=row['city'],
                             created_at=row['created_at'])

    def locations(self, token):
        with self.runs.guarded() as (db, now):
            _, account_id = self._account_id(db, now, token)
            rows = db.execute(select(buyer_locations).where(buyer_locations.c.account_id == account_id)
                              .order_by(buyer_locations.c.label, buyer_locations.c.created_at,
                                        buyer_locations.c.location_id)).mappings()
            return LocationList(items=tuple(self._summary(row) for row in rows))

    def add_location(self, token, *, label, city, street, details=None):
        values = dict(label=_clean(label, 'Label', 1, 40), city=_clean(city, 'City', 2, 80),
                      street=_clean(street, 'Street', 3, 200),
                      details=_clean(details, 'Building details', 1, 200, required=False))
        with self.runs.guarded() as (db, now):
            _, account_id = self._account_id(db, now, token)
            count = db.execute(select(func.count()).select_from(buyer_locations).where(
                buyer_locations.c.account_id == account_id)).scalar_one()
            if count >= MAX_LOCATIONS:
                raise RunError(409, f'You can save up to {MAX_LOCATIONS} locations; delete one first')
            row = dict(location_id=str(uuid.uuid4()), account_id=account_id, created_at=now, **values)
            db.execute(insert(buyer_locations).values(**row))
            return self._summary(row)

    def delete_location(self, token, location_id):
        """Delete an owned location. Chats that selected it lose that selection."""
        with self.runs.guarded() as (db, now):
            _, account_id = self._account_id(db, now, token)
            if db.execute(delete(buyer_locations).where(buyer_locations.c.location_id == str(location_id),
                    buyer_locations.c.account_id == account_id)).rowcount != 1:
                raise RunError(404, 'Location not found')

    # Destination for one chat

    def _routes(self):
        """Active quote routes, read before (never inside) a guarded transaction."""
        return {} if self.quote_registry is None else active_quote_routes(self.quote_registry)

    def _chat(self, db, now, token, run_id):
        session = self.runs.owner(db, now, token)
        owner = session['session_hash']
        row = self.runs.owned_run(db, run_id, owner)
        latest = db.execute(select(buyer_runs.c.snapshot).where(buyer_runs.c.session_hash == owner,
            buyer_runs.c.request_id == row['request_id']).order_by(buyer_runs.c.request_revision.desc())
            .limit(1)).scalar_one()
        snapshot = v.RunSnapshot.model_validate(latest)
        return session, row['request_id'], chat_sellers(snapshot), snapshot

    def _state(self, db, session, request_id, sellers, snapshot, routes):
        decision = db.execute(select(buyer_destinations).where(
            buyer_destinations.c.session_hash == session['session_hash'],
            buyer_destinations.c.request_id == request_id)).mappings().first()
        selection = None
        if decision is not None:
            location = None
            if decision['location_id'] is not None:
                location = self._summary(db.execute(select(buyer_locations).where(
                    buyer_locations.c.location_id == decision['location_id'])).mappings().one())
            selection = DestinationSelection(status=decision['status'], location=location,
                                             recipients=decision['recipients'], quote_state=decision['quote_state'],
                                             decided_at=decision['decided_at'])
        decision, _ = read_decision(db, session['session_hash'], request_id)
        quotes, revised = chat_quotes(db, session['session_hash'], request_id, snapshot, decision, routes)
        waiting = tuple(DestinationSeller(retailer_id=retailer, name=name)
                        for retailer, name in waiting_sellers(snapshot, routes))
        return DestinationState(request_id=request_id, signed_in=self._signed_in(db, session) is not None,
                                sellers=sellers, selection=selection, waiting=waiting, quotes=quotes,
                                revised=revised)

    def _decide(self, db, now, session, request_id, **values):
        key = (buyer_destinations.c.session_hash == session['session_hash'],
               buyer_destinations.c.request_id == request_id)
        db.execute(delete(buyer_destinations).where(*key))
        db.execute(insert(buyer_destinations).values(session_hash=session['session_hash'], request_id=request_id,
                                                     decided_at=now, **values))
        # Every new choice re-plans the chat's quotes (#186), even within one clock tick.
        db.execute(delete(buyer_quote_plans).where(buyer_quote_plans.c.run_id.in_(
            select(buyer_runs.c.run_id).where(buyer_runs.c.session_hash == session['session_hash'],
                                              buyer_runs.c.request_id == request_id))))

    def destination(self, token, run_id):
        routes = self._routes()
        with self.runs.guarded() as (db, now):
            return self._state(db, *self._chat(db, now, token, run_id), routes)

    def select_destination(self, token, run_id, location_id, recipients):
        """Use an owned location for this chat and only the named sellers in it."""
        if (not isinstance(recipients, (list, tuple)) or not 1 <= len(recipients) <= 20
                or len(set(recipients)) != len(recipients)):
            raise RunError(422, 'Choose one or more distinct sellers to receive this address')
        routes = self._routes()
        with self.runs.guarded() as (db, now):
            session, request_id, sellers, snapshot = self._chat(db, now, token, run_id)
            _, account_id = self._account_id(db, now, token)
            if db.execute(select(buyer_locations.c.location_id).where(
                    buyer_locations.c.location_id == str(location_id),
                    buyer_locations.c.account_id == account_id)).first() is None:
                raise RunError(404, 'Location not found')
            named = {seller.retailer_id: seller for seller in sellers}
            if any(retailer not in named for retailer in recipients):
                raise RunError(422, 'Choose sellers from this comparison')
            self._decide(db, now, session, request_id, status='selected', location_id=str(location_id),
                         quote_state='pending',
                         recipients=[named[retailer].model_dump() for retailer in recipients])
            return self._state(db, session, request_id, sellers, snapshot, routes)

    def decline_destination(self, token, run_id):
        """Keep shopping without a precise address; no seller receives one in this chat."""
        routes = self._routes()
        with self.runs.guarded() as (db, now):
            session, request_id, sellers, snapshot = self._chat(db, now, token, run_id)
            self._decide(db, now, session, request_id, status='declined', location_id=None,
                         quote_state='none', recipients=[])
            return self._state(db, session, request_id, sellers, snapshot, routes)

    # Internal hand-off to the pre-order quote operation (#186). Never exposed over HTTP.

    def pending_quotes(self, limit=20) -> tuple[PendingQuote, ...]:
        """Selected destinations still waiting for retailer quotes, with each chat's latest run.

        Kept from the #187 hand-off. The quote worker (app.delivery_quotes.QuoteService) plans
        from buyer_quote_plans instead, which also covers a new search and a changed choice."""
        with self.runs.guarded() as (db, now):
            rows = db.execute(select(buyer_destinations).join(buyer_sessions).where(
                buyer_destinations.c.status == 'selected', buyer_destinations.c.quote_state == 'pending',
                buyer_sessions.c.expires_at > now).order_by(buyer_destinations.c.decided_at)
                .limit(limit)).mappings().all()
            pending = []
            for row in rows:
                latest = db.execute(select(buyer_runs.c.snapshot).where(
                    buyer_runs.c.session_hash == row['session_hash'], buyer_runs.c.request_id == row['request_id'])
                    .order_by(buyer_runs.c.request_revision.desc()).limit(1)).scalar_one_or_none()
                if latest is not None:
                    pending.append(PendingQuote(run=latest['run'], recipients=tuple(
                        item['retailer_id'] for item in row['recipients'])))
            return tuple(pending)

    def release_address(self, run: v.RunReference, retailer_id: str, *,
                        location_id: str | None = None) -> PreciseAddress | None:
        """The selected address of that run's chat, only for a retailer the buyer named.

        With location_id, only while that location is still the chat's selection, so a quote
        planned for one address can never carry another (#186)."""
        with self.runs.guarded() as (db, now):
            row = db.execute(select(buyer_runs.c.session_hash, buyer_runs.c.request_id).join(buyer_sessions).where(
                buyer_runs.c.run_id == run.run_id, buyer_sessions.c.expires_at > now)).first()
            if row is None:
                return None
            decision = db.execute(select(buyer_destinations).where(
                buyer_destinations.c.session_hash == row.session_hash,
                buyer_destinations.c.request_id == row.request_id,
                buyer_destinations.c.status == 'selected')).mappings().first()
            if decision is None or retailer_id not in {item['retailer_id'] for item in decision['recipients']}:
                return None
            if location_id is not None and decision['location_id'] != location_id:
                return None
            location = db.execute(select(buyer_locations).where(
                buyer_locations.c.location_id == decision['location_id'])).mappings().one()
            return PreciseAddress(city=location['city'], street=location['street'], details=location['details'])
