"""Shared public evidence writes and retention for collection and buyer runs."""
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from typing import Literal

from pgvector import Vector
from pydantic import TypeAdapter
from sqlalchemy import delete, func, insert, literal, select, update

from app.collection_contracts import EvidencePassage
from app.contracts import Money, SourcePolicy, Text, UtcTime
from app.contracts_v2 import Evidence
from app.corpus_contracts import (
    DocumentCapture, Extraction, FieldObservation, ProcessingVersion, ResourceKey, binding,
)
from app.storage import (
    SnapshotStore, Storage, corpus_document_observations, corpus_document_passages,
    corpus_documents, corpus_embeddings, corpus_guard, corpus_observations, corpus_passages,
    corpus_receipts, corpus_resources, corpus_retirements, sources,
)

_TIME = TypeAdapter(UtcTime)
_TEXT = TypeAdapter(Text)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _fingerprint(value) -> str:
    return _digest(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                              separators=(',', ':')).encode('utf-8'))


def _resource_id(key: ResourceKey) -> str:
    return _fingerprint(key.model_dump(mode='json'))


def _head(db, resource_id):
    return db.execute(select(corpus_documents)
                      .where(corpus_documents.c.resource_id == resource_id)
                      .order_by(corpus_documents.c.document_id.desc()).limit(1)).first()


def _linked(db, document_id, records, links):
    return db.execute(select(records).join(links).where(links.c.document_id == document_id)
                      .order_by(*records.primary_key)).all()


def _retirement(db, resource_id, head):
    query = select(corpus_retirements.c.status).where(corpus_retirements.c.resource_id == resource_id)
    if head is not None:
        query = query.where(corpus_retirements.c.observed_at >= head.observed_at)
    return db.execute(query.order_by(corpus_retirements.c.observed_at.desc()).limit(1)).scalar_one_or_none()


@dataclass(frozen=True)
class ResourceState:
    document_id: int | None
    checked_at: datetime
    check_status: str
    validated_at: datetime | None
    content_changed_at: datetime | None
    raw_sha256: str | None
    relevant_sha256: str | None
    processing: ProcessingVersion | None
    conditional_url: str | None
    etag: str | None
    last_modified: str | None
    needs_processing: bool = False
    retirement_status: str | None = None


@dataclass(frozen=True)
class IngestionResult:
    document_id: int | None
    status: Literal['new', 'changed', 'unchanged', 'stale']
    changed_fields: tuple[str, ...] = ()
    changed_passages: int = 0


@dataclass(frozen=True)
class RetentionPolicy:
    raw_age: timedelta = timedelta(days=7)
    content_age: timedelta = timedelta(days=90)

    def __post_init__(self):
        if not timedelta(0) < self.raw_age <= self.content_age:
            raise ValueError('Retention needs positive raw and content ages with raw no longer than content')


class Corpus:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.snapshots = SnapshotStore(storage.snapshots.root / 'corpus')

    @contextmanager
    def _write(self):
        # The same transaction lock protects head selection and raw-file ownership across processes.
        with self.storage.transaction() as db:
            locked = db.execute(update(corpus_guard).where(corpus_guard.c.id == 1)
                                .values(id=corpus_guard.c.id))
            if locked.rowcount != 1:
                raise RuntimeError('Corpus storage requires migrations')
            yield db

    @staticmethod
    def _source(db, key, final_url=None):
        data = db.execute(select(sources.c.policy).where(sources.c.id == key.source_id)).scalar_one_or_none()
        if data is None:
            raise ValueError('Unknown corpus source')
        source = SourcePolicy.model_validate(data)
        origin = (source.origin.host, source.origin.port)
        urls = (key.url,) if final_url is None else (key.url, final_url)
        if source.status != 'active' or any((url.host, url.port) != origin for url in urls):
            raise ValueError('Corpus captures require an active source and its configured origin')

    @staticmethod
    def _save_check(db, resource_id, state, checked_at, status, **values):
        if state is None or checked_at >= state.checked_at:
            values.update(checked_at=checked_at, check_status=status)
        if values:
            db.execute(update(corpus_resources).where(corpus_resources.c.resource_id == resource_id)
                       .values(**values))

    @staticmethod
    def _state(db, resource_id):
        return db.execute(select(corpus_resources)
                          .where(corpus_resources.c.resource_id == resource_id)).first()

    def _resource(self, db, resource_id, key, checked_at, status):
        state = self._state(db, resource_id)
        if state is None:
            db.execute(insert(corpus_resources).values(
                resource_id=resource_id, source_id=key.source_id, resource_key=key.model_dump(mode='json'),
                checked_at=checked_at, check_status=status, needs_processing=False,
            ))
        return state

    def ingest(self, capture: DocumentCapture,
               extract: Callable[[DocumentCapture], Extraction], *,
               revalidate_fields: tuple[str, ...] = (), compare_extracted: bool = False) -> IngestionResult:
        """Persist a gated response using a deterministic local extractor."""
        if capture.visibility != 'public':
            raise ValueError('Private material cannot enter the shared corpus')
        resource_id = _resource_id(capture.key)
        raw = _digest(capture.body)
        relevant = _digest(capture.relevant_content)
        recipe = capture.processing.model_dump(mode='json')
        with self._write() as db:
            self._source(db, capture.key, capture.final_url)
            state = self._resource(db, resource_id, capture.key, capture.checked_at, 'retrieved')
            head = _head(db, resource_id)
            if state is not None and state.validated_at is not None and (
                capture.observed_at < state.validated_at or (
                    capture.observed_at == state.validated_at
                    and (head is None or raw != head.raw_sha256 or str(capture.final_url) != head.final_url)
                )
            ):
                return IngestionResult(head.document_id if head else None, 'stale')

            same_source = head is not None and head.final_url == str(capture.final_url)
            same_extractor = same_source and head.processing['extraction_version'] == recipe['extraction_version']
            if same_extractor and head.raw_sha256 == raw and head.relevant_sha256 != relevant:
                raise ValueError('The same response needs a stable relevant-content fingerprint')
            reuse = (not revalidate_fields and same_extractor and not state.needs_processing and head.relevant_sha256 == relevant
                     and head.processing['chunk_version'] == recipe['chunk_version'])
            self.snapshots.put(capture.body)
            changed_at = state.content_changed_at if state is not None else None
            changed = head is None or (head.raw_sha256 != raw and head.relevant_sha256 != relevant)
            if changed:
                changed_at = capture.observed_at
            self._save_check(
                db, resource_id, state, capture.checked_at, 'retrieved',
                validated_at=capture.observed_at, content_changed_at=changed_at,
                etag=capture.etag, last_modified=capture.last_modified, needs_processing=False,
            )
            if reuse and head.raw_sha256 == raw and head.processing == recipe:
                db.execute(update(corpus_documents).where(corpus_documents.c.document_id == head.document_id)
                           .values(snapshot_sha256=raw, snapshot_observed_at=capture.observed_at))
                return IngestionResult(head.document_id, 'unchanged')

            document_id = db.execute(insert(corpus_documents).values(
                resource_id=resource_id, final_url=str(capture.final_url), observed_at=capture.observed_at,
                raw_sha256=raw, snapshot_sha256=raw, snapshot_observed_at=capture.observed_at,
                relevant_sha256=relevant, origin=capture.origin, processing=recipe,
            )).inserted_primary_key[0]

            if reuse:
                self._copy_links(db, corpus_document_observations, 'observation_id', head.document_id, document_id)
                self._copy_links(db, corpus_document_passages, 'passage_id', head.document_id, document_id)
            else:
                extraction = extract(capture)
                if not isinstance(extraction, Extraction):
                    raise TypeError('Corpus extraction must return validated observations and passages')
                extraction = Extraction.model_validate(extraction.model_dump(mode='json'))
                self._validate_receipts(capture, extraction, raw)
                for item in extraction.observations:
                    receipt = item.evidence[0]
                    if (receipt.field_path == 'resource.availability' and item.fact.state == 'known'
                            and item.fact.value == 'discontinued' and receipt.scope == 'variant'
                            and capture.key.product_id and receipt.product_id == capture.key.product_id
                            and capture.key.variant_id and receipt.variant_id == capture.key.variant_id):
                        db.execute(insert(corpus_retirements).values(resource_id=resource_id,
                                   observed_at=receipt.observed_at, status='discontinued',
                                   evidence=receipt.model_dump(mode='json')))
                if not set(revalidate_fields) <= {item.evidence[0].field_path for item in extraction.observations}:
                    raise ValueError('Revalidation requires scoped extracted evidence for every named field')
                previous = head.document_id if same_extractor else None
                self._save_observations(db, document_id, previous, extraction, revalidate_fields)
                if previous is not None and head.processing['chunk_version'] != recipe['chunk_version']:
                    previous = None
                self._save_passages(db, document_id, previous, extraction)

            fields, passages = (), 0
            if compare_extracted:
                fields, passages = self._document_changes(db, head.document_id if head else None, document_id)
                if changed and same_extractor and not fields and not passages:
                    changed = False
                    db.execute(update(corpus_resources).where(corpus_resources.c.resource_id == resource_id)
                               .values(content_changed_at=state.content_changed_at))
            status = 'new' if head is None else 'changed' if changed else 'unchanged'
            return IngestionResult(document_id, status, fields, passages)

    @staticmethod
    def _copy_links(db, table, item_column, previous, document_id):
        copies = select(literal(document_id), table.c[item_column]).where(table.c.document_id == previous)
        db.execute(insert(table).from_select(('document_id', item_column), copies))

    @staticmethod
    def _validate_receipts(capture, extraction, raw):
        raw_text = capture.body.decode('utf-8')
        context = ('product_id', 'variant_id', 'branch_id', 'route_id')
        receipts = [item for field in extraction.observations for item in field.evidence]
        receipts.extend(passage.evidence for passage in extraction.passages)
        seen = {}
        for receipt in receipts:
            processing = capture.processing
            expected = (capture.key.source_id, capture.key.url, capture.final_url, capture.observed_at,
                        capture.origin, processing.extraction_version, processing.corpus_version, raw)
            actual = (receipt.source_id, receipt.origin_url, receipt.final_url, receipt.observed_at,
                      receipt.origin, receipt.adapter_version, receipt.corpus_version, receipt.response_sha256)
            if actual != expected or receipt.run is not None or (
                receipt.index_version is not None and receipt.index_version != processing.index_version
            ) or not receipt.excerpt or receipt.excerpt not in raw_text:
                raise ValueError('Corpus evidence must cite an original excerpt from its public capture')
            if any(getattr(capture.key, field) is not None and getattr(receipt, field) is not None
                   and getattr(receipt, field) != getattr(capture.key, field) for field in context):
                raise ValueError('Evidence is outside the captured resource context')
            if receipt.evidence_id in seen and seen[receipt.evidence_id] != receipt:
                raise ValueError('An evidence ID cannot denote different receipts in one capture')
            seen[receipt.evidence_id] = receipt

    @staticmethod
    def _save_receipt(db, document_id, receipt):
        key = corpus_receipts.c.evidence_id == receipt.evidence_id
        payload = receipt.model_dump(mode='json')
        existing = db.execute(select(corpus_receipts).where(key)).first()
        if existing is None:
            db.execute(insert(corpus_receipts).values(
                evidence_id=receipt.evidence_id, source_document_id=document_id, payload=payload,
            ))
        else:
            ownership = select(corpus_documents.c.resource_id).where(
                corpus_documents.c.document_id.in_((document_id, existing.source_document_id)))
            if existing.payload != payload or len(set(db.execute(ownership).scalars())) != 1:
                raise ValueError('A corpus evidence ID is immutable within its source resource')

    @staticmethod
    def _save_observations(db, document_id, previous, extraction, revalidate_fields=()):
        prior = {row.field_key: row for row in _linked(
            db, previous, corpus_observations, corpus_document_observations)}
        for item in extraction.observations:
            first = item.evidence[0]
            key = _fingerprint(binding(first))
            value = item.fact.model_dump(mode='json', exclude={'evidence_ids'})
            value['alternatives'] = sorted(value['alternatives'], key=_fingerprint)
            digest = _fingerprint(value)
            earlier = prior.get(key)
            if earlier is not None and earlier.field_sha256 == digest and not (
                first.field_path in revalidate_fields and first.observed_at > earlier.observed_at
            ):
                item_id = earlier.observation_id
            else:
                price = (Money.model_validate(item.fact.value)
                         if first.field_path == 'price' and item.fact.state == 'known' else None)
                for receipt in item.evidence:
                    Corpus._save_receipt(db, document_id, receipt)
                item_id = db.execute(insert(corpus_observations).values(
                    source_document_id=document_id, field_key=key, field_sha256=digest,
                    scope=first.scope, product_id=first.product_id, variant_id=first.variant_id,
                    branch_id=first.branch_id, route_id=first.route_id, field_path=first.field_path,
                    observed_at=first.observed_at, fact_state=item.fact.state, fact_value=item.fact.value,
                    price_amount=price.amount if price else None, price_currency=price.currency if price else None,
                    payload=item.model_dump(mode='json'),
                )).inserted_primary_key[0]
            db.execute(insert(corpus_document_observations).values(
                document_id=document_id, observation_id=item_id,
            ))

    @staticmethod
    def _save_passages(db, document_id, previous, extraction):
        prior = {row.passage_key: row for row in _linked(db, previous, corpus_passages, corpus_document_passages)}
        for item in extraction.passages:
            key = _fingerprint((binding(item.evidence), item.locator))
            digest = _fingerprint((item.text, item.evidence.excerpt))
            earlier = prior.get(key)
            if earlier is not None and earlier.passage_sha256 == digest:
                passage_id = earlier.passage_id
            else:
                Corpus._save_receipt(db, document_id, item.evidence)
                passage_id = db.execute(insert(corpus_passages).values(
                    source_document_id=document_id, passage_key=key, passage_sha256=digest,
                    text=item.text, payload=item.model_dump(mode='json'),
                )).inserted_primary_key[0]
            db.execute(insert(corpus_document_passages).values(document_id=document_id, passage_id=passage_id))

    def state(self, key: ResourceKey) -> ResourceState | None:
        resource_id = _resource_id(key)
        with self._write() as db:
            state = self._state(db, resource_id)
            head = _head(db, resource_id)
            retirement = _retirement(db, resource_id, head) if state else None
        if state is None:
            return None
        conditional = (not retirement and head is not None and head.snapshot_sha256 is not None and not state.needs_processing
                       and (state.etag is not None or state.last_modified is not None))
        return ResourceState(
            head.document_id if head else None, state.checked_at, state.check_status, state.validated_at,
            state.content_changed_at, head.raw_sha256 if head else None, head.relevant_sha256 if head else None,
            ProcessingVersion.model_validate(head.processing) if head else None,
            head.final_url if conditional else None, state.etag if conditional else None,
            state.last_modified if conditional else None, state.needs_processing, retirement,
        )

    @staticmethod
    def _document_changes(db, previous: int | None, current: int) -> tuple[tuple[str, ...], int]:
        """Compare the stored observations and passages actually linked to two versions."""
        def changed_values(links, table, identity, value):
            # Reused immutable IDs occur in both documents. Changed or newly unlinked IDs occur once.
            query = (select(value).select_from(links.join(table))
                     .where(links.c.document_id.in_((previous, current)))
                     .group_by(identity, value).having(func.count() == 1))
            return set(db.execute(query).scalars())

        fields = changed_values(corpus_document_observations, corpus_observations,
                                corpus_observations.c.observation_id, corpus_observations.c.field_path)
        passages = changed_values(corpus_document_passages, corpus_passages,
                                  corpus_passages.c.passage_id, corpus_passages.c.passage_key)
        return tuple(sorted(fields)), len(passages)

    def record_failed_check(self, key: ResourceKey, checked_at: datetime, *,
                            status: Literal['failed', 'denied', 'absent'] = 'failed') -> None:
        if status not in {'failed', 'denied', 'absent'}:
            raise ValueError('Unsupported failed-check status')
        checked_at = _TIME.validate_python(checked_at)
        resource_id = _resource_id(key)
        with self._write() as db:
            self._source(db, key)
            state = self._resource(db, resource_id, key, checked_at, status)
            self._save_check(db, resource_id, state, checked_at, status)

    def record_retirement(self, capture: DocumentCapture, receipt: Evidence, *,
                          status: Literal['removed', 'discontinued']) -> None:
        """Record a supported removal, not an inference from a timeout/404.

        The caller's source adapter must identify affirmative removal text. Its
        captured excerpt and exact resource scope are retained for audit.
        """
        from app.corpus_contracts import PassageDraft
        if status not in {'removed', 'discontinued'} or capture.visibility != 'public':
            raise ValueError('Retirement requires a public supported removal')
        if receipt.field_path != 'resource.availability' or any(
            getattr(receipt, field) != getattr(capture.key, field)
            for field in ('product_id', 'variant_id', 'branch_id', 'route_id')
        ):
            raise ValueError('Removal evidence must bind the exact resource')
        self._validate_receipts(capture, Extraction(passages=(
            PassageDraft(locator='removal', text=receipt.excerpt or '', evidence=receipt),
        )), _digest(capture.body))
        with self._write() as db:
            self._source(db, capture.key, capture.final_url)
            resource_id = _resource_id(capture.key)
            state = self._resource(db, resource_id, capture.key, capture.checked_at, status)
            db.execute(insert(corpus_retirements).values(
                resource_id=resource_id, observed_at=receipt.observed_at, status=status,
                evidence=receipt.model_dump(mode='json')))
            self._save_check(db, resource_id, state, capture.checked_at, status)

    def assessed_observations(self, key: ResourceKey, context, *, policy=None):
        """Read the current resource's observations with mandatory reuse decisions."""
        from app.freshness import FreshnessPolicy, assess_observation
        with self.storage.engine.connect() as db:
            resource_id = _resource_id(key)
            state = self._state(db, resource_id)
            head = _head(db, resource_id)
            if state is None or head is None:
                return ()
            status = _retirement(db, resource_id, head) or state.check_status
            rows = _linked(db, head.document_id, corpus_observations, corpus_document_observations)
            source = db.execute(select(sources.c.status).where(sources.c.id == key.source_id)).scalar_one()
        return tuple((item, assess_observation(item, context,
            policy=policy or FreshnessPolicy(), check_status=status,
            superseded=source != 'active'))
            for item in (FieldObservation.model_validate(row.payload) for row in rows))

    def record_not_modified(self, key: ResourceKey, previous: ResourceState, checked_at: datetime) -> bool:
        """Apply a 304 against the exact conditional URL and validators sent for the resource."""
        checked_at = _TIME.validate_python(checked_at)
        resource_id = _resource_id(key)
        with self._write() as db:
            self._source(db, key)
            state = self._state(db, resource_id)
            head = _head(db, resource_id)
            if (state is None or head is None or head.document_id != previous.document_id
                    or state.needs_processing or checked_at <= state.validated_at
                    or not head.snapshot_sha256 or not (state.etag or state.last_modified)
                    or _retirement(db, resource_id, head)
                    or (head.final_url, state.etag, state.last_modified)
                    != (previous.conditional_url, previous.etag, previous.last_modified)):
                return False
            try:
                self.snapshots.get(head.snapshot_sha256)
            except (FileNotFoundError, ValueError):
                return False
            self._save_check(db, resource_id, state, checked_at, 'not_modified', validated_at=checked_at)
            return True

    def load_raw(self, document_id: int) -> bytes:
        with self._write() as db:
            digest = db.execute(select(corpus_documents.c.snapshot_sha256)
                                .where(corpus_documents.c.document_id == document_id)).scalar_one_or_none()
            if digest is None:
                raise KeyError('No retained raw response for this document version')
            return self.snapshots.get(digest)

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        with self.storage.engine.connect() as db:
            data = db.execute(select(corpus_receipts.c.payload)
                              .where(corpus_receipts.c.evidence_id == evidence_id)).scalar_one_or_none()
        return Evidence.model_validate(data) if data is not None else None

    def observations(self, document_id: int) -> tuple[FieldObservation, ...]:
        with self.storage.engine.connect() as db:
            rows = _linked(db, document_id, corpus_observations, corpus_document_observations)
        return tuple(FieldObservation.model_validate(row.payload) for row in rows)

    def passages(self, document_id: int) -> tuple[EvidencePassage, ...]:
        with self._write() as db:
            rows = _linked(db, document_id, corpus_passages, corpus_document_passages)
            processing = db.execute(select(corpus_documents.c.processing)
                                    .where(corpus_documents.c.document_id == document_id)).scalar_one_or_none()
        return tuple(EvidencePassage(
            passage_id=str(row.passage_id), text=row.text, evidence=row.payload['evidence'],
            chunk_version=processing['chunk_version'],
        ) for row in rows)

    def pending_embeddings(self, document_id: int, model: str, dimensions: int,
                           *, index_version: str | None = None) -> tuple[int, ...]:
        """Return the passages that still need an embedding in this document's index version."""
        links = corpus_document_passages
        if index_version is None:
            index_version = corpus_documents.c.processing['index_version'].as_string()
        indexed = select(1).where(
            corpus_embeddings.c.passage_id == links.c.passage_id,
            corpus_embeddings.c.model == model,
            corpus_embeddings.c.dimensions == dimensions,
            corpus_embeddings.c.index_version == index_version,
        ).exists()
        query = (select(links.c.passage_id).join(corpus_documents)
                 .where(links.c.document_id == document_id, ~indexed).order_by(links.c.passage_id))
        with self.storage.engine.connect() as db:
            return tuple(db.execute(query).scalars())

    def save_embedding(self, passage_id: int, index_version: str, model: str,
                       values: Sequence[float]) -> None:
        index_version = _TEXT.validate_python(index_version)
        model = _TEXT.validate_python(model)
        if not 1 <= len(values) <= 2000 or any(type(v) not in (int, float) for v in values):
            raise ValueError('Invalid embedding dimensions or values')
        try:
            embedding = Vector(values).to_list()
        except (OverflowError, ValueError) as exc:
            raise ValueError('Invalid embedding values') from exc
        if any(not math.isfinite(v) for v in embedding) or not any(embedding):
            raise ValueError('Embeddings must be finite and nonzero')
        key = dict(passage_id=passage_id, index_version=index_version, model=model, dimensions=len(embedding))
        with self._write() as db:
            if db.execute(select(corpus_passages.c.passage_id)
                          .where(corpus_passages.c.passage_id == passage_id)).scalar_one_or_none() is None:
                raise KeyError('Unknown or expired passage')
            existing = db.execute(select(corpus_embeddings.c.embedding).filter_by(**key)).scalar_one_or_none()
            if existing is None:
                db.execute(insert(corpus_embeddings).values(**key, embedding=embedding))
            elif existing != embedding:
                raise ValueError('Change the index version before replacing an embedding')

    def delete_resource(self, key: ResourceKey) -> None:
        with self._write() as db:
            db.execute(delete(corpus_resources).where(corpus_resources.c.resource_id == _resource_id(key)))
        self._sweep_snapshots()

    def prune(self, *, now: datetime, policy: RetentionPolicy = RetentionPolicy()) -> None:
        now = _TIME.validate_python(now)
        with self._write() as db:
            expired = corpus_documents.c.observed_at < now - policy.content_age
            affected = select(corpus_documents.c.resource_id).where(expired)
            db.execute(update(corpus_resources).where(corpus_resources.c.resource_id.in_(affected))
                       .values(needs_processing=True))
            db.execute(delete(corpus_documents).where(expired))
            db.execute(update(corpus_documents)
                       .where(corpus_documents.c.snapshot_observed_at < now - policy.raw_age)
                       .values(snapshot_sha256=None, snapshot_observed_at=None))
        self._sweep_snapshots()

    def _sweep_snapshots(self):
        # Drop references first, then remove files under the same lock used by capture writers.
        # This also reclaims files left by an interrupted or rolled-back ingestion.
        with self._write() as db:
            retained = set(db.execute(select(corpus_documents.c.snapshot_sha256).distinct()).scalars())
            for digest in self.snapshots.digests():
                if digest not in retained:
                    self.snapshots.remove(digest)
            self.snapshots.remove_abandoned_writes()
