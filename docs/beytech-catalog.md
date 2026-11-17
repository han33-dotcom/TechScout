# Beytech catalog and policies

Task #53 exposes `app.adapters.search_catalog(fetch, "beytech", category=..., search=...)`
for phones, laptops and monitors, using the shared bounded WooCommerce acquisition module.
The existing v1 phone/policy adapter remains compatible. The new API returns v2 corpus
observations and snapshots; see [catalog integration and bounds](mojitech-catalog.md).
Pass the controller's budget-bound gate fetch, run reference and corpus version. Neither
catalog nor policy readers open a connection or create a budget themselves.

Exact API/HTML IDs and selected attributes distinguish parent and variation records even when
their names and slugs look identical. Identical parent repeats are deduplicated; conflicting
ones fail closed. `M Size` is a storage attribute, while `1 Sim` remains unresolved. Numerically
equal Decimal prices such as `1079.00` and `1079` are one value; genuine disagreements remain
conflicts. Explicit variant-bound condition attributes may establish condition; site navigation,
returns packaging rules and a lack of used items never establish "new".

`beytech.CATALOG.fetch_policies(fetch, ...)` reads the existing reviewed warranty and exchange
pages. It returns source-bound policy text for warranty/returns, explicit country-wide delivery,
VAT inclusion/exclusion and a store-wide new-device statement when actually present. Missing
delivery/tax/condition/warranty topics are named in the result. These statements do not resolve
an offer's tax, shipping charge, guaranteed arrival, warranty territory or condition by themselves.
Route- and product-specific application remains eligibility/integration work. The reader preserves
multiple sentences and does not reinterpret conflicting text as a resolved monetary amount.

## Acceptance evidence — 17 November 2026

[Bounded live trace](beytech-catalog-smoke.json) retrieved two distinct products and **10 priced
configurations**: four iPhone 17 and six iPhone 17 Pro variations, using five successful GETs.
The search hit its configured two-product bound and correctly returned partial coverage. This
proves multi-product extraction at the recorded time, not qualifying recommendations. SIM type,
condition, VAT, shipping fees, territory and route qualification remain incomplete.

A sixth GET retrieved warranty and Lebanon-delivery statements. The seventh logical call, for
exchange policy, was refused before transmission by the existing per-run source limit. The
policy result is partial and retains warranty evidence while reporting missing tax and condition.
The earlier four-endpoint access check had already retrieved exchange successfully; the preserved
minimized exchange fixture exercises its parser offline. No limit was raised to force completion.

The confirming smoke had a 110-second / eight-call local envelope and six actual attempts.
Including four initial access checks and five diagnostic catalog reads, this task used 15 manual
Beytech GET attempts, no paid provider calls and $0 provider spend. These were manual source
checks using the existing local RunBudget, not initial ingestion; operational collection must use
the shared ledger. Nothing was purchased or submitted.

`tests/fixtures/stores/beytech/catalog-2026-11-17.json` retains minimized fields and variation forms
from the two live products. Original response URLs, timestamps and hashes are in the trace;
fixture minimization changes the fixture hash. Offline replay proves ten distinct configurations
from two models. Synthetic fixtures separately exercise laptop and monitor fields; **live laptop
and monitor extraction is not established by this phone sample**. Tests also cover identical
parents, Decimal equality, explicit condition, absent policy topics and partial gate denial.
