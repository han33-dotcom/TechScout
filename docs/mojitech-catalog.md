# Mojitech catalog acquisition

Task #52 adds `app.adapters.search_catalog(fetch, "mojitech", category=..., search=...)`
for phone, laptop and monitor candidates. Supply the controller's budget-bound SourceGate fetch,
the current run reference and the corpus version when integrating it with a persisted corpus.
The default `catalog-preview-v1` denotes an unpersisted preview. The return value contains v2
`FieldObservation`/`Extraction` records and raw snapshots, not verified offers. Store snapshots
before observations; use existing category normalization, eligibility and current-run finalist
verification before recommendations. The browser integration remains #75/#76; the v1 phone
adapter API is unchanged.

One search page (10 records), at most two products and 40 variants per product are processed.
A search plus exact API and HTML reads uses at most five logical fetches. Existing gate limits
(6 requests/minute, one concurrent request, six attempts/run) still apply, including retries.
Full pages/product bounds produce a partial result; coverage is never represented as exhaustive.
Unknown category slugs, accessories and unsupported URL shapes are skipped. Reviewed paths are
four/five-segment `/shop/.../` pages and `/product/<slug>/`, with exact slug API reads.

Simple products use their own product ID as the configuration ID. Variable products retain real
variation IDs and the parent's selected attribute values, checked against the product's HTML
variation form. Parent/range prices never establish variant prices. Explicit RAM/storage/CPU,
phone SIM/region and monitor size/resolution/refresh attributes become field-bound evidence.
Ambiguous SIM names stay absent; unknown attributes remain in `selected_attributes` and snapshots.
No title-based configuration guessing, default new condition, free delivery or included VAT is
introduced. Existing policy readers remain available; fees and taxes require their own evidence.

Zero, missing, malformed or call-for-price amounts cannot establish prices. Non-purchasable or
incompletely bound configurations keep price unknown. Contradictory API/form prices remain a
conflict. `priced` means only a published price plus explicit purchasability; it does not certify
stock, identity equivalence, total cost or buyer eligibility. Bounded discovery can return priced
alternatives alongside an unpriced listing. Re-reading with `only={(product_id, variant_id)}`
uses fresh discovery and responses; missing configurations report an explicit gap.

## Evidence, 17 November 2026

Offline tests cover synthetic laptop/phone/monitor products, 16/32 GB configurations, price
changes, duplicate/conflicting records, wrong parent identity, unsafe URLs, discovery bounds,
and the minimized real iPhone capture (all 12 variants remain unpriced). Synthetic examples
are test data, not claims about Mojitech inventory.

[Live smoke trace](mojitech-catalog-smoke.json): three manual gated discovery attempts, for
iPhone, Lenovo and Samsung, each returned HTTP 202 and no usable catalog. The gate correctly
reported `INVALID_SCHEMA: unexpected status 202`; no challenge bypass was attempted. This is an
access gap, not proof that Mojitech has no matching products. Current multi-category live
extraction is unverified. The issue permits explicit access gaps plus fixtures as acceptance.

Probe envelope: 90 seconds, six external attempts maximum; actual three attempts, no paid
provider/model/extraction calls and $0 provider spend. This was a manual access smoke, not
initial corpus collection, and used the existing local RunBudget. No central collection ledger
was configured or credited with ingestion. Operational research/collection must supply the
shared-accounting budget from #65; this adapter never constructs a budget or opens connections.
