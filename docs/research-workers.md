# Bounded research workers

`app.research_workers.ResearchWorkers.execute(job, work)` implements #67 on top of
the existing fan-out and shared accounting ledger. The controller supplies one to
three distinct `Work` objects for the same request/run revision. Each contains a
ResearchContext, up to 20 exact resource bindings, processing versions and one to
six investigation turns. This module starts neither a background crawl nor a
recommendation.

Resources name the directory retailer, source, exact product/variant/route,
content purpose, fields and optional source capability. The persistent directory
prioritizes configured sources and exposes unconfigured leads. Controller-scoped
URLs can be absent from the seed corpus, but every fetch still passes the current
source gate. Profiles must support the exact source/configuration; missing
profiles or unsupported extraction remain gaps. A resource whose source has no
profile is never offered to the model as a fetch choice, so no paid call is spent on
it (#75); its missing fields stay explicit gaps.

Workers read structured observations and bounded lexical evidence retrieval
before external work. Processing versions and field freshness govern reuse.
Foreign specifications/reviews can support a domestic request; only
specification/policy fields may use that exception. Research excludes foreign purchase listings, including cached observations from earlier searches.

Only corpus, fetch, discover and model are permitted tool names. Corpus access is
required before acquisition. An optional ModelProvider chooses an index in the
supplied missing-resource list; it cannot provide URLs, execute tools, manufacture
facts or edit the request. Failures are traced and unresolved work remains a gap.
No provider is needed for deterministic resource order. Tavily-compatible
discovery returns leads for technical configuration, never activated retailers.

The default acquisition path reuses `page_extraction.read_page` and `Corpus.ingest`.
Source workloads, retries and paid fallback remain metered by the durable shared
job. Per-task calls, spend and deadline restrict every reservation, conservatively
retaining task allowances after an attempt. Identical run/resource/processing
acquisitions coalesce within a dispatch batch, including failures. Across
processes, durable source leases enforce configured concurrency; there is no
cross-process response cache. Cancellation interrupts waiters and prevents new
calls or late ingestion.

Report contains a v2 AgentFinding, typed field observations, complete evidence
receipts, leads and bounded action traces. Fresh structured facts become claims;
historical, unknown and conflicting observations retain their states. Receipts
retain source, extraction/corpus/index versions, exact field binding and original
timestamps. Research does not attach current-run verification receipts to price
or stock, even after fetching. Finalist verification remains authoritative.

Offline tests cover stored-first reuse, foreign supporting evidence versus
purchase offers, legacy opt-in exclusion, unseeded acquisition, simultaneous fetch sharing,
cancellation, revision mismatch, source refusal, task exhaustion, model scope
rejection, discovery leads and the shared page/extraction path. No live retailer
or paid provider is used.

## Evidence critique

After research, the orchestrator matches known claims to current typed
observations and scoped receipts. It reports unsupported claims, absent
required specification fields, and contradictory specification values for the
same configuration. Missing facts without a matching resource remain visible as
issues. Model-fit selection receives only supported, undisputed specifications.

The evaluator sees the structured issues and can select one existing
resource/field pair. The controller runs a single stored-first research task for
that pair with the same job and deadline, then reevaluates the evidence once.
An unavailable evaluator or invalid selection marks the critique degraded while
retaining deterministic findings. Eligibility, totals and live finalist
verification remain downstream. Browser-to-live integration is tracked in #75.
