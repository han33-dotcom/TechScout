# Ayoub Computers marketplace catalog

The [reader](../app/adapters/) extracts laptop and monitor candidates from public BigCommerce listing data. On a marketplace the seller and fulfiller are separate facts. A site-wide fulfillment statement leaves the actual seller of a listing unresolved.

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

## Observed coverage · November 17, 2026

All four gated reads returned HTTP 403. Direct reads also showed that decoded product pages exceeded the gate’s 2 MiB cap. The selected fixtures include an unpriced option, conflicting SSD sizes and conflicting stock signals. A reader that handles those fixtures has yet to demonstrate a working live offer in this checkpoint.

[Gated trace](ayoub-catalog-smoke.json) · [Source observations](source-coverage.md#ayoub-computers-live-check-2026-11-17) · [Field mappings and fixture details](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/ayoub-catalog.md)
