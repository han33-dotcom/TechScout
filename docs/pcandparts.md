# PC and Parts exact-listing retrieval

Implementation for [#54](https://github.com/han33-dotcom/TechScout/issues/54).
`app.adapters.pcandparts.read_listing` connects an explicitly selected laptop or
monitor listing to the existing `read_page` source gate, shared ledger and corpus
capture path. It does not search the whole site or activate a retailer capability.
The controller must supply a reviewed product/SKU mapping, a matching `ResourceKey`
with the selected route ID, a domestic `DirectRoute`, and processing versions.

## Implemented and tested

- Exactly one JSON-LD Product for the requested SKU and one bound Offer are
  required. Aggregate prices, duplicate matching products, wrong URLs and other
  named sellers fail closed. A cheaper sibling SKU does not replace the selection.
- The shared extractor supplies exact Decimal price, online stock, condition,
  model, color and RAM/storage. The dedicated profile adds the explicit MPN,
  manufacturer, laptop CPU/GPU/OS, monitor dimensions/refresh/panel, and packaged
  shipping weight. Missing values stay absent/unknown; repeated conflicting
  configuration values stay conflicts. Product weight is not packaged weight.
- Item-level `priceSpecification.valueAddedTaxIncluded: true` can establish tax
  inclusion only for the selected Offer, without a conflicting price/currency.
  A false or absent flag does not establish a tax amount. Sibling-product tax
  statements and general policy wording cannot settle this item's tax.
- A single published shipping rate is retained as a **quote candidate** only
  when currency, exact destination city/postcode and packaged weight are present.
  Country or region coverage alone is insufficient. The candidate remains bound
  to the selected product, SKU and route. It does not certify delivery eligibility.
- Shipping `Charge` remains unknown even when a base rate exists: zone/weight
  applicability, surcharges and complete quote terms have not been validated.
  No estimated rate is silently promoted into a verified delivered total. Online
  stock does not prove pickup stock or purchasability.

`Listing.extraction` contains corpus-compatible specification observations and
passages. Additional part/weight/quote and tax receipts are exposed in
`Listing.evidence`; their passages preserve the original document, source pointer,
body hash, timestamp, variant/route and run provenance. The existing wire contracts
are unchanged. A future controller must bind these into an Offer and independently
obtain missing eligibility, purchasability and complete costs before verification.
The adapter is available to explicit callers; wiring it into automatic research
selection and the full buyer flow remains integration work under #76.

## Evidence and current limitations — 17 November 2026

The fixtures in `tests/fixtures/stores/pcandparts/` are **synthetic**. They define a
conservative supported metadata profile, not captured retailer listings or proof
that the live site publishes these fields in this shape. No source admission or
capability was added. JSON fixture names and values are deliberately fictional.

The [16 November source audit](retailer-audit-2026-11-16.md) records HTTP 403 for
the homepage, sampled laptop and shipping page. That failure remains unresolved.
The audit also records destination/weight-dependent shipping, possible surcharges,
appointment pickup and the need for item-specific tax evidence. None supplies a
current complete offer. This task made **zero live retailer/provider calls**:
shared accounting provisioning/reconciliation remains blocked in
[#60](https://github.com/han33-dotcom/TechScout/issues/60).

This implements #54's explicitly permitted fixture-and-documented-blocker outcome;
it does **not** establish live PC and Parts support. After accounting readiness,
an operator must review/admit a narrow path, perform bounded recorded laptop and
monitor reads, confirm exact SKU/MPN mapping and metadata, and resolve delivery
quote applicability before enabling live coverage. Access failures remain failures;
there is no automatic managed fallback, checkout, account creation or messaging.

## Reproduce offline

From the repository root with the configured Python environment:

```sh
python -m unittest discover -s tests -p test_pcandparts.py -v
python -m unittest discover -s tests -v
```

Cases cover laptop/monitor fields, sibling and seller isolation, conflicting
attributes, city/postcode/weight/currency gaps, tax binding, current-run receipts,
gate/accounting integration, corpus persistence and HTTP 403 behavior. Fixtures
do not require a live ledger or any retailer/provider credentials.
