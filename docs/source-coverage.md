# Source coverage (MVP 06)

Observed **2026-11-14**, one read of each allow-listed URL through the source gate. The store
readers are in `app/adapters/` (MVP 05); minimized copies of the replies are in
`tests/fixtures/stores/` with their provenance. This is a snapshot, and stores change. A Store
API reply or variation form that no longer has the expected shape is reported as `INVALID_SCHEMA`,
and so is a policy page in which the reader recognizes no statement (the read is `partial`). A
product-page or product-name statement whose wording changes is simply not read, so its field
stays unknown with a fixed reason (usually "... is not published"; a product name that no longer
names a known model gives "product name does not name a known model") and the read is still `ok`; a Store API reply
without a currency code likewise leaves prices unknown ("currency is not declared").

All three admitted stores run WooCommerce. One product read is two requests (Store API slug
query, then the product page); each policy page is one more. The per-store cap is 4 requests
per run, so a search plus every policy page fits (Class 3, Mojitech 3, Beytech 4). A run fetches
a policy page only for an open gap the page declares it may settle (see Policy pages), so it never
fetches Beytech's two pages.

## Products and variants

| | Class Cellulars | Mojitech | Beytech |
|---|---|---|---|
| Product (parent ID) | iPhone 17 (18392) | Apple iPhone 17 \| 256GB (91706) | Apple \| iPhone 17 – 1 Sim – 2 Years Warranty (139968) |
| Actual variants | 4 | 12 (5 colors × 3 ESIM options, not all combinations) | 4 |
| Colors, as written | Lavender, Blue, White, Black | lavender, bleu, sage, white, black | Black, Green, Lavender, Blue |
| Storage | 256 GB, option `storage-capacity` | 256 GB, from the product name | 256 GB, option `M Size` |
| Price | USD 1030.00 | 0 on every variant ("Call for Price") | USD 1079.00 |
| Purchasable / in stock | yes / yes | no / yes | yes / yes |
| SKU | one SKU shared by all four colors | none | one per variant |

Colors are kept in the store's words ("bleu"); identity (MVP 07) maps them to canonical names.

## Offer fields

Known means a value with evidence; unknown and conflict carry a fixed reason.

| Field | Class Cellulars | Mojitech | Beytech |
|---|---|---|---|
| `variant.manufacturer` | Apple (tag "Apple iphone") | Apple (name, categories) | Apple (Brand attribute, name, tag) |
| `variant.model` | iPhone 17 | iPhone 17 | iPhone 17 |
| `variant.storage_gb` | 256 | 256 | 256 |
| `variant.sim` | physical+esim | esim-only; unknown for the ABM option | unknown: "1 Sim" is not confirmed (see Mappings) |
| `variant.region` | unknown: not published | unknown | unknown |
| `price` | known | unknown: store lists a zero price | known |
| `condition` | unknown: not published | unknown | unknown |
| `payment_basis` | unknown on the product page; **cash** from the FAQ for delivery offers (pickup stays unknown: the FAQ answers for online orders) | unknown: accepts cash and card | unknown: accepts cash and card |
| `warranty.provider` / `months` | Genuine Warranty Center / 18 | ABM / 24 for the ABM option, else unknown | unknown / 24 |
| `warranty.territory` | unknown: not published | unknown | unknown |
| `arrival`, delivery | 14–30 calendar days (estimate); **conflict** with the FAQ | 0–3 working days (footer, estimate) | unknown: not published |
| `arrival`, pickup | unknown: pickup readiness not published | unknown | unknown |
| `pickup_stock` | one entry per configured branch in the requested city (only the named one, if any), each unknown: online stock does not show branch stock | none: no branch list | none: no branch list |
| `costs.tax` | included: "(Inc. VAT)" next to the price, and JSON-LD | unknown: not published | unknown: not published |
| `costs.delivery` | USD 0 when the subtotal is over USD 50 (site banner), written as `min_subtotal:USD:50.01` | unknown: not published | unknown: not published |

Beytech's JSON-LD names the brand "Electronics"; the reader does not use JSON-LD brands.

Shared reader rules behind these values:
- `purchasable` and `online_stock` come from the page's variation entry and, for the one variation
  the Store API returns, from its record too; a disagreement is a conflict.
- WooCommerce reports a variation on backorder as in stock. The page's `available-on-backorder`
  class or the API's `is_on_backorder` keeps `online_stock` unknown: "variation is on backorder".
  Backorders allowed with no in-stock line only suggests a backorder (a theme may hide the line), so
  it keeps stock unknown with "variation may be on backorder".
- A Store API variation record, variation-list entry or option name that repeats with a different
  value is `INVALID_SCHEMA`; identical repeats are one statement.
- A variation option named "Model" must name exactly a known model and agree with the product name,
  or the model is unknown or a conflict. No admitted store has such an option today.
- The banner's amount is read with thousands separators ("$1,500"); an amount that goes on ("$1,50")
  is not read, so the delivery charge stays unknown.

## Fulfillment

- **Class Cellulars**: delivers all over Lebanon. Product page: "Estimated delivery time 14-30
  days". FAQ: Beirut 1 working day, outside Beirut up to 3 working days. Only the city "Beirut"
  (in any case) gets the Beirut time; every other city, including Beirut districts such as
  Achrafieh or Verdun, gets the outside-Beirut time. Branches listed in the FAQ: Mar Elias, ABC
  Verdun, ABC Achrafieh, ABC Dbayeh, Tyre (Al Abbasiyeh), configured as `mar-elias`,
  `abc-verdun`, `abc-achrafieh` (Beirut), `abc-dbayeh` (Dbayeh) and `tyre` (Tyre). No branch stock.
- **Mojitech**: footer on every page: "Delivery all over Lebanon", "Same Day Up To 72 hours
  (Working Days)", read as 0–3 working days. No delivery fee, no branch list read.
- **Beytech**: header "Delivery all over lebanon"; no delivery time, fee or branch list.
- **Pickup**: only Class Cellulars has configured branches. A pickup offer lists the configured
  branches in the requested city (only the requested one, when the request names a branch), each
  with unknown stock. Mojitech and Beytech publish no pickup location, so their pickup offers list
  no branch and stay unresolved ("branch stock is not published"). A Class branch ID outside the
  five above, a Class branch in another city, or a city with none of them is unsupported and lists
  no branch either. No store publishes branch stock, so no pickup offer passes the stock check.

## Policy pages

| Store | Page | Statements read | What it settles |
|---|---|---|---|
| Class Cellulars | `faq` | delivery time per zone; cash on delivery only; returns within 3 days | delivery offers only: `payment_basis` (cash); `arrival` (conflicts with the product page) |
| Mojitech | `refund_returns` | footer delivery time and payment methods; returns within 5 days | delivery offers only: `arrival`, only when not already known |
| Beytech | `warranty` | covers repairs only; original invoice required | nothing (no length, provider or territory) |
| Beytech | `exchange` | one exchange within 5 days; mobiles listed as non-returnable | nothing |

"What it settles" is what the reader declares: `policy_topics` lists every path on any route,
and `policy_topics_for(mode)` leaves out the delivery-only paths for a pickup offer. Run control
asks a reader for `policy_topics_for(mode)` when it has one (otherwise `policy_topics`), so a
pickup run is never offered the Class Cellulars FAQ or the Mojitech refund page. A page in which none of these
statements is recognized gives `INVALID_SCHEMA` "policy page states nothing the reader recognizes".

## Mappings

SIM types:
- Class Cellulars: "Nano SIM / eSIM" → `physical+esim`; "eSIM" → `esim-only` (listed, unused).
- Mojitech: "ESIM ACT" and "ESIM ACT TECH+" → `esim-only`; "ABM 2YEARS WARRANTY" states no SIM
  type, so SIM stays unknown for those two variants. The store never explains the "ACT" token; it
  is not read as anything (in particular not as a condition such as "activated"), and condition
  stays unknown. Reading "ESIM" as eSIM only is itself our reading; what "ACT" means is to
  confirm with the store.
- Beytech: "1 Sim" in the product name. The store never writes "eSIM"; one physical SIM slot plus
  eSIM is a plausible reading but an unconfirmed assumption, so the SIM type stays unknown
  ("SIM type in the product name is not confirmed", citing the name) until the store confirms
  what "1 Sim" means. A SIM-specific request therefore leaves Beytech offers unresolved.

Warranty:
- Class Cellulars: "18 months warranty at Genuine Warranty Center." (product page).
- Mojitech: option "ABM 2YEARS WARRANTY" → ABM, 24 months, for that variant only. "TECH+" in
  "ESIM ACT TECH+" may name a local warranty provider; it is not read as one.
- Beytech: "2 Years Warranty" in the product name → 24 months; provider not named.

## Blockers

1. **Mojitech lists every variant at 0 and not purchasable**, so no Mojitech offer can be
   priced or recommended today.
2. **No store publishes the device condition.** Condition stays unknown everywhere; the team
   must decide how a "new" requirement treats it.
3. **No delivery fee from Mojitech or Beytech, and no VAT statement from either**: delivery
   totals there stay unresolved.
4. **Class Cellulars' delivery estimates conflict** (14–30 days on the product page, 1–3
   working days in the FAQ), so a delivery deadline cannot be proven.
5. Warranty territory, region and branch stock are never published.
6. **The allow-list covers only the iPhone 17 pages**, one product per store. Other models or
   storage sizes need new reviewed endpoints in `config/sources.json`.
7. **Pickup is unsupported at Mojitech and Beytech** (no branch published), and at any Class
   Cellulars branch or city outside its five configured branches.
8. **Beytech's SIM type is unconfirmed** ("1 Sim"), so no Beytech offer can pass a SIM-specific
   request until the store confirms it.

The MVP 06 acceptance criterion that the readers retrieve qualifying offers from all three stores
with useful variant overlap is **not met** with this capture, and no code change can meet it: it
depends on the stores' live data. Mojitech yields no qualifying offer (blocker 1). Class Cellulars
and Beytech each yield four priced, purchasable, in-stock offers, and three color variants of the
iPhone 17 256 GB appear at both (Black, Blue, Lavender), but Beytech's SIM type is unknown
(blocker 8), so the two stores' offers cannot be shown to be the same SIM variant. Even if
Mojitech were priced again, its "ESIM ACT" variants are eSIM-only and could match only another
eSIM-only offer; only its two ABM variants (Blue and White, SIM unknown) could pair with the
others, and only as an open identity match. A human decision is needed (a documented exception,
a different reviewed Mojitech product, or a later re-capture).
