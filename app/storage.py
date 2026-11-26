"""Persistence for sources, variants, offers, evidence, runs and actions.

Contracts stay the source of truth: rows keep queryable columns plus the validated
contract JSON, money stays Decimal, and raw responses live in a hash-addressed
snapshot store that the web application never serves.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from pgvector.sqlalchemy import VECTOR
from sqlalchemy.exc import IntegrityError
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData, Numeric,
    String, Table, Text, TypeDecorator, URL, UniqueConstraint, create_engine, delete, func,
    event, insert, literal_column, select, update,
)

from app.contracts import BudgetUsage, Money, Offer, RunResult, ShoppingRequest, SourcePolicy, ToolResult

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{ROOT / 'techscout.db'}"
DEFAULT_EVIDENCE_DIR = ROOT / 'var' / 'evidence'
RUN_STATES = ('running', 'complete', 'partial', 'abstained', 'cancelled', 'error')
# How long a run may stay 'running' before recovery treats it as abandoned. Every run's time cap
# ends well inside it (app.runs.RunCaps), so a run still live in another process, such as a
# command-line search beside the web app, is never closed.
RUN_LEASE = timedelta(minutes=10)
# A run's time cap stays well inside the lease, so a request or planner call in flight at the cap,
# a slow lookup or a slow database write never makes a live run look abandoned. Checked by
# app.runs.RunCaps and by the evaluation config (app.planner.EvaluationCaps).
MAX_RUN_SECONDS = RUN_LEASE.total_seconds() / 2
INTERRUPTED = 'interrupted: the service stopped before this run finished'
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

directory_entries = Table(
    'directory_entries', metadata,
    Column('retailer_id', Text, primary_key=True),
    Column('updated_at', UtcDateTime, nullable=False),
    Column('payload', JSON, nullable=False),
)

directory_categories = Table(
    'directory_categories', metadata,
    Column('retailer_id', ForeignKey('directory_entries.retailer_id', ondelete='CASCADE'), primary_key=True),
    Column('category', String(20), primary_key=True),
    Index('ix_directory_categories_category', 'category'),
)

directory_markets = Table(
    'directory_markets', metadata,
    Column('retailer_id', ForeignKey('directory_entries.retailer_id', ondelete='CASCADE'), primary_key=True),
    Column('market', String(2), primary_key=True),
    Index('ix_directory_markets_market', 'market'),
)

directory_aliases = Table(
    'directory_aliases', metadata,
    Column('retailer_id', ForeignKey('directory_entries.retailer_id', ondelete='CASCADE'), primary_key=True),
    Column('alias', Text, primary_key=True),
    Index('ix_directory_aliases_alias', 'alias'),
)

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


# Shared public corpus. Legacy buyer snapshots have their own namespace and retention.
corpus_guard = Table(
    'corpus_guard', metadata,
    Column('id', Integer, primary_key=True, autoincrement=False),
)

corpus_resources = Table(
    'corpus_resources', metadata,
    Column('resource_id', String(64), primary_key=True),
    Column('source_id', ForeignKey('sources.id'), nullable=False, index=True),
    Column('resource_key', JSON, nullable=False),
    Column('checked_at', UtcDateTime, nullable=False),
    Column('check_status', String(16), nullable=False),
    Column('validated_at', UtcDateTime),
    Column('content_changed_at', UtcDateTime),
    Column('etag', Text),
    Column('last_modified', Text),
    Column('needs_processing', Boolean, nullable=False),
)

corpus_retirements = Table(
    'corpus_retirements', metadata,
    Column('id', Integer, primary_key=True),
    Column('resource_id', ForeignKey('corpus_resources.resource_id', ondelete='CASCADE'), nullable=False),
    Column('observed_at', UtcDateTime, nullable=False),
    Column('status', String(16), nullable=False),
    Column('evidence', JSON, nullable=False),
    Index('ix_corpus_retirements_resource_time', 'resource_id', 'observed_at'),
)

corpus_documents = Table(
    'corpus_documents', metadata,
    Column('document_id', Integer, primary_key=True),
    Column('resource_id', ForeignKey('corpus_resources.resource_id', ondelete='CASCADE'), nullable=False),
    Column('final_url', Text, nullable=False),
    Column('observed_at', UtcDateTime, nullable=False, index=True),
    Column('raw_sha256', String(64), nullable=False),
    Column('snapshot_sha256', String(64), index=True),
    Column('snapshot_observed_at', UtcDateTime),
    Column('relevant_sha256', String(64), nullable=False),
    Column('origin', String(16), nullable=False),
    Column('processing', JSON, nullable=False),
    Index('ix_corpus_documents_resource_version', 'resource_id', 'document_id'),
    sqlite_autoincrement=True,
)

corpus_receipts = Table(
    'corpus_receipts', metadata,
    Column('evidence_id', Text, primary_key=True),
    Column('source_document_id', ForeignKey('corpus_documents.document_id', ondelete='CASCADE'),
           nullable=False, index=True),
    Column('payload', JSON, nullable=False),
)

corpus_observations = Table(
    'corpus_observations', metadata,
    Column('observation_id', Integer, primary_key=True),
    Column('source_document_id', ForeignKey('corpus_documents.document_id', ondelete='CASCADE'),
           nullable=False, index=True),
    Column('field_key', String(64), nullable=False),
    Column('scope', String(20), nullable=False),
    Column('product_id', Text),
    Column('variant_id', Text),
    Column('branch_id', Text),
    Column('route_id', Text),
    Column('field_path', Text, nullable=False),
    Column('observed_at', UtcDateTime, nullable=False),
    Column('fact_state', String(12), nullable=False),
    Column('fact_value', JSON),
    Column('price_amount', ExactMoney),
    Column('price_currency', String(3)),
    Column('field_sha256', String(64), nullable=False),
    Column('payload', JSON, nullable=False),
    Index('ix_corpus_observations_product_field', 'product_id', 'variant_id', 'field_path'),
    sqlite_autoincrement=True,
)

corpus_passages = Table(
    'corpus_passages', metadata,
    Column('passage_id', Integer, primary_key=True),
    Column('source_document_id', ForeignKey('corpus_documents.document_id', ondelete='CASCADE'),
           nullable=False, index=True),
    Column('passage_key', String(64), nullable=False),
    Column('passage_sha256', String(64), nullable=False),
    Column('text', Text, nullable=False),
    Column('payload', JSON, nullable=False),
    sqlite_autoincrement=True,
)

corpus_document_observations = Table(
    'corpus_document_observations', metadata,
    Column('document_id', ForeignKey('corpus_documents.document_id', ondelete='CASCADE'), primary_key=True),
    Column('observation_id', ForeignKey('corpus_observations.observation_id', ondelete='CASCADE'),
           primary_key=True, index=True),
)

corpus_document_passages = Table(
    'corpus_document_passages', metadata,
    Column('document_id', ForeignKey('corpus_documents.document_id', ondelete='CASCADE'), primary_key=True),
    Column('passage_id', ForeignKey('corpus_passages.passage_id', ondelete='CASCADE'),
           primary_key=True, index=True),
)

buyer_guard = Table('buyer_guard', metadata, Column('id', Integer, primary_key=True, autoincrement=False))

buyer_sessions = Table(
    'buyer_sessions', metadata,
    Column('session_hash', String(64), primary_key=True),
    Column('csrf', String(64), nullable=False),
    Column('expires_at', UtcDateTime, nullable=False, index=True),
)

buyer_runs = Table(
    'buyer_runs', metadata,
    Column('run_id', String(36), primary_key=True),
    Column('session_hash', ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), nullable=False, index=True),
    Column('request_id', Text, nullable=False),
    Column('request_revision', Integer, nullable=False),
    Column('status', String(20), nullable=False, index=True),
    Column('snapshot', JSON, nullable=False),
    Column('answers', JSON, nullable=False),
    Column('previous_id', String(36)),
    Column('lease_token', String(32)),
    Column('lease_until', UtcDateTime),
    Column('active_since', UtcDateTime),
    Column('active_ms', Integer, nullable=False),
    UniqueConstraint('session_hash','request_id','request_revision',name='uq_buyer_request_revision'),
)

# TechScout buyer accounts (issue #187, migration 0010). Passwords are stored only as
# salted scrypt hashes. A signed-in browser session has one buyer_signins row; chats stay
# owned by the session. Saved locations belong to the account and outlive sessions, but
# their street details never enter request snapshots, history, exports or logs.
buyer_accounts = Table(
    'buyer_accounts', metadata,
    Column('account_id', String(36), primary_key=True),
    Column('username', String(32), nullable=False, unique=True),
    Column('password_hash', String(255), nullable=False),
    Column('created_at', UtcDateTime, nullable=False),
)

buyer_signins = Table(
    'buyer_signins', metadata,
    Column('session_hash', ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), primary_key=True),
    Column('account_id', ForeignKey('buyer_accounts.account_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('signed_in_at', UtcDateTime, nullable=False),
)

buyer_locations = Table(
    'buyer_locations', metadata,
    Column('location_id', String(36), primary_key=True),
    Column('account_id', ForeignKey('buyer_accounts.account_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('label', String(40), nullable=False),
    Column('city', String(80), nullable=False),
    Column('street', String(200), nullable=False),
    Column('details', String(200)),
    Column('created_at', UtcDateTime, nullable=False),
)

# One destination decision per chat (session + request ID), shared by every revision of
# that request. recipients lists the retailers allowed to receive the selected address for
# this chat only; quote_state is the hand-off to the pre-order quote operation (#186).
buyer_destinations = Table(
    'buyer_destinations', metadata,
    Column('session_hash', ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), primary_key=True),
    Column('request_id', Text, primary_key=True),
    Column('status', String(10), nullable=False),
    Column('location_id', ForeignKey('buyer_locations.location_id', ondelete='CASCADE'), index=True),
    Column('recipients', JSON, nullable=False),
    Column('quote_state', String(12), nullable=False),
    Column('decided_at', UtcDateTime, nullable=False),
)

# Fixed-window counters for sign-in and registration throttling. Keys hold SHA-256
# digests rather than attempted usernames, and rows are removed within an hour.
buyer_attempts = Table(
    'buyer_attempts', metadata,
    Column('key', String(80), primary_key=True),
    Column('attempts', Integer, nullable=False),
    Column('window_start', UtcDateTime, nullable=False),
)

accounting_state = Table(
    'accounting_state', metadata,
    Column('id', Integer, primary_key=True),
    Column('payload', JSON, nullable=False),
)

# Automatic retailer onboarding (app.onboarding, issue #99). The guard row serializes
# lead claims and capability activation across API and worker processes.
onboarding_guard = Table('onboarding_guard', metadata, Column('id', Integer, primary_key=True, autoincrement=False))

onboarding_leads = Table(
    'onboarding_leads', metadata,
    Column('lead_id', Text, primary_key=True),
    Column('status', String(12), nullable=False),
    Column('priority', Integer, nullable=False),
    Column('attempts', Integer, nullable=False),
    Column('next_attempt_at', UtcDateTime, nullable=False),
    Column('lease_token', String(32)),
    Column('lease_until', UtcDateTime),
    Column('updated_at', UtcDateTime, nullable=False),
    Column('payload', JSON, nullable=False),
    Index('ix_onboarding_leads_status', 'status'),
)

source_activations = Table(
    'source_activations', metadata,
    Column('source_id', String(100), primary_key=True),
    Column('capability_id', String(100), primary_key=True),
    Column('version', String(100), primary_key=True),
    Column('status', String(12), nullable=False),
    Column('activated_at', UtcDateTime, nullable=False),
    Column('changed_at', UtcDateTime, nullable=False),
    Column('payload', JSON, nullable=False),
    Index('ix_source_activations_status', 'status'),
)

onboarding_events = Table(
    'onboarding_events', metadata,
    Column('event_id', Integer, primary_key=True, autoincrement=True),
    Column('lead_id', Text, nullable=False),
    Column('source_id', String(100)),
    Column('kind', String(20), nullable=False),
    Column('recorded_at', UtcDateTime, nullable=False),
    Column('detail', JSON, nullable=False),
    Index('ix_onboarding_events_lead_id', 'lead_id'),
)

embedding_calls = Table(
    'embedding_calls', metadata,
    Column('call_id', String(32), primary_key=True),
    Column('owner_id', Text, nullable=False, index=True),
    Column('purpose', String(10), nullable=False),
    Column('model', Text, nullable=False),
    Column('index_version', Text, nullable=False),
    Column('status', String(12), nullable=False),
    Column('reserved_usd', ExactMoney, nullable=False),
    Column('rate_usd_per_million', ExactMoney, nullable=False),
    Column('tokens', Integer),
    Column('actual_usd', ExactMoney),
    Column('recorded_at', UtcDateTime, nullable=False),
)

corpus_embeddings = Table(
    'corpus_embeddings', metadata,
    Column('passage_id', ForeignKey('corpus_passages.passage_id', ondelete='CASCADE'), primary_key=True),
    Column('index_version', Text, primary_key=True),
    Column('model', Text, primary_key=True),
    Column('dimensions', Integer, primary_key=True),
    Column('embedding', VECTOR().with_variant(JSON(), 'sqlite'), nullable=False),
    Index('ix_corpus_embeddings_profile', 'index_version', 'model', 'dimensions'),
    CheckConstraint('dimensions BETWEEN 1 AND 2000', name='dimensions'),
)

corpus_passages.append_constraint(
    Index('ix_corpus_passages_fulltext', literal_column("to_tsvector('simple'::regconfig, text)"),
          postgresql_using='gin').ddl_if(dialect='postgresql')
)
corpus_embeddings.append_constraint(
    Index('ix_corpus_embeddings_hnsw', literal_column('(embedding::vector(1536))').label('embedding_1536'),
          postgresql_using='hnsw', postgresql_ops={'embedding_1536': 'vector_cosine_ops'},
          postgresql_where=corpus_embeddings.c.dimensions == 1536).ddl_if(dialect='postgresql')
)


def _now():
    return datetime.now(timezone.utc)


def _check_usage(external_calls, planner_turns, spend) -> None:
    # Usage only grows; a negative value would also make recovery's BudgetUsage invalid.
    if not isinstance(spend, Decimal) or not spend.is_finite() or spend < 0:
        raise ValueError('Spend must be a finite, non-negative Decimal')
    if any(type(n) is not int or n < 0 for n in (external_calls, planner_turns)):
        raise ValueError('Budget counters must be non-negative integers')


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
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
                return sha256
        except FileNotFoundError:
            pass
        # Missing or damaged: write under a unique temporary name so concurrent saves can't collide.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f'{sha256}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            temporary.write_bytes(content)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return sha256

    def get(self, sha256: str) -> bytes:
        content = self._path(sha256).read_bytes()
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError('Snapshot content does not match its hash')
        return content

    def remove(self, sha256: str) -> None:
        self._path(sha256).unlink(missing_ok=True)

    def digests(self):
        for path in self.root.glob('[0-9a-f][0-9a-f]/*'):
            if path.is_file() and SHA256.fullmatch(path.name) and path.parent.name == path.name[:2]:
                yield path.name

    def remove_abandoned_writes(self) -> None:
        """Call with all writers to this snapshot namespace excluded."""
        for path in self.root.glob('[0-9a-f][0-9a-f]/*.tmp'):
            if re.fullmatch(r'[0-9a-f]{64}\.\d+\.[0-9a-f]{32}\.tmp', path.name):
                path.unlink(missing_ok=True)


def configured_database_url() -> str | URL:
    if url := os.environ.get('DATABASE_URL'):
        return url
    if host := os.environ.get('TECHSCOUT_DB_HOST'):
        return URL.create('postgresql+psycopg', host=host,
                          username=os.environ['POSTGRES_USER'],
                          password=os.environ['POSTGRES_PASSWORD'],
                          database=os.environ['POSTGRES_DB'],
                          query={'connect_timeout': '5'})
    return DEFAULT_DATABASE_URL


def _sqlite_foreign_keys(connection, _):
    connection.execute('PRAGMA foreign_keys = ON')


class Storage:
    def __init__(self, database_url=None, evidence_dir=None):
        url = database_url or configured_database_url()
        self.engine = create_engine(url, future=True)
        if self.engine.dialect.name == 'sqlite':
            event.listen(self.engine, 'connect', _sqlite_foreign_keys)
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
        try:
            with self.transaction() as db:
                if not db.execute(select(snapshots.c.sha256).where(snapshots.c.sha256 == sha256)).first():
                    db.execute(insert(snapshots).values(sha256=sha256, size_bytes=len(content),
                                                        media_type=media_type, stored_at=_now()))
        except IntegrityError:
            # Another process stored the same content between the check and the insert.
            with self.engine.connect() as db:
                if not db.execute(select(snapshots.c.sha256).where(snapshots.c.sha256 == sha256)).first():
                    raise
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

    def recent_offers(self, source_id, stored_after, *, limit=50) -> list[Offer]:
        """Offers of one source stored at or after a time, for stored-evidence discovery (#75).

        Their evidence keeps its original observation times; a caller must still re-read
        a finalist before treating any of its price or stock as current."""
        with self.engine.connect() as db:
            rows = db.execute(select(offers.c.payload).where(
                offers.c.source_id == source_id, offers.c.stored_at >= stored_after)
                .order_by(offers.c.offer_id).limit(limit)).all()
        return [Offer.model_validate(row.payload) for row in rows]

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
        _check_usage(external_calls, planner_turns, spend)
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

    def note_usage(self, run_id, external_calls=0, planner_turns=0, spend: Decimal = Decimal('0')):
        """Add usage still in flight (reserved before a request, paid call or planner turn, and in
        no action yet) to a running run's totals, so recovery after a crash counts it."""
        _check_usage(external_calls, planner_turns, spend)
        with self.transaction() as db:
            run = db.execute(select(runs.c.status, runs.c.spend_amount)
                             .where(runs.c.run_id == run_id).with_for_update()).first()
            if run is None or run.status != 'running':
                raise ValueError('Usage can only be noted on a running run')
            db.execute(update(runs).where(runs.c.run_id == run_id).values(
                external_calls=runs.c.external_calls + external_calls,
                planner_turns=runs.c.planner_turns + planner_turns,
                spend_amount=run.spend_amount + spend))

    def list_actions(self, run_id):
        with self.engine.connect() as db:
            return [dict(r._mapping) for r in db.execute(
                select(actions.c.seq, actions.c.tool, actions.c.status, actions.c.result)
                .where(actions.c.run_id == run_id).order_by(actions.c.seq))]

    def finish_run(self, result: RunResult):
        """Close a running run with its result; its stored totals become the result's usage
        (while it ran they could be ahead of it, counting usage still in flight)."""
        usage = result.usage
        with self.transaction() as db:
            changed = db.execute(update(runs).where(
                (runs.c.run_id == result.run_id) & (runs.c.status == 'running')).values(
                status=result.status, stop_reason=result.stop_reason,
                finished_at=result.finished_at, result=result.model_dump(mode='json'),
                external_calls=usage.external_calls, planner_turns=usage.planner_turns,
                spend_amount=usage.spend.amount, spend_currency=usage.spend.currency)).rowcount
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

    def recover_interrupted_runs(self, now=None, *, live_for: timedelta = RUN_LEASE) -> list[str]:
        """Close runs left 'running' by a crash as partial; never resume stale searches.

        Only runs started more than live_for ago are closed: a younger run may still be live in
        another process, since the web app and command-line searches share the database. The web
        app calls this at startup and each command-line search before it starts. The usage is the
        run's stored totals, which count requests and paid calls in flight (written before each
        is sent); the elapsed time runs to the run's last recorded step, so downtime is never
        counted, but the step in flight at the crash is left out of it.
        """
        now = now or _now()
        recovered = []
        with self.transaction() as db:
            stale = db.execute(select(runs).where(runs.c.status == 'running')).all()
            for run in stale:
                if run.started_at >= now - live_for:
                    continue  # may still be live in another process
                finished = max(now, run.started_at)
                last = db.execute(select(func.max(actions.c.recorded_at))
                                  .where(actions.c.run_id == run.run_id)).scalar()
                active_until = min(max(last or run.started_at, run.started_at), finished)
                result = RunResult(
                    run_id=run.run_id, request_id=run.request_id, status='partial',
                    stop_reason=INTERRUPTED, started_at=run.started_at, finished_at=finished,
                    usage=BudgetUsage(
                        elapsed_ms=int((active_until - run.started_at).total_seconds() * 1000),
                        external_calls=run.external_calls, planner_turns=run.planner_turns,
                        spend=Money(amount=run.spend_amount, currency=run.spend_currency)))
                db.execute(update(runs).where(
                    (runs.c.run_id == run.run_id) & (runs.c.status == 'running')).values(
                    status='partial', stop_reason=result.stop_reason, finished_at=finished,
                    result=result.model_dump(mode='json')))
                recovered.append(run.run_id)
        return recovered


def load_sources(storage: Storage, sources_path=ROOT / 'config' / 'sources.json'):
    """Load the reviewed source policies into a corpus database."""
    registry = json.loads(Path(sources_path).read_text(encoding='utf-8'))
    for entry in registry['sources']:
        storage.save_source(SourcePolicy.model_validate(entry))
    return len(registry['sources'])


def load_fixtures(storage: Storage, sources_path=ROOT / 'config' / 'sources.json',
                  contracts_path=ROOT / 'tests' / 'fixtures' / 'contracts.json'):
    """Load the reviewed source registry and the synthetic contract example."""
    count = load_sources(storage, sources_path)
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
    return count, 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['load-sources']:
        print(f'Loaded {load_sources(Storage())} reviewed source policies')
        return 0
    if argv != ['load-fixtures']:
        print('usage: python -m app.storage load-sources | load-fixtures', file=sys.stderr)
        return 2
    count, offers_loaded = load_fixtures(Storage())
    print(f'Loaded {count} sources and {offers_loaded} synthetic offer')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
