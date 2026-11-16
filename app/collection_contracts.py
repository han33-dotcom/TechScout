"""Shared contracts for retailer discovery, collection jobs and evidence indexing."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.contracts import Amount, Count, HttpsUrl, Model, PositiveInt, Text, UtcTime
from app.contracts_v2 import Category, Contract, Country, Evidence

ContentKind = Literal['listing', 'specification', 'review', 'policy']
JobKind = Literal['bootstrap', 'refresh', 'discovery']
Digest = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9a-f]{64}$')]


class CapabilityRef(Model):
    capability_id: Text
    version: Text


class RetailerDirectoryEntry(Contract):
    retailer_id: Text
    name: Text
    origin: HttpsUrl
    aliases: tuple[Text, ...] = ()
    related_origins: tuple[HttpsUrl, ...] = ()
    markets: Annotated[tuple[Country, ...], Field(min_length=1)]
    categories: tuple[Category, ...] = ()
    brands: tuple[Text, ...] = ()
    discovery_status: Literal['discovered', 'audited', 'retired']
    retrieval_method: Literal['feed', 'catalog', 'direct', 'managed_fallback'] | None = None
    capability: CapabilityRef | None = None
    policy_evidence_ids: tuple[Text, ...] = ()
    last_checked_at: UtcTime | None = None
    coverage_gaps: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def documented_coverage(self):
        origins = (self.origin, *self.related_origins)
        if any(url.path not in (None, '/') or url.query or url.fragment for url in origins):
            raise ValueError('Directory origins require a host and optional port only')
        if self.discovery_status == 'audited' and self.last_checked_at is None:
            raise ValueError('An audited retailer requires a dated check')
        return self


class CollectionLimits(Model):
    max_fetch_attempts: PositiveInt
    max_elapsed_ms: PositiveInt
    max_records: PositiveInt
    max_response_bytes: PositiveInt
    max_paid_calls: Count
    max_spend_usd: Amount
    max_concurrent_sources: Annotated[PositiveInt, Field(le=4)]
    max_requests_per_source_minute: Annotated[PositiveInt, Field(le=6)]
    max_concurrent_per_source: Annotated[PositiveInt, Field(le=1)]
    max_retries_per_resource: Annotated[Count, Field(le=2)]

    @model_validator(mode='after')
    def compatible_bounds(self):
        if self.max_concurrent_per_source > self.max_concurrent_sources:
            raise ValueError('Per-source concurrency must fit the job-wide bound')
        return self


class CollectionTarget(Model):
    source_id: Text
    retailer_id: Text | None = None
    capability: CapabilityRef
    market: Country
    categories: Annotated[tuple[Category, ...], Field(min_length=1)]
    content: Annotated[tuple[ContentKind, ...], Field(min_length=1)]
    seed_urls: Annotated[tuple[HttpsUrl, ...], Field(min_length=1)]


class CorpusVersion(Model):
    corpus_version: Text
    index_version: Text


class IngestionManifest(Contract):
    manifest_id: Text
    kind: JobKind
    targets: Annotated[tuple[CollectionTarget, ...], Field(min_length=1)]
    versions: CorpusVersion
    limits: CollectionLimits
    international_limits: CollectionLimits | None = None
    created_at: UtcTime

    @model_validator(mode='after')
    def nested_market_limits(self):
        if any(item.market != 'LB' for item in self.targets) and self.international_limits is None:
            raise ValueError('International collection requires a nested allocation')
        if self.international_limits and any(
            value > getattr(self.limits, field)
            for field, value in self.international_limits.model_dump().items()
        ):
            raise ValueError('The international allocation must fit the aggregate allocation')
        return self


class CollectionUsage(Model):
    fetch_attempts: Count = 0
    elapsed_ms: Count = 0
    records: Count = 0
    response_bytes: Count = 0
    paid_calls: Count = 0
    spend_usd: Amount = Decimal(0)


class IngestionJob(Contract):
    job_id: Text
    manifest_id: Text
    kind: JobKind
    status: Literal['queued', 'running', 'partial', 'complete', 'failed', 'cancelled']
    versions: CorpusVersion
    usage: CollectionUsage = Field(default_factory=CollectionUsage)
    international_usage: CollectionUsage = Field(default_factory=CollectionUsage)
    lease_owner: Text | None = None
    lease_expires_at: UtcTime | None = None
    started_at: UtcTime | None = None
    finished_at: UtcTime | None = None

    @model_validator(mode='after')
    def coherent_job_state(self):
        running = self.status == 'running'
        terminal = self.status not in {'queued', 'running'}
        if ((self.lease_owner is not None) != running
                or (self.lease_expires_at is not None) != running
                or (self.finished_at is not None) != terminal
                or (self.status in {'running', 'partial', 'complete'} and self.started_at is None)
                or (self.status == 'queued' and self.started_at is not None)):
            raise ValueError('Job timestamps and lease must agree with job status')
        if self.started_at and (
            (self.finished_at and self.finished_at < self.started_at)
            or (self.lease_expires_at and self.lease_expires_at <= self.started_at)
        ):
            raise ValueError('The job must start before its finish and lease expiry')
        if any(value > getattr(self.usage, field)
               for field, value in self.international_usage.model_dump().items()):
            raise ValueError('International usage must be included in the overall totals')
        return self


class IngestionCheckpoint(Contract):
    job_id: Text
    manifest_id: Text
    source_id: Text
    resource_url: HttpsUrl
    status: Literal['pending', 'unchanged', 'changed', 'new', 'removed', 'failed', 'skipped']
    versions: CorpusVersion
    attempts: Count = 0
    checked_at: UtcTime | None = None
    last_retrieved_at: UtcTime | None = None
    response_sha256: Digest | None = None
    evidence_ids: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def checked_resource(self):
        if self.attempts and self.checked_at is None:
            raise ValueError('Attempts require a last check timestamp')
        if self.last_retrieved_at and (self.checked_at is None or self.last_retrieved_at > self.checked_at):
            raise ValueError('Retrieval cannot follow the last check')
        if self.status in {'new', 'changed', 'unchanged'} and (
            not self.attempts or self.last_retrieved_at is None or self.response_sha256 is None
        ):
            raise ValueError('Successful checkpoints require a retrieval and response hash')
        if self.status == 'removed' and (not self.attempts or not self.evidence_ids):
            raise ValueError('Removal requires a checked source and supporting evidence')
        return self


class CollectionSnapshot(Contract):
    manifest: IngestionManifest
    job: IngestionJob
    checkpoints: tuple[IngestionCheckpoint, ...] = ()

    @model_validator(mode='after')
    def consistent_references(self):
        manifest, job = self.manifest, self.job
        if (job.manifest_id, job.kind, job.versions) != (
            manifest.manifest_id, manifest.kind, manifest.versions
        ):
            raise ValueError('The job must reference its immutable manifest and corpus versions')
        sources = {item.source_id for item in manifest.targets}
        seen = set()
        for checkpoint in self.checkpoints:
            if ((checkpoint.job_id, checkpoint.manifest_id, checkpoint.versions)
                    != (job.job_id, manifest.manifest_id, manifest.versions)
                    or checkpoint.source_id not in sources):
                raise ValueError('Checkpoints must belong to this job and a targeted source')
            key = (checkpoint.source_id, checkpoint.resource_url)
            if key in seen:
                raise ValueError('A resource has one checkpoint per source and job')
            seen.add(key)
        return self


class EvidencePassage(Contract):
    passage_id: Text
    evidence: Evidence
    chunk_version: Text
    text: Text
    embedding_model: Text | None = None
    embedding_dimensions: PositiveInt | None = None

    @model_validator(mode='after')
    def embedding_identity(self):
        if self.embedding_model is not None:
            if self.embedding_dimensions is None or self.evidence.index_version is None:
                raise ValueError('Embeddings require dimensions and an index version')
        elif self.embedding_dimensions is not None:
            raise ValueError('Dimensions require an embedding model')
        return self
