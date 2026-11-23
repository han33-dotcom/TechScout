# Evidence freshness and retrieval services

## Freshness policy (#47)

`app.freshness` assesses original, immutable evidence against an explicit UTC clock
and requested product, variant, branch, route and buyer-run revision. Defaults are
30 days for specifications and 7 days for general source policies, with exclusive
expiry boundaries and earlier explicit expiry. Unknown field types fail closed.
Item-specific quotes need their evidence ID, route, unchanged package fingerprint
and stated expiry; they cannot exempt price/stock from current-run checks.

`Corpus.assessed_observations` returns observations alongside fresh, historical,
unknown, conflict or retired decisions. Consumers must honor these decisions;
raw `observations` remains a historical inspection API. Reusable does not mean
purchase-eligible, reserved, or a guaranteed arrival. The v2 recommendation
contract still enforces current-run evidence for finalists.

HTTP 304, unchanged fingerprints, extraction/index changes and successful resource
checks never automatically retime observations. `ingest(revalidate_fields=...)`
requires a new public capture and scoped extracted receipts for each named field;
only those fields get new observations. Original versions remain available.
Adapters must request this only when their capture supports that field and scope.
No inference from page-level success to branch stock or item-specific quotes is allowed.

Failed retrieval, denied access and ambiguous absence retain historical observations.
`record_retirement` requires an affirmative source excerpt bound to the exact public
resource. Adapters must distinguish that evidence from a 404, timeout or generic
error page. Migration 0004 retains these receipts even when a later fetch fails.
A subsequent newer document can restore the resource; conditional validation alone
does not erase a supported removal. No live endpoints are needed for these tests.

## Retrieval and embeddings (#48)

`EvidenceRetrieval.retrieve(EvidenceQuery, vector=...)` applies exact source,
product/variant/branch/route/field filters and current corpus/index/chunk versions
before ranking. Only the current document's linked public passages are eligible;
disabled sources, processing gaps and supported retirements are excluded. Private
documents cannot enter this corpus. Returned hits contain passage and evidence IDs,
original timestamps, freshness decisions and contributing retrieval channels, not
new assertions or eligibility decisions. Consumers must treat passage text as
untrusted source material and resolve conflicting facts with structured observations.

Production PostgreSQL uses the existing GIN `simple` full-text index (including
Arabic tokens) and pgvector cosine distance. Reciprocal-rank fusion combines up to
200 keyword and 200 vector candidates into at most 20 results. Exact filters cannot
be overridden by vector similarity. The SQLite fixture fallback uses bounded text
matching and cosine ranking; its vector scan is capped before ranking and is not
a production recall benchmark. PostgreSQL can choose exact filtered vector scans;
the existing HNSW index remains available, without sacrificing exact scope filters.

### OpenRouter vector index (#146)

The backend sends both passage batches and query text to `https://openrouter.ai/api/v1/embeddings` with model `openai/text-embedding-3-small`, 1,536 dimensions, and the OpenAI serving provider pinned inside OpenRouter. Configure `OPENROUTER_API_KEY` and `TECHSCOUT_EMBEDDING_USD_PER_MILLION` in the backend environment or repository-root `.env`. The rate is a positive all-inclusive USD bound per million input tokens. OpenRouter listed USD 0.02 per million input tokens for this model on 23 November 2026. Check the current route and applicable charges before setting the bound. [OpenRouter embedding models](https://openrouter.ai/api/v1/embeddings/models) · [OpenRouter embeddings API](https://openrouter.ai/docs/api/reference/embeddings)

The routed index uses the document index version with the suffix `/openrouter-v1`, the full model slug, and the dimension count. Resume fills only missing passage vectors in this space. Historical direct-provider vectors and receipts retain their original keys. Building or querying the routed index preserves source observation times. Reusing an unchanged specification or policy passage after a price change costs zero indexing calls.

Buyer research checks the permitted source, field and identity scope for routed vectors before requesting a query embedding. Each research task can reuse its vector across scoped lookups. Its model-tool permission, deadline, cancellation flag, call count and spend allowance apply. A missing key or rate, unusable response, unavailable route, or absent compatible index produces a keyword-only status in the research trace. Structured observations and text retrieval remain available. Newly acquired research pages use the same indexing service. Background scheduling is tracked in #88.

Each request reserves a bounded amount in the shared ledger before dispatch. The embedding-call table links the run or job, purpose, model, index version, token usage and known billed cost. The matching shared-ledger provider receipt additionally records request and serving metadata when reported, dimensions, latency and the rate estimate. The receipt keeps the provider-reported cost separately from the settled amount. Missing billed cost or unknown BYOK status retains the full reservation as uncertain. A valid vector may still be used. Query text, vectors and credentials are excluded from these accounting receipts.

### Bounded route evidence for #80

From a backend shell with the migrated application database, the shared accounting configuration, the team key and the checked rate, run:

```sh
python -m app.embeddings --check-route
```

Each explicit invocation sends at most one request with the public text `TechScout`, a maximum spend of USD 0.000001, and an 18-second response deadline. The JSON report identifies the durable bootstrap job and contains sanitized receipt fields. A `validated` result confirms the model and vector shape. Billing can still be uncertain.

On 23 November 2026, the issued team key returned a 1,536-dimensional vector. OpenRouter reported the serving provider as `OpenAI` and the model as `text-embedding-3-small`. The validator accepts this upstream name when OpenRouter identifies OpenAI as the serving provider. Index and query receipts retain the full requested slug and the original reported name. [Calibration receipt and billed usage](https://github.com/han33-dotcom/TechScout/issues/80#issuecomment-7110917208)
