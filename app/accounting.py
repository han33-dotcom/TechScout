"""One durable, fail-closed campaign ledger for all devices and worker processes.

No provider calls occur here. Every attempt (including retries/fallbacks) obtains a
fresh reservation before dispatch. Uncertain attempts retain their entire bound.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import math
from hashlib import sha256
import os
import uuid

from sqlalchemy import select, update, func, or_

from app.ledger_storage import entries, materialize, read_state, save_state

from app.storage import Storage, accounting_state


class BudgetDenied(RuntimeError):
    pass


def money(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError('A known nonnegative Decimal cost bound is required')
    return value


@dataclass(frozen=True)
class Charge:
    """All-inclusive upper bound; rates/fees are configuration, never guessed."""
    provider: Decimal
    gateway: Decimal = Decimal(0)
    payment: Decimal = Decimal(0)
    byok: Decimal = Decimal(0)

    @property
    def total(self):
        return sum((money(v) for v in (self.provider, self.gateway, self.payment, self.byok)), Decimal(0))


# fetch attempts, paid attempts, active worker milliseconds, USD, all external calls
LIMITS = {
    'project': (None, None, None, '500', None),
    'bootstrap': (3000, 200, 10_800_000, '50', None),
    'bootstrap:international': (1000, 60, 3_600_000, '15', None),
    'maintenance': (300, 30, 1_800_000, '3', None),
    'maintenance:international': (100, 10, 900_000, '1', None),
    'buyer': (40, 40, None, '1', 40),
}
KINDS = {'model', 'search', 'retrieval', 'embedding', 'work', 'worker'}


class Ledger:
    def __init__(self, storage: Storage, project: str, *, fixture=False, clock=None):
        if not project or not project.strip():
            raise ValueError('An explicit shared project identifier is required')
        if not fixture and (storage.engine.dialect.name != 'postgresql' or clock is not None):
            raise ValueError('Operational accounting requires the central PostgreSQL database clock')
        self.storage, self.project, self.clock = storage, project, clock

    @classmethod
    def configured(cls):
        # Deliberately no DATABASE_URL/local fallback: every laptop uses this service.
        url = os.environ.get('TECHSCOUT_ACCOUNTING_URL', '').strip()
        project = os.environ.get('TECHSCOUT_ACCOUNTING_PROJECT', '').strip()
        if not url or not project:
            raise BudgetDenied('An explicit accounting URL and project are required')
        return cls(Storage(url), project)

    @contextmanager
    def _transaction(self, *, initializing=False, readonly=False):
        with self.storage.transaction() as db:
            query = select(accounting_state.c.payload).where(accounting_state.c.id == 1)
            if db.dialect.name == 'postgresql':
                query = query.with_for_update(read=readonly)
            elif readonly:
                # sqlite3 legacy transaction control doesn't BEGIN on SELECT in Python 3.11.
                db.exec_driver_sql('BEGIN')
            else:
                db.execute(update(accounting_state).where(accounting_state.c.id == 1).values(id=1))
            header = db.execute(query).scalar_one_or_none()
            if header is None:
                raise BudgetDenied('Accounting database has not been migrated')
            state = read_state(db, header)
            if not initializing and state['project'] != self.project:
                raise BudgetDenied('Central project has not been provisioned or identifier differs')
            clock = func.clock_timestamp() if db.dialect.name == 'postgresql' else func.current_timestamp()
            now = self.clock() if self.clock else db.execute(select(clock)).scalar_one()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            yield state, now.timestamp()
            if not readonly:
                save_state(db, header, state)

    def provision(self):
        """Explicit administrative initialization; never resets an existing campaign."""
        with self._transaction(initializing=True) as (state, _):
            if state['project'] is not None:
                if state['project'] != self.project:
                    raise BudgetDenied('A different campaign already owns this ledger')
                return
            if state != {'project': None}:
                raise BudgetDenied('Unidentified ledger contains prior state; restore or reconcile it')
            state.update(project=self.project, blocked=False, scopes={}, jobs={}, attempts={}, credits={})

    def open_job(self, job_id, kind='buyer', *, international=False, envelope=None):
        if not isinstance(job_id, str) or not job_id.strip() or kind not in {'buyer', 'bootstrap', 'maintenance'}:
            raise ValueError('A stable job ID and supported job kind are required')
        if type(international) is not bool:
            raise ValueError('International opt-in must be explicit')
        if envelope is not None:
            if (kind != 'maintenance' or set(envelope) != {'calls', 'seconds', 'usd'}
                    or any(type(envelope[k]) is not int or envelope[k] < 1 for k in ('calls', 'seconds'))):
                raise ValueError('Maintenance jobs need positive call and time limits')
            envelope = {**envelope, 'usd': str(money(Decimal(envelope['usd'])))}
        with self._transaction() as (state, now):
            jobs = state['jobs']
            if job_id in jobs:
                job = jobs[job_id]
                if job['kind'] != kind or job['international'] != international:
                    raise BudgetDenied('A job cannot reset its scope on resume or clarification')
                if envelope is not None and job.get('envelope') != envelope:
                    raise BudgetDenied('A maintenance job cannot change its envelope')
            else:
                day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
                jobs[job_id] = dict(kind=kind, international=international, day=day,
                                    started=now, cancelled=False, final=False)
                if envelope is not None:
                    jobs[job_id]['envelope'] = envelope
        return Job(self, job_id)

    def _scope_keys(self, job_id, job, international):
        keys = [('project', 'project')]
        if job['kind'] == 'buyer':
            keys.append(('buyer:' + job_id, 'buyer'))
        else:
            base = job['kind'] + (':' + job['day'] if job['kind'] == 'maintenance' else '')
            keys.append((base, job['kind']))
            if international:
                keys.append((base + ':international', job['kind'] + ':international'))
        return keys

    def reserve(self, job_id, kind, charge, seconds, *, source=None, limits=None, international=False, resource=None):
        if kind not in KINDS or not isinstance(charge, Charge):
            raise ValueError('A typed call kind and all-inclusive charge are required')
        cost = charge.total
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 120:
            raise ValueError('Attempts require a positive timeout of at most 120 seconds')
        if type(international) is not bool or (kind in {'work', 'worker'} and cost):
            raise ValueError('Invalid attempt scope or local-work charge')
        if source is not None and limits is None:
            raise ValueError('Source requests need the current registry limits')
        if kind == 'retrieval' and not source:
            raise ValueError('Retrieval needs an admitted source')
        values = [int(kind == 'retrieval' or source is not None), int(cost > 0),
                  math.ceil(seconds * 1000), str(cost), int(kind not in {'work', 'worker'})]
        with self._transaction() as (state, now):
            job = state['jobs'][job_id]
            if state['blocked'] or job['cancelled']:
                raise BudgetDenied('Project or job is stopped')
            if international and not job['international']:
                raise BudgetDenied('International work requires opt-in')
            if job['kind'] == 'buyer' and now + seconds > job['started'] + (120 if job['final'] else 90):
                raise BudgetDenied('Run deadline includes final verification and writer')
            if job['kind'] == 'maintenance' and datetime.fromtimestamp(now, timezone.utc).date().isoformat() != job['day']:
                raise BudgetDenied('A new UTC maintenance day requires a new job ID')
            if job['kind'] == 'maintenance':
                days = {key for key, usage in state['scopes'].matching_items(entries.c.key.startswith('maintenance:'))
                        if key.startswith('maintenance:') and key.count(':') == 1 and any(map(Decimal, usage))}
                if 'maintenance:' + job['day'] not in days and len(days) >= 14:
                    raise BudgetDenied('The 14 maintenance days are exhausted')
            attempts = state['attempts']
            live = attempts.matching(entries.c.status.in_(('held', 'sent')) & (entries.c.expires > now))
            envelope = job.get('envelope')
            if envelope:
                consumed = attempts.matching((entries.c.job_id == job_id) & (entries.c.status != 'released'))
                if (now + seconds > job['started'] + envelope['seconds']
                        or sum(a['values'][4] for a in consumed) + values[4] > envelope['calls']
                        or sum(Decimal(a['values'][3]) for a in consumed) + cost > Decimal(envelope['usd'])):
                    raise BudgetDenied('Maintenance job envelope exhausted')
            # The digest coordinates in-flight work without keeping buyer-supplied URLs in the ledger.
            resource_id = sha256((source + '\n' + resource).encode()).hexdigest() if resource and source else None
            if resource_id and any(a.get('resource') == resource_id for a in live):
                raise BudgetDenied('Another process is fetching this resource')
            if kind == 'worker' and sum(a['kind'] == 'worker' and a['job'] == job_id for a in live) >= 3:
                raise BudgetDenied('Three research workers already own this run')
            # Background work cannot occupy all provider slots; two slots and the
            # final $1 of campaign capacity remain available for interactive work.
            if job['kind'] != 'buyer' and sum(a['background'] for a in live) >= 2:
                raise BudgetDenied('Background capacity is busy')
            if source is not None:
                positive = (limits.requests_per_minute, limits.concurrency, limits.max_requests_per_run)
                if any(type(v) is not int or v <= 0 for v in positive):
                    raise ValueError('Source workload bounds must be positive integers')
                matching = attempts.matching((entries.c.source == source) & (entries.c.status != 'released')
                                             & or_(entries.c.created > now - 60, entries.c.job_id == job_id))
                if (sum(a['source'] == source for a in live) >= limits.concurrency
                    or sum(a['created'] > now - 60 for a in matching) >= limits.requests_per_minute
                    or sum(a['job'] == job_id for a in matching) >= limits.max_requests_per_run):
                    raise BudgetDenied('Shared source workload limit')
            keys = self._scope_keys(job_id, job, international)
            for key, profile in keys:
                used = state['scopes'].setdefault(key, [0, 0, 0, '0', 0])
                cap = list(LIMITS[profile])
                if profile == 'buyer' and not job['final']:
                    cap[0], cap[1], cap[3], cap[4] = 30, 30, '0.75', 30
                if profile == 'project' and job['kind'] != 'buyer':
                    cap[3] = '499'
                for i, bound in enumerate(cap):
                    if bound is not None and Decimal(str(used[i])) + Decimal(str(values[i])) > Decimal(str(bound)):
                        raise BudgetDenied(f'{key} exhausted: {("fetches", "paid calls", "active time", "spend", "calls")[i]}')
            for key, _ in keys:
                self._add(state['scopes'][key], values)
            token = uuid.uuid4().hex
            attempts[token] = dict(job=job_id, kind=kind, source=source, created=now, expires=now + seconds,
                status='held', owner=uuid.uuid4().hex, scopes=[key for key, _ in keys], values=values,
                background=job['kind'] != 'buyer', fees={k: str(getattr(charge, k)) for k in ('provider','gateway','payment','byok')})
            owner = attempts[token]['owner']
            if resource_id:
                attempts[token]['resource'] = resource_id
        return Attempt(self, token, owner)

    @staticmethod
    def _add(used, values, sign=1):
        for i in range(5):
            used[i] = str(Decimal(used[i]) + sign * Decimal(values[i])) if i == 3 else used[i] + sign * values[i]

    def _change(self, token, owner, action, actual=None, elapsed=None):
        overrun = False
        with self._transaction() as (state, now):
            attempt = state['attempts'][token]
            if attempt['owner'] != owner or attempt['status'] not in {'held', 'sent'}:
                raise BudgetDenied('Reservation owner is stale or attempt is already settled')
            if action == 'send':
                job = state['jobs'][attempt['job']]
                if (attempt['status'] != 'held' or now >= attempt['expires'] or job['cancelled'] or state['blocked']):
                    raise BudgetDenied('Reservation expired or cancelled before dispatch')
                attempt['status'] = 'sent'
                attempt['created'] = now
            elif action == 'release':
                if attempt['status'] != 'held' or now >= attempt['expires']:
                    raise BudgetDenied('Only an unexpired, unsent attempt can be released')
                for key in attempt['scopes']:
                    self._add(state['scopes'][key], attempt['values'], -1)
                attempt['status'] = 'released'
            else:
                if attempt['status'] != 'sent' or now >= attempt['expires']:
                    raise BudgetDenied('Late completion cannot refund an uncertain reservation')
                if actual is None:
                    attempt['status'] = 'uncertain'
                else:
                    money(actual)
                    if elapsed is None or not math.isfinite(elapsed) or elapsed < 0:
                        raise ValueError('Completion needs a finite elapsed duration')
                    used = list(attempt['values'])
                    used[2], used[3] = math.ceil(elapsed * 1000), str(actual)
                    overrun = actual > Decimal(attempt['values'][3]) or used[2] > attempt['values'][2]
                    for key in attempt['scopes']:
                        self._add(state['scopes'][key], attempt['values'], -1)
                        self._add(state['scopes'][key], used)
                    attempt['values'], attempt['status'] = used, 'completed'
                    if overrun:
                        state['blocked'] = True
        if overrun:
            raise BudgetDenied('Provider exceeded its bound; campaign stopped for reconciliation')

    def recover(self):
        """Expire ownership; keep ALL attempted/held resources, never replay calls."""
        with self._transaction() as (state, now):
            expired = entries.c.status.in_(('held', 'sent')) & (entries.c.expires <= now)
            for attempt in state['attempts'].matching(expired):
                attempt['status'] = 'uncertain'
                attempt['owner'] = None

    def credit_purchase(self, receipt_id, credit_usd, fee_usd):
        """Funding is not consumption; only purchase fees enter spend here."""
        money(credit_usd), money(fee_usd)
        if not receipt_id:
            raise ValueError('An idempotent purchase receipt is required')
        with self._transaction() as (state, _):
            receipt = [str(credit_usd), str(fee_usd)]
            if receipt_id in state['credits']:
                if state['credits'][receipt_id] != receipt:
                    raise ValueError('Receipt conflicts with existing funding record')
                return
            used = state['scopes'].setdefault('project', [0, 0, 0, '0', 0])
            if Decimal(used[3]) + fee_usd > 500 or state['blocked']:
                raise BudgetDenied('Purchase fee exceeds project allowance')
            used[3] = str(Decimal(used[3]) + fee_usd)
            state['credits'][receipt_id] = receipt

    def record(self, section, key):
        with self._transaction(readonly=True) as (state, _):
            return state[section][key]

    def job_attempts(self, job_id, *, prefix=False):
        with self._transaction(readonly=True) as (state, _):
            return state['attempts'].for_job(job_id, prefix=prefix)

    def snapshot(self):
        with self._transaction(readonly=True) as (state, _):
            return materialize(state)


class Attempt:
    def __init__(self, ledger, token, owner):
        self.ledger, self.token, self.owner = ledger, token, owner

    def dispatch(self):
        self.ledger._change(self.token, self.owner, 'send')

    def record(self, metadata):
        """Attach credential-free provider accounting evidence to this attempt.

        Settlement/recovery never discards it. A late network worker cannot write
        after ownership expires; callers must record only from the waiting thread.
        """
        import json
        safe = json.loads(json.dumps(metadata, allow_nan=False))
        with self.ledger._transaction() as (state, now):
            attempt = state['attempts'][self.token]
            if (attempt['owner'] != self.owner or attempt['status'] not in {'held', 'sent'}
                    or now >= attempt['expires']):
                raise BudgetDenied('Provider accounting owner expired')
            attempt['provider_usage'] = safe

    def complete(self, actual, elapsed):
        self.ledger._change(self.token, self.owner, 'complete', actual, elapsed)

    def uncertain(self):
        self.ledger._change(self.token, self.owner, 'complete')

    def release(self):
        self.ledger._change(self.token, self.owner, 'release')


class Job:
    def __init__(self, ledger, job_id):
        self.ledger, self.run_id = ledger, job_id

    def reserve(self, kind, charge, seconds, **kwargs):
        return self.ledger.reserve(self.run_id, kind, charge, seconds, **kwargs)

    def enter_final_phase(self):
        with self.ledger._transaction() as (state, _):
            state['jobs'][self.run_id]['final'] = True

    def cancel(self):
        with self.ledger._transaction() as (state, _):
            state['jobs'][self.run_id]['cancelled'] = True

    @property
    def cancelled(self):
        with self.ledger._transaction(readonly=True) as (state, _):
            return state['jobs'].boolean_field(self.run_id, 'cancelled')

    def source_budget(self, registry_path, *, international=False):
        return SourceBudget(self, registry_path, international)


class SourceBudget:
    """SourceGate protocol adapter; each retry/redirect gets its own shared lease."""
    def __init__(self, job, registry_path, international):
        self.job, self.registry_path, self.international = job, registry_path, international
        self.run_id = job.run_id

    def reserve_call(self, source_id, max_seconds):
        return self.reserve_resource(source_id, None, max_seconds)

    def reserve_resource(self, source_id, url, max_seconds):
        from app.contracts import SourcePolicy
        from app.source_registry import get_active_source
        policy = SourcePolicy.model_validate(get_active_source(source_id, self.registry_path))
        try:
            return SourceAttempt(self.job.reserve('retrieval', Charge(Decimal(0)), max_seconds,
                source=source_id, limits=policy.limits, international=self.international, resource=url))
        except BudgetDenied:
            return None


class SourceAttempt:
    def __init__(self, attempt):
        self.attempt, self.sent = attempt, False

    def still_wanted(self):
        try:
            self.attempt.dispatch()
            self.sent = True
            return True
        except BudgetDenied:
            return False

    def commit(self, elapsed_seconds):
        # Expired network requests retain the full bound; recovery frees only slots.
        try:
            self.attempt.complete(Decimal(0), elapsed_seconds)
        except BudgetDenied:
            self.attempt.ledger.recover()

    def release(self):
        if not self.sent:
            try:
                self.attempt.release()
            except BudgetDenied:
                self.attempt.ledger.recover()
