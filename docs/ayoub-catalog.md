# Ayoub Computers marketplace catalog

Task #55 (T19) adds `app.adapters.search_catalog(fetch, "ayoub_computers", category=..., search=...)`
for laptop and monitor candidates on ayoubcomputers.com, a BigCommerce multi-vendor marketplace.
Like the [Mojitech catalog](mojitech-catalog.md), it returns v2 `FieldObservation`/`Extraction`
records and raw snapshots, not verified offers. Ayoub candidates (`MarketplaceCandidate`) also
carry the listing's `seller`, `fulfiller`, `warranty` and `identifiers` (SKU, MPN).
`CATALOG.fetch_policies(fetch)` reads the terms and delivery guide as source-scoped policy text.
Current-run verification, identity and eligibility still decide recommendations (#76).

## Retrieval bounds

| Call | Endpoint | Capability |
|---|---|---|
| Search | `search.php?search_query=<text>&section=product`, one page | `ayoub_computers-search` |
| Product | `products.php?productId=<id>`, at most two per call | `ayoub_computers-product` |
| Policies | `terms-of-service/` and `quick-delivery-guide/` | exact allowed endpoints |

The registry limits are 6 requests per minute, one at a time, and 6 per run. A run's search
(1), two product pages (2) and both policy pages (2) fit, with one spare request for a retry.
Six a minute matches the 10-second crawl delay in Ayoub's robots.txt. That delay is addressed
to a list of AI crawlers; our `TechScout/0.1` agent falls under `*`, which disallows only
`_bc_fsnf=1` URLs. Re-reading with `only={(product_id, variant_id)}` fetches those products
directly, without discovery or cached data.

Product pages are fetched by ID. Ayoub's product URLs are a bare `/<slug>/`, and capabilities
must begin with a literal path segment. BigCommerce's `products.php?productId=N` returns the
same page with status 200, no redirect (checked live for 80559). Its query name is camel case,
so `QueryKey` now accepts upper-case letters. Names still match exactly, and a camel-case name
is also checked against the action denylist (`addToCart` is refused). Names differing only in
case cannot be declared together.

A search page holds about 20 cards and only two product pages are read, so discovery ranks
cards first. Cards whose titles contain every search word come first; among those, cards
naming an accessory (case, sleeve, stand, ...) that the search did not ask for come last. This
ranking orders reads only: no fact comes from a card. A product page must show its category's
mandatory specifications as structured fields to be read as that category: CPU, RAM and
storage for laptops; resolution, refresh rate and size for monitors. Other pages are counted
in `skipped_not_category`. Phones, tablets and audio are not supported: the audit did not
establish Ayoub phones, and no tablet or audio page was checked.

## What a listing yields

Every fact cites the product-page snapshot and is bound to its own product and configuration.

| Field | Source on the page | Rule |
|---|---|---|
| `price` | BCData `price.without_tax` and JSON-LD `offers.price` | Decimal (at most cents); USD/LBP/EUR/AED; disagreement is a conflict |
| `stock` | BCData `instock`, JSON-LD `availability`, an `AVAILABILITY` custom field | Online stock only; needs the store's own flag; backorder and pre-order stay unknown |
| `purchasable` | BCData `purchasable` | |
| `condition` | Details list, JSON-LD `itemCondition`, `CONDITION` field | new, used or refurbished; anything else is unknown |
| specs | Custom fields (`CPU`, `RAM`, `SSD`, `GPU`, `SIZE`, `Resolution`, `REFRESH RATE`), plus explicit `RAM:`/`Memory:`/`Storage:` lines in the description | Titles are never parsed; 1 TB is 1024 GB as elsewhere |
| `variant.color` | A `Color` option or custom field | |
| `seller`, `fulfiller_id` | A listing custom field `Sold By`/`Seller`/`Vendor` or `Fulfilled By`/`Shipped By` | See below |
| `warranty.months` | The warranty tab and a `WARRANTY` field ("1 Year" is 12) | Provider and territory stay unknown |
| `policy.tax` | Footer: "Prices exclude 11% VAT, applied at checkout" | Source-scoped text |
| `policy.marketplace` | The store-wide marketplace notice | Source-scoped text |

Price is unknown unless the listing is explicitly purchasable. Missing, empty, zero, negative,
malformed, sub-cent or call-for-price amounts never become a price. Products with selectable
options (for example two colours) become one candidate per combination, at most 12. Their
price, stock and purchasability stay unknown, because BigCommerce loads them only after a
selection. Checkbox, text or optional options are refused as unsupported. Nothing is resolved as
a cost here: the VAT footer and the delivery guide are kept as text for the cost engine.

### Sellers and fulfillers

Ayoub's terms say a product is sold by Ayoub Computers or by an approved third-party vendor,
and that "the seller is clearly identified on the product page". On all five product pages
checked, though, the only seller-related text is a store-wide notice. It says "Fulfilled by
AyoubComputers.com", and the same notice says certain products "may be sold, invoiced, and
fulfilled directly by independent third-party vendors". It sits in BigCommerce's
`product_below_price--global` region, which is the same on every product page.

The reader therefore keeps seller and fulfiller **unknown** for every observed listing. It never
assumes Ayoub is the seller. A listing-specific custom field naming a party is read when present:

- Ayoub Computers (or AyoubComputers.com) gets the operator ID `ayoub_computers`.
- Every other name gets its own ID, `ayoub_computers.vendor.<name>`.
- Two different names on one listing are a conflict.

Each listing is a separate BigCommerce product, so two listings of one model stay two candidates
with their own sellers. Synthetic tests cover three vendors on one model, because no real
listing has named a vendor yet. Names that are not a short plain name are refused.

### Warranty, stock scope and delivery

The listing gives the warranty duration only. The terms say warranties are fulfilled by service
partners, brand centres or Ayoub, depending on sourcing, and that ayoubcomputers.com acts as
warranty guarantor. Both statements are kept as `policy.warranty`; the listing's provider stays
unknown.

Stock is online availability. The terms say online items may ship from vendors or remote
warehouses and are not immediately available at retail locations, so no branch stock is
produced (`policy.pickup`).

The delivery guide promises free delivery to every Lebanese district in 2–5 or 4–6 business
days. It excludes villages within 5–7 km of the southern border. The cost engine still needs a
route-specific check before treating delivery as free.

## Live evidence, 17 November 2026

Fourteen bounded direct requests were made with our user agent, 10 seconds or more apart, plus
the gated smoke below; there were no logins, carts or forms. The full log is in
[source coverage](source-coverage.md#ayoub-computers-live-check-2026-11-17), and minimized
captures are under `tests/fixtures/stores/ayoub_computers/` (provenance in
`tests/fixtures/stores/PROVENANCE.md`).

Five real product pages were parsed, and each full capture gives the same facts as its minimized
fixture:

- **Xiaomi A27Ui monitor (80559):** USD 367, purchasable and in stock; 3840×2160, 60 Hz, 27 inches;
  no warranty published.
- **MacBook Air M4 MW0X3, two listings (38080 and 38082):** one model, two products and two SKUs.
  - 38080 has no price. Its stock is a conflict: an `AVAILABILITY: In Stock` field against the
    store's out-of-stock flag.
  - 38082 publishes USD 1,129 but is not purchasable. Its SSD field says 256 GB while its
    description says 512 GB, so storage is a conflict.
- **MacBook Air M5 (74423):** two colour options, so two configurations without prices.
- **Acer Aspire 5 (28342):** no price, out of stock, new, 1-year warranty.

**Gap: the gate cannot read Ayoub today.** Every page observed was 2.46–2.72 MB decoded, above
the gate's 2 MiB response cap (`MAX_RESPONSE_BYTES`, from the MVP spec). Most of each page is the
site's navigation menu, about 4,000 links. The product data sits after the first 2 MB. The
gated smoke (`ayoub-catalog-smoke.json`) shows the refusal. This is a technical gap, not a
permission issue. Changing a shared, security-relevant limit is a team decision. Options are a
per-source cap, a cap that keeps only the product region, or a managed extraction route (#58).
Until one is chosen, live Ayoub retrieval returns `BLOCKED` with no candidates.

Other gaps:

- No listing names its seller or fulfiller.
- Option-specific prices are not on the page.
- Search returns many accessories.
- The 11% VAT and route eligibility are not resolved here.
