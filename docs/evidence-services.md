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
