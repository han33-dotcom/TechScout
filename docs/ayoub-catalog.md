# Ayoub Computers marketplace catalog

Task #55 (T19) adds `app.adapters.search_catalog(fetch, "ayoub_computers", category=..., search=...)`
for laptop and monitor candidates on ayoubcomputers.com, a BigCommerce multi-vendor marketplace.
Like the [Mojitech catalog](mojitech-catalog.md), it returns v2 `FieldObservation`/`Extraction`
records and raw snapshots, not verified offers. Ayoub candidates (`MarketplaceCandidate`) also
carry what a marketplace listing must keep apart:

- the listing's `seller`, `fulfiller` and `warranty`;
- `identifiers` (SKU, MPN), for listings without options;
- `notices`: the page's store-wide statements, kept apart from the listing's own `extraction`;
- `costs`: the VAT, marked unknown;
- `evidence`: the receipts for identifiers and costs.

`CATALOG.fetch_policies(fetch)` reads the terms and delivery guide as source-scoped policy text.
Current-run verification, identity and eligibility still decide recommendations (#76).

## Retrieval bounds

| Call | Endpoint | Capability |
|---|---|---|
| Search | `search.php?search_query=<text>&section=product`, one page | `ayoub_computers-search` |
| Product | `products.php?productId=<id>`, at most two per call | `ayoub_computers-product` |
| Policies | `terms-of-service/` and `quick-delivery-guide/` | exact allowed endpoints |

The source is registered with these capabilities and limits, but it stays `pending`, outside
the live release, until the access gaps below are resolved. Until then the gate refuses it
(`SOURCE_DISABLED`) before any network use, so runs spend nothing on a store that rejects
them. Like the PC and Parts reader (#54), the code is available to explicit callers and
tests. Admitting it later means setting `status: active`, adding an admission record and
adding the ID to `live_release`. The registry limits are 6 requests per minute, one at a
time, and 6 per run. A run's search
(1), two product pages (2) and both policy pages (2) fit, with one spare request for a retry.
The gate counts requests per minute and does not space them, so a run's few requests can go
back to back. Ayoub's robots.txt asks for a 10-second crawl delay, but only from a list of AI
crawlers. Our `TechScout/0.1` agent falls under `*`, which disallows only `_bc_fsnf=1` URLs.
Re-reading with `only={(product_id, variant_id)}` fetches those products directly, without
discovery or cached data.

Product pages are fetched by ID. Ayoub's product URLs are a bare `/<slug>/`, and capabilities
must begin with a literal path segment. BigCommerce's `products.php?productId=N` returns the
same page with status 200, no redirect (checked live for 80559). Its query name is camel case,
so `QueryKey` now accepts upper-case letters. Names still match exactly. A name is refused if,
in any case and with separators removed, it spells an action word (`addToCart`, `ADDtoCart`,
`MYaccount` and `WCajax` are all refused). Names differing only in case cannot be declared
together.

A search page holds about 20 cards and only two product pages are read, so discovery ranks
cards first. Cards whose titles contain every search word come first; among those, cards
naming an accessory (case, sleeve, stand, ...) that the search did not ask for come last. This
ranking orders reads only: no fact comes from a card. A product page must show its category's
mandatory specifications as structured fields to be read as that category: CPU, RAM and
storage for laptops; resolution, refresh rate and size for monitors. Other pages are counted
in `skipped_not_category` and reported as a partial result. A skip can be an accessory or a real
product whose specifications are written in another form. Phones, tablets and audio are not supported: the audit did not
establish Ayoub phones, and no tablet or audio page was checked.

## What a listing yields

Every fact cites the product-page snapshot and is bound to its own product and configuration.

| Field | Source on the page | Rule |
|---|---|---|
| `price` | BCData `price.without_tax` and JSON-LD `offers.price` | Decimal (at most cents); USD/LBP/EUR/AED; disagreement is a conflict |
| `stock` | BCData `instock`, JSON-LD `availability`, an `AVAILABILITY` custom field | Online stock only; needs the store's own flag; backorder, pre-order and any availability text other than plain "In Stock"/"Out of Stock" stay unknown |
| `purchasable` | BCData `purchasable` | |
| `condition` | Details list, JSON-LD `itemCondition`, `CONDITION` field | new, used or refurbished; anything else is unknown |
| specs | Custom fields (`CPU`, `RAM`, `SSD`, `GPU`, `SIZE`, `Resolution`, `REFRESH RATE`), plus explicit `RAM:`/`Memory:`/`Storage:` lines in the description | Titles are never parsed; 1 TB is 1024 GB as elsewhere; a plain unit word ("256GB SSD") or a short label ("(4K UHD)") is allowed; other wording is an unknown reading, never dropped |
| `variant.color` | A `Color` option or custom field | |
| `seller`, `fulfiller_id` | A listing custom field `Sold By`/`Seller`/`Vendor` or `Fulfilled By`/`Shipped By` | See below |
| `warranty.months` | The warranty tab and a `WARRANTY` field ("1 Year" is 12) | Provider and territory stay unknown |
| `notices`: `policy.tax` | Footer: "Prices exclude 11% VAT, applied at checkout" | Source-scoped text |
| `notices`: `policy.marketplace` | The store-wide marketplace notice | Source-scoped text |
| `costs`: tax | The same footer | `Charge(kind='tax', state='unknown')`: "listed price excludes VAT; the store adds it at checkout"; without the footer, "tax treatment is not published"; never zero, never computed here |
| `identifiers` | BCData and JSON-LD SKU, MPN, UPC, GTIN | Kept when the two agree, with receipts; omitted for option products, whose embedded identifiers describe the default selection |

Receipts point at the field they read, for example `/custom-fields/7/Sold By` or
`/BCData/product_attributes/instock`. An excerpt is attached only when the raw page holds that
text verbatim, so every excerpt is a quotation, as corpus storage requires.

Price is unknown unless the listing is explicitly purchasable. Missing, empty, zero, negative,
malformed, sub-cent or call-for-price amounts never become a price. Products with selectable
options (for example two colours) become one candidate per combination, at most 12, checked
before any combination is built. Their price, stock and purchasability stay unknown, because
BigCommerce loads them only after a selection.

The following are refused as unsupported:
- checkbox, text or optional options;
- repeated option IDs;
- a second or nested custom-field table, details list or warranty tab.

The parser takes constant time per element, so deeply nested markup cannot stall it. Omitted
`</td>` and `</tr>` end tags are closed at the next row, as browsers do.

The only cost recorded is the unknown VAT charge. The delivery guide is kept as text for the
cost engine.

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
- Every other name gets its own ID, `ayoub_computers.party.<slug>-<digest>`. The digest is of
  the exact spelling, ignoring case and spacing, so "A.B. Tech" and "A-B Tech" stay apart.
- Two different names on one listing are a conflict.

A v2 `Offer` needs a seller ID. A candidate whose seller is unknown therefore stays
provisional, and integration (#76) must never default its seller to `ayoub_computers`.

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

Fifteen bounded direct requests were made with our user agent, mostly 10 seconds or more apart.
Four more went through the real gate in the smoke below. There were no logins, carts or forms.
The full log is in
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

**Gap: the gate cannot read Ayoub today, for two reasons.**

1. **The edge refuses the gate's client.** The [gated smoke](ayoub-catalog-smoke.json) at
   16:39 UTC ran while Ayoub was briefly admitted on the working branch. It sent four requests
   through the real `SourceGate` (search, one product, both policy pages). Each got HTTP 403 from Ayoub's Cloudflare edge, so the run reported `HTTP_ERROR` with
   no candidates and $0 spend. At 16:40 a plain Python client with the same user agent got 200
   for the same product URL. The refusal is specific to the gate's HTTP client, not a block on
   our address. It is bot protection, so no workaround (headers, TLS settings, browsers) was
   attempted.
2. **The pages are too large.** Every page observed was 2.46–2.72 MB decoded, above the gate's
   2 MiB response cap (`MAX_RESPONSE_BYTES`, from the MVP spec). Most of each page is the site's
   navigation menu, about 4,000 links, and the product data sits after the first 2 MB. So even
   an accepted request would be refused as `blocked:size`.

Both are technical gaps, not permission issues, and both need a team decision. The candidate
route is the managed extraction fallback (#58), which must also respect the size limits.
Another is a per-source cap together with a client the store accepts. Until then, live Ayoub
retrieval reports errors and no candidates; it never reports stale or invented facts.

Other gaps:

- No listing names its seller or fulfiller.
- Option-specific prices are not on the page.
- Search returns many accessories.
- The 11% VAT and route eligibility are not resolved here.
