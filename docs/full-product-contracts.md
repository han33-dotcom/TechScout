# TechScout full-product contracts v2

The v2 contracts define the wire format shared by the buyer API, agent roles, evidence
repository and ingestion worker. See the [delivery plan](full-product-plan.md) and
[task #41](https://github.com/han33-dotcom/TechScout/issues/41).

## Using the contracts

- [Shopping contracts](../app/contracts_v2.py) define the five category profiles, requests, variants,
  seller offers, routes, evidence, findings and run snapshots.
- [Collection contracts](../app/collection_contracts.py) define the retailer directory, capability
  references, manifest, job, checkpoint and evidence passage.
- [Module interfaces](../app/contract_interfaces.py) define the in-process buyer and role calls
  and the shared worker/storage boundaries. The FastAPI process and worker coordinate through
  durable records. Each agent role can be invoked with offline inputs.

Run the following command from the repository root to export the JSON Schema bundle for
consumer tooling. The bundle includes the union discriminators and definitions. Python
validation also checks cross-field and cross-record constraints.

~~~sh
python -m app.contract_schema > techscout-v2.schema.json
~~~

### Buyer request and result rules

- Request intents are exact model, product link, or needs. The category discriminator selects
  the supported hard specification fields. Needs input carries the use case and, where
  applicable, required software. Retailer links remain discovery leads until source validation.
- Lebanon and new condition are defaults. Explicit market and condition selections are carried
  in the request. The international switch filters purchase offers across all results. Technical
  evidence can be sourced independently of the purchase market.
- Enter approximate budgets as budget.target and hard ceilings as budget.maximum. A request can
  carry either or both. ShoppingRequest.refine keeps the request ID and advances the revision.
  Budget edits can set either field independently or use null to remove it. Edits to other
  top-level fields replace that field as a whole.
- Coverage reports discovered, attempted, retrieved and qualifying retailer counts in decreasing
  order. Provisional cards carry their original observation time. A terminal result can contain
  up to three verified recommendations, grouped by product for needs-based requests and listing
  additional verified sellers for the same configuration. Uncertain alternatives carry reasons
  and an optional cost assessment. A workspace can show an older revision beside the current one.
- Verified results require a positive price, buyable stock, evidenced condition and resolved
  mandatory charges. Finalist price and stock evidence must name the active run and revision
  and fall inside that run's timestamps. Pickup binds stock to the actual branch and variant.
  Shipping and import charges bind to the selected route. Foreign currency and promotion terms
  are resolved by the deterministic cost engine. Money is serialized as decimal strings.
- An import can use direct delivery or two legs joined at a forwarding hub. Route eligibility,
  package assumptions, quote expiry and charge evidence are recorded separately. Estimated
  ranges and incomplete subtotals have their own discriminators.

## Shared fixtures

All fixture names, product claims and URLs below are synthetic.

| Fixture | Demonstrates |
|---|---|
| [requests.json](../tests/fixtures/v2/requests.json) | Exact, link and needs entry, five category profiles, approximate versus hard budget and refinement |
| [offers.json](../tests/fixtures/v2/offers.json) | Five categories, pickup, domestic and international direct delivery, forwarding, evidence identities and seller terms |
| [matches.json](../tests/fixtures/v2/matches.json) | Confirmed equivalents with different condition, activation and warranty, different configurations, unresolved SIM |
| [workspace.json](../tests/fixtures/v2/workspace.json) | An older provisional result, current-run recommendation and an incomplete import with coverage and revision metadata |
| [finding.json](../tests/fixtures/v2/finding.json) | A typed agent observation referencing evidence and an unresolved question |
| [retailers.json](../tests/fixtures/v2/retailers.json) | A retailer with a configured capability reference and an unconfigured lead |
| [collection.json](../tests/fixtures/v2/collection.json) | One bootstrapping manifest, aggregate and nested import allocations, lease and a resource checkpoint |

## PostgreSQL relationship contract

External IDs in v2 are opaque strings. Use PostgreSQL text keys for those IDs and native
foreign keys in durable storage. Nested response objects are typed projections of the
referenced rows. Keep source listing IDs separate from canonical IDs. In particular, a
single-SKU listing can use a stable source-local variant ID.

| Row or link | Key and relationships |
|---|---|
| products | product_id primary key, with category, manufacturer and model |
| variants | variant_id primary key, product_id foreign key to products |
| retailers and sellers | retailer_id and seller_id primary keys. A retailer_sellers link uses both as foreign keys and supports third-party sellers on a marketplace |
| offers | offer_id primary key. retailer_id and seller_id reference retailer_sellers. source_id references the configured source. variant_id and route_id reference variants and routes. Keep source_product_id and source_variant_id beside source_id for native lookup and evidence checks |
| routes and route_legs | route_id primary key. Store at most one row per route_id and leg role with a route_id foreign key and explicit origin/destination. Store item eligibility and charge evidence independently per leg |
| evidence and offer_evidence | evidence_id primary key. Evidence references its source and retained response hash. offer_evidence links offer_id and evidence_id with the field path. Preserve source-local listing/variant IDs and optional branch and route IDs for scope validation. The same evidence record keeps its original observation timestamp |
| requests, runs and run views | request_id and request_revision form a compound key. Runs foreign-key the request revision. Persist a separate per-run revision when replacing the run view. Link result offers and verification evidence to that view |
| collection manifests, jobs and checkpoints | Jobs foreign-key manifest_id. Checkpoints foreign-key job_id and target source, keyed by job, source and resource URL. Preserve corpus and index versions and immutable observation IDs through checkpoint retries |

Directory discovery status describes research progress. Technical authorization comes from the
versioned capability reference resolved at dispatch through the source gate. Collection and
live jobs share v2 evidence fields, while bootstrap and maintenance evidence carry their
separate origin and corpus/index versions. Preserve the original observation time on reuse.

## Shared public evidence storage

`app.corpus.Corpus` is the common persistence entry point for bootstrap, maintenance,
and public runtime findings. Pass `DocumentCapture` with the original gated response
bytes, the deterministic relevant-content representation, the source observation and check
times, the processing versions, and any resource-scoped HTTP validators. The resource key
includes the requested URL, representation, and known product, variant, branch, or route
context. Document versions record the actual final URL. Save technical source policy before
ingesting its captures.

Provide a local extractor that returns typed `FieldObservation` and `PassageDraft`
records. Structured facts use the v2 field validators, and supported policy fields use
text. Observations and passages cite matching v2 evidence with excerpts present in the
original UTF-8 response. Private captures and buyer-run receipts are refused. Pass only
public source material to the corpus. Buyer requests and run history stay in their existing
storage. The extractor must do local parsing only because writes hold a database lock.

Repeated identical responses reuse the document. Changes to raw bytes that leave relevant
content and the extraction recipe unchanged keep the existing observations and passages.
Changed fields create observations while preserving their history. A price-only change can
reuse a specification passage, and `pending_embeddings` checks whether that passage already
has an embedding for the document's model, dimensions, and index version. PostgreSQL uses
a simple-config full-text GIN index and a cosine HNSW index for 1,536-dimensional embeddings.
Other dimensions retain their embedding metadata for retrieval.

Use `state` for the current document and its conditional URL and validators. A caller passes
the exact state used for its conditional request to `record_not_modified`. A successful 304
updates validation time without changing fact-observation time. `record_failed_check` only
advances check metadata. A validated late result cannot replace a newer document. Client-side
conditional requests, job scheduling, freshness rules, and retrieval queries are downstream
work.

`Corpus.prune(now=..., policy=RetentionPolicy(...))` is an explicit maintenance operation.
Its defaults keep raw responses for seven days and retained excerpts, observations, and
passages for 90 days. Pass different durations to configure retention, or use
`delete_resource` to remove a specific resource. Losing a supporting document removes its
observations and passages from newer versions too, including linked embeddings. The next
full capture can rebuild those links. Raw files are removed once no retained corpus document
references them. This corpus uses a separate hash-addressed namespace in the existing evidence
volume. The same volume-level backup covers both namespaces. No scheduled pruning is enabled.

## Compatibility and rollout

The [v1 model](mvp-spec.md), CLI, API and storage loaders keep reading existing records.
Use read_request, read_offer and read_run in app.contracts_v2 to dispatch shared readers by
schema_version. A record without a version is treated as v1. Unsupported versions fail.
Fresh v2 exports carry schema_version 2. Reuse Money, Fact, scope binding and timestamp
rules from v1 without rewriting historical records.

This change supplies schemas and fixtures. The evidence and retailer persistence tasks add the
physical PostgreSQL relationships and versioned readers in storage. A later data migration can
write new v2 records beside historical v1 records and preserve source receipts, timestamps and
native IDs. Keep legacy results readable as historical v1 results. Any unresolved field or
unconfirmed route requires current verification before promotion into a v2 result.


## Phone and laptop identity module

Implemented for [#42](https://github.com/han33-dotcom/TechScout/issues/42) in
`app.identity`. The legacy v1 `normalize_variant`, `same_device` and
`deduplicate` APIs remain available to the existing shopping flow.
The v2 functions consume validated, source-backed structured offers:

~~~python
from app.identity import compare_offers, group_variants, seller_offer_key

comparison = compare_offers(left_offer, right_offer)
# comparison.hardware is the existing v2 MatchAssessment
# inspect differing/unresolved identity fields AND the separate terms outcome
groups = group_variants(candidate_offers)
~~~

`compare_offers` distinguishes confirmed hardware equivalence, known configuration
differences and unresolved identity. Phone identity requires manufacturer, model,
color, storage, SIM and region. Laptop identity requires manufacturer, model, color,
CPU, GPU, RAM, storage, OS, screen size, resolution, refresh rate, panel and touch.
A known difference remains visible beside missing facts. Missing/unknown/conflicting
fields are never wildcards; even two identical unknowns cannot confirm equivalence.
Manufacturer and model require product/variant-scoped evidence at
`variant.product.manufacturer` and `variant.product.model`; store-wide text does
not supply either. Other fields retain the ordinary v2 field-bound receipts.

Text matching is exact after case/whitespace and typographic-dash normalization,
with narrow explicit manufacturer, model, color and country aliases. It preserves
model/component suffixes, punctuation and numeric boundaries. Arbitrary structured
phone/laptop model identifiers can be compared without extending a model catalog.
The legacy free-text vocabulary now also recognizes selected Galaxy S24/S25 and
Pixel 9 models, with longest-match and suffix guards. This is not general free-text
specification extraction, multilingual entity resolution, or a complete model list.
Extractors must mark incomplete configurations unknown rather than call a generic
CPU family, product line or a guessed default an exact specification.

`terms_outcome` separately reports matching/different/unresolved condition,
activation (required for phones), payment and warranty provider/months/territory.
Warranty changes do not create a different device. Activation and condition stay
on each offer. Matching terms cover only these fields, **not equivalent purchases**:
price, delivery, taxes, stock, route eligibility and deadlines still need the cost
and eligibility modules. Return both hardware and term findings to downstream
callers rather than treating the hardware outcome as a purchase recommendation.

`group_variants` gives fully evidenced equal hardware one `hardware-v1:` key
while retaining each complete seller offer and its evidence/timestamps. Key
generation is independent of the ambient Decimal precision. Unknown hardware
stays in separate groups. This groups variants, not offers: merchant, fulfiller,
native listing/variant IDs and route remain distinct in `seller_offer_key`.
A shared SKU is never cross-store identity. Identical repeated offer payloads
are idempotent; conflicting payloads with one global offer ID raise instead of
silently losing an observation. A canonical variant ID reused for conflicting
hardware produces an unresolved assessment with the differing fields exposed;
the grouping keys still keep those hardware configurations apart.

### v2 schema revision: 2026-11-16 display identity

Five optional fields were added to `LaptopSpecs`:
`display_width_px`, `display_height_px`, `display_refresh_hz`,
`display_panel` and `display_touchscreen`. They use the existing
known/unknown/conflict Fact format with selected-variant evidence.
Old v2 records still load and leave these fields absent; absence cannot establish
an equivalent display. Width/height and refresh must be positive integers,
touchscreen is a strict boolean, and size retains exact decimal serialization.

The wire major remains 2 because existing records retain their meaning; this is
an additive schema revision, not a v1 migration. Regenerate the schema bundle
when upgrading API/worker consumers. Old strict decoders that reject extra fields
must be upgraded before they receive the new fields. The Python schema export
and corpus FieldObservation validators derive these fields from LaptopSpecs;
no relational migration is needed for the JSON projections. The UI/API consumers
under later tasks should use this revision when they expose laptop comparisons.

[Identity recipes](../tests/fixtures/identity/full-product.json) and
`tests.identity_v2.offer` build synthetic, evidence-bound v2 offers. They cover
31 positive/adversarial cases, including SIM/region/activation/condition/warranty
traps and laptop CPU/GPU/memory/storage/display differences. Additional tests
exercise missing fields, receipt scope, seller/SKU/route separation, exact Decimal
keys, canonical-ID collisions and schema/corpus compatibility. All run offline.

### Module interface revision: 2026-11-17 writing context

`WritingContext` (`app/contract_interfaces.py`) gained three optional fields:
`request` (the `ShoppingRequest` revision the run answered), `coverage`
(`CoverageCounts`) and `uncertain` (`UncertainAlternative` records). The writer
needs them to explain budget limits, open priorities, search coverage and
unverified alternatives. They default to absent or empty, so earlier
three-field contexts still validate.

`WritingContext` is an in-process port, not part of the published schema bundle,
so the wire major and the JSON Schemas are unchanged. Its only consumer is
`app/writer.py` (see [Grounded writer](writer.md)). That consumer also rejects a
context whose recommendations were verified in another run, whose request
revision differs from the run's, or which holds more than three recommendations.
