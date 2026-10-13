"""Persistence for sources, variants, offers, evidence, runs and actions.

Contracts stay the source of truth: rows keep queryable columns plus the validated
contract JSON, money stays Decimal, and raw responses live in a hash-addressed
snapshot store that the web application never serves.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from sqlalchemy import (
    JSON, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, MetaData, Numeric,
    String, Table, Text, TypeDecorator, UniqueConstraint, create_engine, delete, func,
    insert, select, update,
)

from app.contracts import BudgetUsage, Money, Offer, RunResult, ShoppingRequest, SourcePolicy, ToolResult

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{ROOT / 'techscout.db'}"
DEFAULT_EVIDENCE_DIR = ROOT / 'var' / 'evidence'
RUN_STATES = ('running', 'complete', 'partial', 'abstained', 'cancelled', 'error')
SHA256 = re.compile(r'^[0-9a-f]{64}$')


class ExactMoney(TypeDecorator):
    """NUMERIC on Postgres; a decimal string where the database has no exact decimal."""
    impl = Numeric()
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(Numeric(asdecimal=True))
        return dialect.type_descriptor(String(40))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise TypeError('Money must be a finite Decimal')
        return value if dialect.name == 'postgresql' else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class UtcDateTime(TypeDecorator):
    """Aware UTC timestamps on every dialect; naive values are rejected."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Timestamps need a timezone')
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


metadata = MetaData(naming_convention={
    'pk': 'pk_%(table_name)s', 'fk': 'fk_%(table_name)s_%(column_0_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s', 'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'ix': 'ix_%(table_name)s_%(column_0_name)s',
})

sources = Table(
    'sources', metadata,
    Column('id', String(100), primary_key=True),
    Column('name', Text, nullable=False),
    Column('origin', Text, nullable=False),
    Column('status', String(20), nullable=False),
    Column('reviewed_at', Date, nullable=False),
    Column('policy', JSON, nullable=False),
    Column('updated_at', UtcDateTime, nullable=False),
)

variants = Table(
    'variants', metadata,
    Column('id', Integer, primary_key=True),
    Column('source_id', ForeignKey('sources.id'), nullable=False),
    Column('product_id', Text, nullable=False),
    Column('variant_id', Text, nullable=False),
    Column('payload', JSON, nullable=False),
    UniqueConstraint('source_id', 'product_id', 'variant_id'),
)

snapshots = Table(
    'snapshots', metadata,
    Column('sha256', String(64), primary_key=True),
    Column('size_bytes', Integer, nullable=False),
    Column('media_type', String(100), nullable=False),
    Column('stored_at', UtcDateTime, nullable=False),
)

evidence = Table(
    'evidence', metadata,
    Column('evidence_id', String(200), primary_key=True),
    Column('source_id', ForeignKey('sources.id'), nullable=False),
    Column('product_id', Text),
    Column('variant_id', Text),
    Column('scope', String(20), nullable=False),
    Column('field_path', Text, nullable=False),
    Column('observed_at', UtcDateTime, nullable=False),
    Column('response_sha256', String(64), nullable=False, index=True),
    Column('payload', JSON, nullable=False),
)

offers = Table(
    'offers', metadata,
    Column('offer_id', String(200), primary_key=True),
    Column('source_id', ForeignKey('sources.id'), nullable=False),
    Column('variant_pk', ForeignKey('variants.id'), nullable=False),
    Column('price_amount', ExactMoney),
    Column('price_currency', String(3)),
    Column('payload', JSON, nullable=False),
    Column('stored_at', UtcDateTime, nullable=False),
)

offer_evidence = Table(
    'offer_evidence', metadata,
    Column('offer_id', ForeignKey('offers.offer_id', ondelete='CASCADE'), primary_key=True),
    Column('evidence_id', ForeignKey('evidence.evidence_id'), primary_key=True),
)

runs = Table(
    'runs', metadata,
    Column('run_id', String(200), primary_key=True),
    Column('request_id', Text, nullable=False),
    Column('request', JSON, nullable=False),
    Column('status', String(20), nullable=False),
    Column('stop_reason', Text),
    Column('started_at', UtcDateTime, nullable=False),
    Column('finished_at', UtcDateTime),
    Column('external_calls', Integer, nullable=False, default=0),
    Column('planner_turns', Integer, nullable=False, default=0),
    Column('spend_amount', ExactMoney, nullable=False),
    Column('spend_currency', String(3), nullable=False),
    Column('result', JSON),
    CheckConstraint(f"status IN {RUN_STATES}", name='status'),
)

actions = Table(
    'actions', metadata,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('runs.run_id'), nullable=False),
    Column('seq', Integer, nullable=False),
    Column('tool', String(100), nullable=False),
    Column('arguments', JSON, nullable=False),
    Column('status', String(20), nullable=False),
    Column('result', JSON, nullable=False),
    Column('evidence_ids', JSON, nullable=False),
    Column('duration_ms', Integer, nullable=False),
    Column('recorded_at', UtcDateTime, nullable=False),
    UniqueConstraint('run_id', 'seq'),
)


def _now():
    return datetime.now(timezone.utc)


class SnapshotStore:
    """Hash-addressed raw responses. Readers must present the hash an evidence row cites."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, sha256):
        if not isinstance(sha256, str) or not SHA256.match(sha256):
            raise ValueError('Snapshot references are lowercase SHA-256 hex digests')
        return self.root / sha256[:2] / sha256

    def put(self, content: bytes) -> str:
        sha256 = hashlib.sha256(content).hexdigest()
        path = self._path(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(content)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        return sha256

    def get(self, sha256: str) -> bytes:
        content = self._path(sha256).read_bytes()
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError('Snapshot content does not match its hash')
        return content


class Storage:
    def __init__(self, database_url=None, evidence_dir=None):
        url = database_url or os.environ.get('DATABASE_URL', DEFAULT_DATABASE_URL)
        self.engine = create_engine(url, future=True)
        self.snapshots = SnapshotStore(evidence_dir or os.environ.get('EVIDENCE_DIR', DEFAULT_EVIDENCE_DIR))

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield connection

    def ping(self):
        with self.engine.connect() as connection:
            connection.execute(select(1))

    # Sources

    def save_source(self, policy: SourcePolicy):
        row = dict(name=policy.name, origin=str(policy.origin), status=policy.status,
                   reviewed_at=policy.reviewed_at, policy=policy.model_dump(mode='json'),
                   updated_at=_now())
        with self.transaction() as db:
            if db.execute(select(sources.c.id).where(sources.c.id == policy.id)).first():
                db.execute(update(sources).where(sources.c.id == policy.id).values(**row))
            else:
                db.execute(insert(sources).values(id=policy.id, **row))

    def load_source(self, source_id) -> SourcePolicy | None:
        with self.engine.connect() as db:
            row = db.execute(select(sources.c.policy).where(sources.c.id == source_id)).first()
        return None if row is None else SourcePolicy.model_validate(row.policy)

    def list_sources(self, status=None) -> list[SourcePolicy]:
        query = select(sources.c.policy).order_by(sources.c.id)
        if status is not None:
            query = query.where(sources.c.status == status)
        with self.engine.connect() as db:
            return [SourcePolicy.model_validate(r.policy) for r in db.execute(query)]

    # Snapshots and evidence

    def save_snapshot(self, content: bytes, media_type='application/octet-stream') -> str:
        sha256 = self.snapshots.put(content)
        with self.transaction() as db:
            if not db.execute(select(snapshots.c.sha256).where(snapshots.c.sha256 == sha256)).first():
                db.execute(insert(snapshots).values(sha256=sha256, size_bytes=len(content),
                                                    media_type=media_type, stored_at=_now()))
        return sha256

    def load_snapshot(self, sha256) -> bytes:
        with self.engine.connect() as db:
            if not db.execute(select(snapshots.c.sha256).where(snapshots.c.sha256 == sha256)).first():
                raise KeyError('Unknown snapshot')
        return self.snapshots.get(sha256)

    def _save_evidence(self, db, record):
        payload = record.model_dump(mode='json')
        existing = db.execute(select(evidence.c.payload)
                              .where(evidence.c.evidence_id == record.evidence_id)).first()
        if existing is not None:
            if existing.payload != payload:
                raise ValueError(f'Evidence {record.evidence_id} is immutable once stored')
            return
        db.execute(insert(evidence).values(
            evidence_id=record.evidence_id, source_id=record.source_id,
            product_id=record.product_id, variant_id=record.variant_id, scope=record.scope,
            field_path=record.field_path, observed_at=record.observed_at,
            response_sha256=record.response_sha256, payload=payload))

    # Variants and offers

    def save_offer(self, offer: Offer):
        variant = offer.variant
        key = (variants.c.source_id == variant.source_id) & (
            variants.c.product_id == variant.product_id) & (variants.c.variant_id == variant.variant_id)
        price = offer.price.value if offer.price.state == 'known' else None
        with self.transaction() as db:
            if db.execute(select(sources.c.id).where(sources.c.id == offer.source_id)).first() is None:
                raise ValueError(f'Unknown source {offer.source_id}')
            row = db.execute(select(variants.c.id).where(key)).first()
            if row is None:
                variant_pk = db.execute(insert(variants).values(
                    source_id=variant.source_id, product_id=variant.product_id,
                    variant_id=variant.variant_id, payload=variant.model_dump(mode='json'))
                ).inserted_primary_key[0]
            else:
                variant_pk = row.id
                db.execute(update(variants).where(variants.c.id == variant_pk)
                           .values(payload=variant.model_dump(mode='json')))
            for record in offer.evidence:
                self._save_evidence(db, record)
            db.execute(delete(offers).where(offers.c.offer_id == offer.offer_id))
            db.execute(delete(offer_evidence).where(offer_evidence.c.offer_id == offer.offer_id))
            db.execute(insert(offers).values(
                offer_id=offer.offer_id, source_id=offer.source_id, variant_pk=variant_pk,
                price_amount=price.amount if price else None,
                price_currency=price.currency if price else None,
                payload=offer.model_dump(mode='json'), stored_at=_now()))
            for record in offer.evidence:
                db.execute(insert(offer_evidence).values(offer_id=offer.offer_id,
                                                         evidence_id=record.evidence_id))

    def load_offer(self, offer_id) -> Offer | None:
        with self.engine.connect() as db:
            row = db.execute(select(offers.c.payload).where(offers.c.offer_id == offer_id)).first()
        return None if row is None else Offer.model_validate(row.payload)

    def offer_price(self, offer_id) -> Money | None:
        with self.engine.connect() as db:
            row = db.execute(select(offers.c.price_amount, offers.c.price_currency)
                             .where(offers.c.offer_id == offer_id)).first()
        return None if row is None or row.price_amount is None else Money(
            amount=row.price_amount, currency=row.price_currency)

    # Runs and actions

    def start_run(self, run_id, request: ShoppingRequest, started_at=None, currency='USD'):
        with self.transaction() as db:
            db.execute(insert(runs).values(
                run_id=run_id, request_id=request.request_id,
                request=request.model_dump(mode='json'), status='running',
                started_at=started_at or _now(), external_calls=0, planner_turns=0,
                spend_amount=Decimal('0'), spend_currency=currency))

    def record_action(self, run_id, tool, arguments, result: ToolResult, external_calls=0,
                      planner_turns=0, spend: Decimal = Decimal('0')):
        with self.transaction() as db:
            run = db.execute(select(runs.c.status, runs.c.spend_amount)
                             .where(runs.c.run_id == run_id).with_for_update()).first()
            if run is None or run.status != 'running':
                raise ValueError('Actions can only be recorded on a running run')
            seq = db.execute(select(func.coalesce(func.max(actions.c.seq), 0))
                             .where(actions.c.run_id == run_id)).scalar_one() + 1
            db.execute(insert(actions).values(
                run_id=run_id, seq=seq, tool=tool, arguments=json.loads(json.dumps(arguments)),
                status=result.status, result=result.model_dump(mode='json'),
                evidence_ids=list(result.evidence_ids), duration_ms=result.duration_ms,
                recorded_at=_now()))
            db.execute(update(runs).where(runs.c.run_id == run_id).values(
                external_calls=runs.c.external_calls + external_calls,
                planner_turns=runs.c.planner_turns + planner_turns,
                spend_amount=run.spend_amount + spend))
            return seq

    def list_actions(self, run_id):
        with self.engine.connect() as db:
            return [dict(r._mapping) for r in db.execute(
                select(actions.c.seq, actions.c.tool, actions.c.status, actions.c.result)
                .where(actions.c.run_id == run_id).order_by(actions.c.seq))]

    def finish_run(self, result: RunResult):
        with self.transaction() as db:
            changed = db.execute(update(runs).where(
                (runs.c.run_id == result.run_id) & (runs.c.status == 'running')).values(
                status=result.status, stop_reason=result.stop_reason,
                finished_at=result.finished_at, result=result.model_dump(mode='json'))).rowcount
        if changed != 1:
            raise ValueError('Only a running run can be finished')

    def run_status(self, run_id):
        with self.engine.connect() as db:
            row = db.execute(select(runs.c.status).where(runs.c.run_id == run_id)).first()
        return None if row is None else row.status

    def load_run(self, run_id) -> RunResult | None:
        with self.engine.connect() as db:
            row = db.execute(select(runs.c.result).where(runs.c.run_id == run_id)).first()
        return None if row is None or row.result is None else RunResult.model_validate(row.result)

    def recover_interrupted_runs(self, now=None) -> list[str]:
        """Close runs left 'running' by a crash as partial; never resume stale searches."""
        now = now or _now()
        recovered = []
        with self.transaction() as db:
            stale = db.execute(select(runs).where(runs.c.status == 'running')).all()
            for run in stale:
                finished = max(now, run.started_at)
                result = RunResult(
                    run_id=run.run_id, request_id=run.request_id, status='partial',
                    stop_reason='interrupted: the service stopped before this run finished',
                    started_at=run.started_at, finished_at=finished,
                    usage=BudgetUsage(
                        elapsed_ms=int((finished - run.started_at).total_seconds() * 1000),
                        external_calls=run.external_calls, planner_turns=run.planner_turns,
                        spend=Money(amount=run.spend_amount, currency=run.spend_currency)))
                db.execute(update(runs).where(
                    (runs.c.run_id == run.run_id) & (runs.c.status == 'running')).values(
                    status='partial', stop_reason=result.stop_reason, finished_at=finished,
                    result=result.model_dump(mode='json')))
                recovered.append(run.run_id)
        return recovered


def load_fixtures(storage: Storage, sources_path=ROOT / 'config' / 'sources.json',
                  contracts_path=ROOT / 'tests' / 'fixtures' / 'contracts.json'):
    """Load the reviewed source registry and the synthetic contract example."""
    registry = json.loads(Path(sources_path).read_text(encoding='utf-8'))
    for entry in registry['sources']:
        storage.save_source(SourcePolicy.model_validate(entry))
    example = json.loads(Path(contracts_path).read_text(encoding='utf-8'))
    offer = Offer.model_validate(example['offer'])
    if storage.load_source(offer.source_id) is None:
        # The example uses a fictional seller; register it as a paused synthetic source.
        storage.save_source(SourcePolicy(
            id=offer.source_id, name='Synthetic example seller', origin='https://seller.example',
            status='paused', reviewed_at=offer.evidence[0].observed_at.date(), admission=None,
            allowed_endpoints=[], limits=dict(requests_per_minute=0, concurrency=0,
                                              max_requests_per_run=0)))
    storage.save_offer(offer)
    return len(registry['sources']), 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['load-fixtures']:
        print('usage: python -m app.storage load-fixtures', file=sys.stderr)
        return 2
    count, offers_loaded = load_fixtures(Storage())
    print(f'Loaded {count} sources and {offers_loaded} synthetic offer')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
