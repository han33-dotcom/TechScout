"""Owned run application service, shared by HTTP and separate worker processes.

Database transactions serialize admission and state transitions, never model or
retailer calls. Lease tokens reject late writes; clarification preserves used time.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
import secrets
import uuid

from sqlalchemy import select, insert, update, delete, func

from app import contracts_v2 as v
from app.costs import FxRate, calculate_domestic_cost, supports_domestic_cost
from app.storage import buyer_guard, buyer_sessions, buyer_runs
from app.buyer_history import HistoryEntry, HistoryList, HistoricalWorkspace, DeletionResult

ACTIVE = {'queued','running','clarification'}
TERMINAL = {'complete','partial','abstained','cancelled','error'}


class RunError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


@dataclass(frozen=True)
class Claim:
    run: v.RunReference
    token: str
    snapshot: v.RunSnapshot
    answers: tuple[dict, ...]
    remaining_ms: int


def session_hash(token):
    return sha256(token.encode()).hexdigest()


class BuyerRunService:
    def __init__(self, storage, *, clock=None, max_running=2, max_pending=20, active_seconds=120):
        if (type(max_running) is not int or type(max_pending) is not int
                or not 1 <= max_running <= max_pending <= 100 or not 0 < active_seconds <= 120):
            raise ValueError('Positive bounded run limits are required')
        self.storage, self.clock = storage, clock
        self.max_running, self.max_pending = max_running, max_pending
        self.limit_ms = math.ceil(active_seconds*1000)

    @contextmanager
    def _transaction(self):
        with self.storage.transaction() as db:
            if db.execute(update(buyer_guard).where(buyer_guard.c.id==1).values(id=1)).rowcount != 1:
                raise RunError(503,'Buyer run database is not migrated')
            now = self.clock() if self.clock else db.execute(select(
                func.clock_timestamp() if db.dialect.name=='postgresql'
                else func.strftime('%Y-%m-%dT%H:%M:%fZ','now'))).scalar_one()
            if isinstance(now,str):
                now = datetime.fromisoformat(now)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            yield db, now.astimezone(timezone.utc)

    def session(self, token=None):
        with self._transaction() as (db,now):
            row = self._session(db,now,token,required=False)
            if row:
                return token,row['csrf'],row['expires_at']
            # Expired private sessions cascade to their runs; they are never
            # copied into the public evidence corpus.
            db.execute(delete(buyer_sessions).where(buyer_sessions.c.expires_at<=now))
            if db.execute(select(func.count()).select_from(buyer_sessions)).scalar_one() >= 10000:
                raise RunError(429,'Session capacity is busy')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = now+timedelta(hours=24)
            db.execute(insert(buyer_sessions).values(session_hash=session_hash(token),csrf=csrf,expires_at=expires))
            return token,csrf,expires

    def _session(self,db,now,token,*,required=True,csrf=None):
        row = None
        if isinstance(token,str) and 20 <= len(token) <= 128:
            row=db.execute(select(buyer_sessions).where(buyer_sessions.c.session_hash==session_hash(token),
                buyer_sessions.c.expires_at>now)).mappings().first()
        if row is None and required:
            raise RunError(401,'Session is missing or expired')
        if row is not None and csrf is not None and not secrets.compare_digest(row['csrf'],csrf):
            raise RunError(403,'CSRF token does not match this session')
        return row

    def authorize(self,token,csrf):
        if not isinstance(csrf,str) or not csrf or len(csrf)>128:
            raise RunError(403,'CSRF token is required')
        with self._transaction() as (db,now):
            self._session(db,now,token,csrf=csrf)

    def _row(self,db,run_id,owner=None):
        query=select(buyer_runs).where(buyer_runs.c.run_id==run_id)
        if owner is not None:
            query=query.where(buyer_runs.c.session_hash==owner)
        row=db.execute(query).mappings().first()
        if row is None:
            raise RunError(404,'Run not found')
        return row

    def _used(self,row,now):
        elapsed=max(0,math.ceil((now-row['active_since']).total_seconds()*1000)) if row['active_since'] else 0
        return row['active_ms']+elapsed

    def _write(self,db,row,snapshot,**fields):
        db.execute(update(buyer_runs).where(buyer_runs.c.run_id==row['run_id']).values(
            snapshot=snapshot.model_dump(mode='json'),status=snapshot.status,**fields))

    def _finish(self,db,row,now,status,reason):
        data=dict(row['snapshot'])
        data.update(status=status,updated_at=now,finished_at=now,stop_reason=reason,clarification=None)
        if status not in {'complete','partial'}:
            data['recommendations']=[]
        if status not in {'complete','partial','abstained'}:
            data['explanation']=None
        snapshot=v.RunSnapshot.model_validate(data)
        self._write(db,row,snapshot,active_ms=self._used(row,now),active_since=None,lease_token=None,lease_until=None)
        return snapshot

    def _recover(self,db,now):
        db.execute(delete(buyer_sessions).where(buyer_sessions.c.expires_at<=now))
        rows=db.execute(select(buyer_runs).where(buyer_runs.c.status=='running')).mappings().all()
        for row in rows:
            if row['lease_until'] is None or row['lease_until']<=now or self._used(row,now)>=self.limit_ms:
                self._finish(db,row,now,'partial','Execution limit reached or worker interrupted; no automatic retry')

    def recover(self):
        with self._transaction() as (db,now):
            self._recover(db,now)

    def _admit(self,db,owner):
        if db.execute(select(func.count()).select_from(buyer_runs).where(buyer_runs.c.status.in_(ACTIVE))).scalar_one() >= self.max_pending:
            raise RunError(429,'Run capacity is busy; retry later')
        if db.execute(select(func.count()).select_from(buyer_runs).where(
                buyer_runs.c.session_hash==owner,buyer_runs.c.status.in_(ACTIVE))).scalar_one():
            raise RunError(409,'This session already has an active run; refine or cancel it')

    def _new(self,db,now,owner,request,previous=None):
        self._admit(db,owner)
        run=v.RunReference(run_id=str(uuid.uuid4()),run_revision=1,request_id=request.request_id,request_revision=request.revision)
        snapshot=v.RunSnapshot(run=run,request=request,status='queued',phase='interpret',started_at=now,updated_at=now)
        db.execute(insert(buyer_runs).values(run_id=run.run_id,session_hash=owner,request_id=request.request_id,
            request_revision=request.revision,status='queued',snapshot=snapshot.model_dump(mode='json'),
            answers=[],previous_id=previous,active_ms=0))
        return snapshot

    def start(self,token,request: v.ShoppingRequest):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            self._recover(db,now)
            if request.revision!=1 or db.execute(select(buyer_runs.c.run_id).where(
                    buyer_runs.c.session_hash==owner,buyer_runs.c.request_id==request.request_id)).first():
                raise RunError(409,'A new request needs a new ID and revision 1')
            return self._new(db,now,owner,request)

    def _workspace(self,db,owner,row):
        current=db.execute(select(buyer_runs).where(buyer_runs.c.session_hash==owner,
            buyer_runs.c.request_id==row['request_id']).order_by(buyer_runs.c.request_revision.desc()).limit(1)).mappings().one()
        previous=self._row(db,current['previous_id'],owner)['snapshot'] if current['previous_id'] else None
        return v.ComparisonWorkspace(current=v.RunSnapshot.model_validate(current['snapshot']),previous=previous)

    def read(self,token,run_id):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            self._recover(db,now)
            return self._workspace(db,owner,self._row(db,run_id,owner))

    def history(self,token,limit=20):
        if type(limit) is not int or not 1<=limit<=20:
            raise RunError(422,'History limit must be between 1 and 20')
        with self._transaction() as (db,now):
            session=self._session(db,now,token)
            owner=session['session_hash']
            self._recover(db,now)
            newer=buyer_runs.alias('newer')
            latest=~select(1).where(newer.c.session_hash==owner,
                newer.c.request_id==buyer_runs.c.request_id,
                newer.c.request_revision>buyer_runs.c.request_revision).exists()
            # Contracts normalize UTC timestamps to Z. Remove that suffix so
            # a whole second sorts before its fractional timestamps as well.
            updated=func.replace(buyer_runs.c.snapshot['updated_at'].as_string(),'Z','')
            rows=db.execute(select(buyer_runs).where(buyer_runs.c.session_hash==owner,latest)
                .order_by(updated.desc(),buyer_runs.c.run_id)
                .limit(limit)).mappings().all()
            return HistoryList(expires_at=session['expires_at'],items=tuple(HistoryEntry(
                run_id=row['run_id'],revision=row['request_revision'],status=row['status'],
                category=row['snapshot']['request']['requirements']['category'],
                updated_at=row['snapshot']['updated_at']) for row in rows))

    def reopen(self,token,run_id):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            self._recover(db,now)
            row=self._row(db,run_id,owner)
            previous=self._row(db,row['previous_id'],owner)['snapshot'] if row['previous_id'] else None
            return HistoricalWorkspace(workspace=v.ComparisonWorkspace(current=row['snapshot'],previous=previous))

    def delete_history(self,token,run_id):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            self._recover(db,now)
            row=self._row(db,run_id,owner)
            chain=(buyer_runs.c.session_hash==owner,buyer_runs.c.request_id==row['request_id'])
            if db.execute(select(buyer_runs.c.run_id).where(*chain,buyer_runs.c.status.in_(ACTIVE))).first():
                raise RunError(409,'Cancel the active search before deleting its history')
            db.execute(delete(buyer_runs).where(*chain))
            return DeletionResult()

    @staticmethod
    def _reference(row,run):
        if v.RunReference.model_validate(row['snapshot']['run']) != run:
            raise RunError(409,'Run revision is stale')

    def cancel(self,token,run):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            row=self._row(db,run.run_id,owner)
            self._reference(row,run)
            if row['status'] in TERMINAL:
                return v.RunSnapshot.model_validate(row['snapshot'])
            return self._finish(db,row,now,'cancelled','Cancelled by buyer')

    def refine(self,token,run,request):
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            self._recover(db,now)
            row=self._row(db,run.run_id,owner)
            self._reference(row,run)
            workspace=self._workspace(db,owner,row)
            if (workspace.current.run!=run or request.request_id!=run.request_id
                    or request.revision!=run.request_revision+1):
                raise RunError(409,'Refinement requires the current request and next revision')
            if row['status'] in ACTIVE:
                self._finish(db,row,now,'cancelled','Superseded by buyer refinement')
            current=self._new(db,now,owner,request,previous=run.run_id)
            return self._workspace(db,owner,self._row(db,current.run.run_id,owner))

    def clarify(self,token,run,clarification_id,answer):
        if not isinstance(answer,str) or not 1<=len(answer.strip())<=4000:
            raise RunError(422,'Clarification answer must contain 1–4000 characters')
        with self._transaction() as (db,now):
            owner=self._session(db,now,token)['session_hash']
            row=self._row(db,run.run_id,owner)
            self._reference(row,run)
            snap=v.RunSnapshot.model_validate(row['snapshot'])
            if snap.status!='clarification' or snap.clarification.clarification_id!=clarification_id:
                raise RunError(409,'Clarification is no longer waiting')
            if len(row['answers'])>=6:
                raise RunError(409,'Clarification limit reached; refine the request explicitly')
            data=snap.model_dump()
            data['run']['run_revision']+=1
            data.update(status='queued',clarification=None,updated_at=now)
            snap=v.RunSnapshot.model_validate(data)
            answers=[*row['answers'],dict(clarification_id=clarification_id,answer=answer)]
            self._write(db,row,snap,answers=answers)
            return snap

    def claim(self):
        with self._transaction() as (db,now):
            self._recover(db,now)
            if db.execute(select(func.count()).select_from(buyer_runs).where(buyer_runs.c.status=='running')).scalar_one()>=self.max_running:
                return None
            row=db.execute(select(buyer_runs).join(buyer_sessions).where(buyer_runs.c.status=='queued',
                buyer_sessions.c.expires_at>now).order_by(buyer_runs.c.snapshot['started_at'].as_string(),
                    buyer_runs.c.run_id).limit(1)).mappings().first()
            if row is None:
                return None
            if row['active_ms']>=self.limit_ms:
                self._finish(db,row,now,'partial','Active execution allowance exhausted')
                return None
            data=dict(row['snapshot'])
            data.update(status='running',updated_at=now)
            snap=v.RunSnapshot.model_validate(data)
            token=uuid.uuid4().hex
            self._write(db,row,snap,lease_token=token,lease_until=now+timedelta(seconds=15),active_since=now)
            return Claim(snap.run,token,snap,tuple(row['answers']),self.limit_ms-row['active_ms'])

    def _owned_claim(self,db,now,claim):
        row=self._row(db,claim.run.run_id)
        session=db.execute(select(buyer_sessions).where(buyer_sessions.c.session_hash==row['session_hash'],
            buyer_sessions.c.expires_at>now)).first()
        self._reference(row,claim.run)
        if (session is None or row['status']!='running' or row['lease_token']!=claim.token
                or row['lease_until']<=now or self._used(row,now)>=self.limit_ms):
            raise RunError(409,'Worker ownership expired or run stopped')
        return row

    def heartbeat(self,claim):
        with self._transaction() as (db,now):
            row=self._owned_claim(db,now,claim)
            db.execute(update(buyer_runs).where(buyer_runs.c.run_id==row['run_id']).values(lease_until=now+timedelta(seconds=15)))
            return self.limit_ms-self._used(row,now)

    def abandon(self,claim):
        """Close a stopped local controller even between database clock ticks."""
        with self._transaction() as (db,now):
            row=db.execute(select(buyer_runs).where(buyer_runs.c.run_id==claim.run.run_id)).mappings().first()
            if row and row['status']=='running' and row['lease_token']==claim.token:
                self._finish(db,row,now,'partial','Controller stopped or active execution allowance exhausted')

    def publish(self,claim,*,status='running',phase=None,coverage=None,provisional=None,
                uncertain=None,recommendations=None,clarification=None,stop_reason=None,
                source_outcomes=None,explanation=None):
        if status not in {'running','clarification','complete','partial','abstained','error'}:
            raise RunError(422,'Worker status is invalid')
        with self._transaction() as (db,now):
            row=self._owned_claim(db,now,claim)
            data=dict(row['snapshot'])
            data.update(status=status,updated_at=now,finished_at=now if status in TERMINAL else None,
                        clarification=clarification,stop_reason=stop_reason)
            for key,value in dict(phase=phase,coverage=coverage,provisional=provisional,
                                  uncertain=uncertain,recommendations=recommendations,
                                  source_outcomes=source_outcomes,explanation=explanation).items():
                if value is not None:
                    data[key]=value
            snapshot=v.RunSnapshot.model_validate(data)
            if snapshot.recommendations:
                from app.eligibility import check_full_product
                for group in snapshot.recommendations:
                    for checked in group.offers():
                        if check_full_product(snapshot.request,checked.offer).outcome!='pass':
                            raise ValueError('Recommendation does not meet the buyer hard requirements')
                        if any(charge.valid_until is not None and charge.valid_until <= now
                               for charge in checked.offer.costs):
                            raise ValueError('Decisive cost quotes must be current')
                        if supports_domestic_cost(checked.offer):
                            conversion = checked.cost.budget_conversion
                            rates = () if conversion is None else (FxRate(
                                base=conversion.base.currency, quote=conversion.quote.currency,
                                rate=conversion.rate, observed_at=conversion.observed_at,
                                evidence_id=conversion.evidence_id),)
                            calculated = calculate_domestic_cost(snapshot.request, checked.offer,
                                fx=rates, now=checked.verification.checked_at)
                            if calculated.status != 'verified' or calculated.total != checked.cost.total:
                                raise ValueError('Recommendation does not match the verified domestic cost')
            fields={}
            if status!='running':
                fields.update(active_ms=self._used(row,now),active_since=None,lease_token=None,lease_until=None)
            self._write(db,row,snapshot,**fields)
            return snapshot
