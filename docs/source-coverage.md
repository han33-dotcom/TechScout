# Source coverage

The current full-product [Lebanese retailer audit](retailer-audit-2026-11-16.md)
records 12 priority sources and eight wider leads, with importable directory seeds
and dated access/policy findings. No qualifying-offer count is inferred from discovery.

Amhaz is blocked until the intended business is identified; see
[Amhaz (#56)](#amhaz-56-blocked-until-the-intended-business-is-identified) at the end.

## Ayoub Computers live check, 2026-11-17

Issue #55 (T19); the reader and its rules are in [Ayoub catalog](ayoub-catalog.md). These were
direct public GETs with the gate's user agent, `TechScout/0.1`. They were made one at a time, 10
or more seconds apart, with a 2 MB compressed read limit, and used no login, cart, form or
checkout. Sizes are decoded bytes. Rows marked *fixture* are kept, minimized, in
`tests/fixtures/stores/ayoub_computers/`.

| UTC | Path on ayoubcomputers.com | Status | Bytes | Seen |
|---|---|---|---|---|
| 15:41:53 | `/robots.txt` | 200 | 1,345 | `*` disallows only `_bc_fsnf=1`; a 10 s crawl delay for listed AI crawlers |
| 15:42:07 | `/acer-aspire-5-…-nx-khjem-007/` | 200 | 2,513,895 | *fixture*: laptop with no price, out of stock, new, 1-year warranty |
| 15:42:18 | `/search.php?search_query=iphone+17&section=product` | 200 | 2,698,761 | 20 cards, all cases and protectors; no phone |
| 15:46:05 | `/quick-delivery-guide/` | 200 | 2,489,466 | *fixture*: free delivery to every district; 2–5 / 4–6 business days; border villages excluded |
| 15:46:23 | `/terms-of-service/` | 200 | 2,518,812 | *fixture*: Ayoub or vendor sells; 11% VAT at checkout; warranty guarantor; pickup scope |
| 15:46:37 | `/search.php?search_query=macbook+air&section=product` | 200 | 2,718,505 | *fixture*: 20 cards, including two MW0X3 listings (38080 and 38082) |
| 15:46:54 | `/search.php?search_query=monitor&section=product` | 200 | 2,679,897 | 20 cards, mostly stands; Xiaomi A27Ui at USD 367 |
| 15:49:27 | `/apple-macbook-air-m4-…-mw0x3/` | timeout | | the 20 s read timed out; retried once at 15:51 |
| 15:49:58 | `/apple-macbook-air-m4-…-mw0x3-oxdv/` | timeout | | the 20 s read timed out; retried once at 15:51 |
| 15:50:30 | `/xiaomi-4k-monitor-a27ui-27-uhd-ips-usb-c-monitor/` | 200 | 2,528,172 | *fixture*: USD 367, in stock, purchasable, 3840×2160 at 60 Hz |
| 15:50:51 | `/apple-macbook-air-13-6-m5-…-1tb-ssd/` | 200 | 2,531,332 | *fixture*: two colour options; USD 2,050 for the default option, out of stock |
| 15:51:24 | `/apple-macbook-air-m4-…-mw0x3-oxdv/` | 200 | 2,510,946 | *fixture*: USD 1,129, not purchasable; SSD field 256 GB against "512GB" in the description |
| 15:51:38 | `/apple-macbook-air-m4-…-mw0x3/` | 200 | 2,510,230 | *fixture*: no price; `AVAILABILITY: In Stock` field against the store's out-of-stock flag |
| 15:58:50 | `/products.php?productId=80559` | 200 | 2,528,120 | the same Xiaomi page by ID, without a redirect |
| 16:39:30–31 | gated smoke: search, `products.php?productId=80559`, both policy pages | 403 ×4 | | through the real `SourceGate`, with Ayoub briefly admitted on the working branch: `HTTP_ERROR`, no candidates, $0 ([trace](ayoub-catalog-smoke.json)) |
| 16:40:03 | `/products.php?productId=80559` (direct) | 200 | 267,368 compressed | the same URL accepted for a plain client (Cloudflare); the 403 targets the gate's client |

Every product page shows only a store-wide marketplace notice: "Fulfilled by AyoubComputers.com".
The same notice says certain products may be sold and fulfilled by third-party vendors. No
listing names its own seller, so seller and fulfiller stay unknown.

The gate cannot read Ayoub yet. Its client gets HTTP 403 from the store's edge, and every page
also exceeds the gate's 2 MiB response cap. No bot-protection workaround was tried. Ayoub is
registered with read-only capabilities and limits but stays `pending` until the team picks a
route. These are technical gaps, not permission issues.

## Historical MVP source checks

On November 14 the team checked reviewed endpoints for listing variants, price, stock, fulfillment and policy coverage. Use the [dated source-by-source tables](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/source-coverage.md#historical-mvp-06-snapshot) and [fixture provenance](../tests/fixtures/stores/PROVENANCE.md) to reproduce that checkpoint.

# Amhaz (#56): blocked until the intended business is identified

**Status on 17 November 2026: blocked, no retrieval.** Issue #56 may use only the identity the
retailer audit (#50) established. The audit found one candidate storefront,
[amhazofficial.com](https://amhazofficial.com/) ("Amhaz Official", Saint Therese, Hadath), and
recorded its identity as `unresolved_user_intent` with no canonical seller: the user named
"Amhaz", and nothing shows which similarly named business was meant. Building retrieval for this
site would turn a guess into a seller identity, so #56 ships a blocked profile instead.

- **What ships.** `app/adapters/amhaz.py` declares no retrieval capability, no supported
  category and no canonical origin. `PROFILE.search(...)` has the same call shape as the catalog
  readers' `search`, sends no request and returns no candidate. Its result is an `error` with
  one non-retryable `SOURCE_DISABLED` error: "amhaz_official_candidate: business identity is
  unresolved; no retrieval is configured". The profile is not in `ADAPTERS`, `CATALOGS`,
  `config/sources.json` or `config/shared-extraction-profiles.json`, so no run can dispatch to
  the site.
- **Directory.** The candidate stays an unconfigured lead with `seller_identity` "unresolved".
  "Amhaz", "Amhaz Official", "Amhaz Mobile" and similar names resolve to no directory record.
- **Capabilities and categories.** None supported. Category leads the audit observed in the
  candidate's catalog (`catalog_observed`): phone, tablet, audio. Laptop and monitor: not
  established.

## Live check, 17 November 2026

Three read-only HTTPS GETs of the audit's recorded candidate entry points, made outside the app
runtime with Python `urllib.request` and the User-Agent `TechScout-research-check/1`: 20 s
timeout, 4 MB cap, at least 12 s between requests, no retries, no login, cart, form or message.
They are evidence for the blocker, not extraction for a reader.

| URL | Checked (UTC) | Status | Bytes | SHA-256 of the reply |
|---|---|---|---|---|
| https://amhazofficial.com/ | 15:44:07 | 200 | 84649 | `1cf33f9feb46a2d51ee0e4683cfc53c139d3e36df54b484f9d1807877315e009` |
| https://amhazofficial.com/contact | 15:44:24 | 200 | 25549 | `b1b347788d58b10a22a1c4521d3fa387ea5ad45961289b194f6a11b7ffa1df6d` |
| https://amhazofficial.com/product/1018 | 15:44:36 | 200 | 44212 | `ed9cea5f8c2e166ba425d15a416624add2d688dc7a863fee52a0c379b9603d0c` |

What the three pages show:

- **Identity.** The brand is "Amhaz Official" and the address "Saint Therese, Hadath , Lebanon".
  Gallery captions also read "Amhaz Mobile Store front" and "Wholesale and maintenance". Nothing
  links the site to another Amhaz business or names a legal entity, so identity stays unresolved.
- **Policies.** The footer's "FAQs", "Shipping" and "About Us" are plain text with no link; only
  "Contact Us" links (to `/contact`). None of the three replies contains the words "warranty",
  "guarantee", "return", "refund", "VAT", "delivery" or "payment"; "shipping" appears only as
  that unlinked footer label. No delivery, warranty, returns, VAT or payment policy was found.
- **Sample product `/product/1018`.** The page shows "IPHONE 17", a 256GB storage option,
  "$2,865.00", "Out of stock" and "Availability: Check for availability". Its embedded Next.js
  data gives `price` 2865, `currency` USD, `qty_available` 0, no warehouse, an empty
  description and specs `{"Size": "256GB"}`: no condition is stated for it. The price is far
  above the 14 November listings of this model at Class Cellulars and Beytech (USD 1,030 and
  1,079) and is not explained. It is recorded, not interpreted.
- **Related items** on the same page carry an explicit `Condition` spec ("New" or "Used"). The
  card for `/product/909`, "IPHONE 11 128GB USED", shows "Ask for price"; its data says price 0.

The site is reachable, but even apart from identity it offers no qualifying listing today: the
sample is out of stock, its price is unexplained, its condition is not stated and no policy was
found. The minimized capture is `tests/fixtures/stores/amhaz/candidate-2026-11-17.json` (contact
numbers and email left out).

## What unblocks it

1. A teammate records, with evidence, which Amhaz business the user meant (name, domain and
   location) in the audit sidecar and seed, and on #56. If it is this candidate, update its
   identity there. If it is another business, add a separate record; never merge the names.
2. Review narrow endpoint capabilities for the confirmed domain in `config/sources.json`.
3. Replace the blocked profile with a reader. For this candidate the reader must:
   - read condition only from the item's own explicit `Condition` spec, never from a name token
     such as "USED", from sibling items or from silence (`1018` states none, so it stays unknown);
   - leave price 0 or "Ask for price" unknown, and parse prices as Decimal;
   - treat `qty_available` 0 or "Out of stock" as not in stock; "Check for availability" is not
     stock evidence, and online quantity is not branch stock;
   - leave warranty, delivery fee, VAT and arrival time unknown, never zero, while no policy
     states them;
   - treat the embedded Next.js data as page-internal, not a documented API, and report a
     changed shape as `INVALID_SCHEMA`.
4. `tests/test_amhaz_profile.py` fails as soon as a dated retailer audit, the seed or the registry
   resolves, aliases or admits Amhaz, so the blocked profile cannot silently drift from the records.
