# Store fixture provenance

These fixtures are **minimized factual extracts**, not the original responses. Each was cut down
from one public reply fetched through the source gate on 2026-11-14 (an allow-listed GET with the
per-store limits), keeping only what the readers use: record IDs, names, prices, stock and
purchasability flags, attributes and terms, tags and categories, the price line, the delivery,
warranty, banner and footer lines, the variation form with each entry reduced to its ID, prices,
stock, purchasability and visibility flags, SKU, availability line and attributes, and the
relevant policy sentences inside minimal markup. The form entries' `backorders_allowed` and the
Store API records' `is_on_backorder` were not kept, so these captures cannot show whether a store
uses silent backorders; the backorder tests edit the fixtures synthetically. Product
descriptions, images, galleries, scripts, reviews and navigation were removed. Kept sentences are
verbatim.

Hashes and lengths below are of the **original** decoded response bodies, as the gate recorded
them; the fixtures themselves are smaller and hash differently.

| Fixture | Source URL | Observed (UTC) | Original response SHA-256 | Original bytes |
|---|---|---|---|---|
| `class_cellulars/api.json` | https://classcellulars.com/wp-json/wc/store/v1/products?slug=iphone-17 | 2026-11-14T19:03:47Z | `d8fc6ce3f05c7369b80bbc512c4bf0e6999ff14b660a4d624b1645c71b417fd3` | 21464 |
| `class_cellulars/faq.html` | https://classcellulars.com/faq/ | 2026-11-14T19:03:51Z | `7d2a80f1844de6bdf753b05d19304ef62b2d758fd61f9d6af34b2dbdb07e0c65` | 186821 |
| `class_cellulars/page.html` | https://classcellulars.com/product/iphone-17/ | 2026-11-14T19:03:49Z | `288db034c8eef20d2a4a54b85a143cfde69375998914bf002822695aa4ff87b8` | 248129 |
| `mojitech/api.json` | https://mojitech.net/wp-json/wc/store/v1/products?slug=apple-iphone-17-256gb | 2026-11-14T19:03:53Z | `d17d213f9f5829dbd9519e94b04d38a87e7ea793e38b4cfc2e8edf2497ae4054` | 10449 |
| `mojitech/page.html` | https://mojitech.net/shop/brands/apple-brands/apple-iphone-17-256gb/ | 2026-11-14T19:03:59Z | `046c28d1d038245a335bcacc1aaf03b0f7e4d033e85d1cec6269bb0e0a12d17a` | 381350 |
| `mojitech/refund_returns.html` | https://mojitech.net/refund_returns/ | 2026-11-14T19:04:03Z | `6a888fdef9c16aab44811020a781be0640b55f352ba599f8b17738204dc9740d` | 197073 |
| `beytech/api.json` | https://beytech.com.lb/wp-json/wc/store/v1/products?slug=apple-iphone-17-1-sim-2-years-warranty | 2026-11-14T19:04:04Z | `cd83e32d1581153c3c22f8145fd8b4773d45869346fa365c14273a69e649fac9` | 29640 |
| `beytech/exchange.html` | https://beytech.com.lb/exchange-policy-2/ | 2026-11-14T19:04:11Z | `ac84af4655633f0830cf054834755e727a81be896735bdcfceb5726b4623a298` | 396302 |
| `beytech/page.html` | https://beytech.com.lb/product/apple-iphone-17-1-sim-2-years-warranty/ | 2026-11-14T19:04:07Z | `a22615751216cb70e9f1b526797a0f4f2be9b9dd97d19d7f42c136957f9605f6` | 623864 |
| `beytech/warranty.html` | https://beytech.com.lb/warranty-terms-conditions/ | 2026-11-14T19:04:09Z | `15c950cd5d3c87967d1e65a635803501334b769cb538de1dbccce689dc482f1f` | 385899 |

## Adversarial fixtures (`adversarial/`)

Synthetic variants of the minimized fixtures above, each with one deliberate change:

| Fixture | Built from | Change |
|---|---|---|
| `injected_name.api.json` | `class_cellulars/api.json` | Product name holds an injected instruction and a URL |
| `missing_prices.api.json` | `class_cellulars/api.json` | `prices` removed from every record |
| `minor_unit_string.api.json` | `class_cellulars/api.json` | `currency_minor_unit` written as a string |
| `ajax_only.page.html` | `class_cellulars/page.html` | `data-product_variations="false"` (variations load separately) |
| `price_mismatch.page.html` | `class_cellulars/page.html` | Variation 19116 shows 1100 while the API record says 103000 minor units |
| `duplicate_variation.page.html` | `class_cellulars/page.html` | Variation 18398 repeated identically; 20648 repeated at 1050 |
| `too_many_decimals.page.html` | `mojitech/page.html` | Variation 91707 at 1100.55 and 91708 at 1100.5 (currency minor unit 1) |

## Amhaz candidate capture (`amhaz/`)

`amhaz/candidate-2026-11-17.json` is a minimized factual extract of three read-only GETs made
on 2026-11-17 outside the app runtime, recorded in [source coverage](../../../docs/source-coverage.md)
under Amhaz (#56). It keeps each reply's URL, time, status, byte count and SHA-256, the contact
page's brand name, address and gallery captions, the footer's link targets, the sample product's
visible price and availability lines, and the product records from the page's embedded Next.js
data (images, descriptions and units removed). Contact numbers and email were left out. The
identity is unresolved (#50), so this capture is evidence for the blocker, not purchase evidence;
no reader consumes it.
