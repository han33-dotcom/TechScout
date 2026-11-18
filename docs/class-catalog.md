# Class Cellulars catalog reader

Task #51 extends the existing WooCommerce reader, without treating catalog prices as verified offers.

## Coverage and bounds

The adapter supports variable **phones** recognized by `app/devices.py`, including iPhone 16 and 17 families and the listed Galaxy/Pixel models when actually present in Class's mobile category. This is parser coverage, not a claim of retailer inventory. Laptops, tablets, monitors, audio, simple products and unrecognized phones remain unsupported here; discovery does not manufacture their identity.

The existing iPhone 17 route uses the direct slug lookup. Other exact models use one Store API search restricted to Class Cellulars' Mobiles category (ID 132), capped at ten records and two matching parent products. The adapter still checks the returned category, exact model, parent identity, canonical same-host permalink and slug. A full page or additional matching parents produces explicit truncation. No automatic pagination or catalog crawl occurs.

Each selected parent is re-read through its scoped slug endpoint and product page. The shared parser preserves variant IDs, Decimal minor units, product-bound VAT, conflicting delivery evidence and unknown condition/region. Discovery snapshots are retained alongside detail snapshots. Final refresh rediscovers the request model without a process-global cache, then re-fetches selected products and only returns requested variants. Missing or changed identities fail explicitly.

Search capability version 2 requires category 132. The single slug query and `/product/<slug>/` GET remain on capability version 1. The source gate enforces public DNS, TLS, redirects, body sizes and deadlines. The source allows one concurrent request and six per minute. Its per-run ceiling is twelve: up to five discovery/detail requests, one FAQ, five rediscovery/final detail requests, plus one spare. This ceiling never overrides the controller's time or total-call budget.

## Evidence and limits — 17 November 2026

The original 14 November minimized iPhone 17 API/page/FAQ captures and their provenance remain unchanged. `tests/adapters/test_class_catalog.py` derives explicitly synthetic iPhone 16 records and IDs from those shapes; these are regression fixtures, not new live captures or purchase evidence. Tests cover fresh-instance refresh, exact money/unknown condition, category/accessory exclusion, unsafe targets, duplicates, truncation, malformed replies and budget/access failure. Existing VAT/stock/delivery regressions still apply.

`class-catalog-smoke.json` records the two live gate attempts on 17 November. Both failed with `INVALID_SCHEMA` before a usable response was accepted.

## Live smoke on 19 November 2026 in Beirut

The unfiltered iPhone 16 search returned ten accessory listings. The exact product
and slug endpoints returned parent 7761 in Mobiles (category 132). With the fixed
category search, the full adapter extracted three iPhone 16 128 GB variants and
four iPhone 17 256 GB variants. Prices were parsed. Condition remained unknown.
These were catalog candidates. Buyer flow and final offer verification were outside
the smoke check.

The five successful GETs ran through the normal source gate and authoritative
PostgreSQL accounting as `issue-51-mobile-discovery-2026-11-19` at
22:14:47–22:14:49 UTC on 18 November. The ledger recorded 3.346 active seconds,
zero paid calls and USD 0.00. SHA-256 hashes cover the decoded bodies.

| Endpoint | SHA-256 |
| --- | --- |
| iPhone 16 category search and exact slug (identical bodies) | `360861ba0e82d45ebbd8916f9f8be791bf3edb43d68c5cd5a8c0873656091503` |
| iPhone 16 product page | `d4a6d2d64e8bd451a845d283e483b0109e043e67b9c058a386418957e7ec6a05` |
| iPhone 17 slug | `30e2ff33d4c529fe7c5e902e4565b56708f7a1b24f5771ad999f1d0c5ba9030b` |
| iPhone 17 product page | `52b32685380c3f581dc8d1bb31647a58b57ecdb6c72e40ec784189b086eafd55` |
