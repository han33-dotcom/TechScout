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

`EmbeddingService` explicitly invokes `text-embedding-3-small` at 1536 dimensions.
`OpenAIEmbeddings` supplies a direct API transport, with explicit credentials,
bounded time/response size and no automatic retries. Index writes preserve chunk
and index versions and skip already-indexed passages. Query and indexing calls
both record owner, model, index, tokens and Decimal costs. Configure the applicable
USD-per-million rate; there is no hard-coded operational price.

Migration 0005 stores a conservative allowance before a provider call. Failed or
ambiguous calls retain an uncertain cost and reserved allowance; missing usage is
never recorded as zero. A call requires an explicit spend allowance. The shared
aggregate reservation/controller integration belongs to #65: callers must allocate
from that budget before invoking this service. No provider call is made by ordinary
tests or automatically during search. Tests inject synthetic vectors; the bilingual
fixtures demonstrate ranking/filter mechanics, not measured live model quality.

Implementation references: [OpenAI embeddings API](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create)
and [pgvector SQLAlchemy support](https://github.com/pgvector/pgvector-python#sqlalchemy).
