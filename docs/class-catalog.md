# Class Cellulars catalog reader

Task #51 extends the existing WooCommerce reader, without treating catalog prices as verified offers.

## Coverage and bounds

The adapter supports variable **phones** recognized by `app/devices.py`, including iPhone 16 and 17 families and the listed Galaxy/Pixel models when actually present in Class's mobile category. This is parser coverage, not a claim of retailer inventory. Laptops, tablets, monitors, audio, simple products and unrecognized phones remain unsupported here; discovery does not manufacture their identity.

The existing iPhone 17 route remains compatible. Other exact models use one Store API search, capped at ten records and two matching parent products. Mobile category, model, parent identity, canonical same-host permalink and slug checks reject accessories and unsafe targets. A full page or additional matching parents produces explicit truncation. No automatic pagination or catalog crawl occurs.

Each selected parent is re-read through its scoped slug endpoint and product page. The shared parser preserves variant IDs, Decimal minor units, product-bound VAT, conflicting delivery evidence and unknown condition/region. Discovery snapshots are retained alongside detail snapshots. Final refresh rediscovers the request model without a process-global cache, then re-fetches selected products and only returns requested variants. Missing or changed identities fail explicitly.

Version 1 capabilities authorize only the bounded search, single slug query and `/product/<slug>/` GET. Existing source gate checks still enforce public DNS, TLS, redirects, body sizes and deadlines. The source remains at one concurrent request and six per minute. Its per-run ceiling is twelve: up to five discovery/detail requests, one FAQ, five rediscovery/final detail requests, plus one spare. This ceiling never overrides the controller's time or total-call budget.

## Evidence and limits — 17 November 2026

The original 14 November minimized iPhone 17 API/page/FAQ captures and their provenance remain unchanged. `tests/adapters/test_class_catalog.py` derives explicitly synthetic iPhone 16 records and IDs from those shapes; these are regression fixtures, not new live captures or purchase evidence. Tests cover fresh-instance refresh, exact money/unknown condition, category/accessory exclusion, unsafe targets, duplicates, truncation, malformed replies and budget/access failure. Existing VAT/stock/delivery regressions still apply.

`class-catalog-smoke.json` records two bounded live gate attempts on 17 November: model search and iPhone 16 slug. Both failed with INVALID_SCHEMA before a usable response was accepted. No successful live multi-model extraction or qualifying offer is claimed. The 16 November audit also recorded direct access failures. Search-engine visibility is not live gate evidence. Recovery of direct access and fresh provenance captures is still required before claiming live coverage.
