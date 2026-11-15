# Source coverage (MVP 06)

Observed **2026-11-14**, one read of each allow-listed URL through the source gate. The store
readers are in `app/adapters/` (MVP 05); minimized copies of the replies are in
`tests/fixtures/stores/` with their provenance. This is a snapshot: stores change, and the
readers report changes as `INVALID_SCHEMA` instead of guessing.

All three admitted stores run WooCommerce. One product read is two requests (Store API slug
query, then the product page); each policy page is one more. The per-store cap is 4 requests
per run, so a search plus every policy page fits (Class 3, Mojitech 3, Beytech 4).

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
| `variant.sim` | physical+esim | esim-only; unknown for the ABM option | physical+esim |
| `variant.region` | unknown: not published | unknown | unknown |
| `price` | known | unknown: store lists a zero price | known |
| `condition` | unknown: not published | unknown | unknown |
| `payment_basis` | unknown on the product page; **cash** from the FAQ | unknown: accepts cash and card | unknown: accepts cash and card |
| `warranty.provider` / `months` | Genuine Warranty Center / 18 | ABM / 24 for the ABM option, else unknown | unknown / 24 |
| `warranty.territory` | unknown: not published | unknown | unknown |
| `arrival`, delivery | 14–30 calendar days (estimate); **conflict** with the FAQ | 0–3 working days (footer, estimate) | unknown: not published |
| `arrival`, pickup | unknown: pickup readiness not published | unknown | unknown |
| `pickup_stock` | unknown: online stock does not show branch stock | unknown | unknown |
| `costs.tax` | included: "(Inc. VAT)" next to the price, and JSON-LD | unknown: not published | unknown: not published |
| `costs.delivery` | USD 0 when the subtotal is at least USD 50 (site banner) | unknown: not published | unknown: not published |

Beytech's JSON-LD names the brand "Electronics"; the reader does not use JSON-LD brands.

## Fulfillment

- **Class Cellulars**: delivers all over Lebanon. Product page: "Estimated delivery time 14-30
  days". FAQ: Beirut 1 working day, outside Beirut up to 3 working days. Branches listed in the
  FAQ: Mar Elias, ABC Verdun, ABC Achrafieh, ABC Dbayeh, Tyre (Al Abbasiyeh). No branch stock.
- **Mojitech**: footer on every page: "Delivery all over Lebanon", "Same Day Up To 72 hours
  (Working Days)", read as 0–3 working days. No delivery fee, no branch list read.
- **Beytech**: header "Delivery all over lebanon"; no delivery time, fee or branch list.

## Policy pages

| Store | Page | Statements read | What it settles |
|---|---|---|---|
| Class Cellulars | `faq` | delivery time per zone; cash on delivery only; returns within 3 days | `payment_basis` (cash); `arrival` (conflicts with the product page) |
| Mojitech | `refund_returns` | footer delivery time and payment methods; returns within 5 days | `arrival`, only when not already known |
| Beytech | `warranty` | covers repairs only; original invoice required | nothing (no length, provider or territory) |
| Beytech | `exchange` | one exchange within 5 days; mobiles listed as non-returnable | nothing |

## Mappings

SIM types:
- Class Cellulars: "Nano SIM / eSIM" → `physical+esim`; "eSIM" → `esim-only` (listed, unused).
- Mojitech: "ESIM ACT" and "ESIM ACT TECH+" → `esim-only`; "ABM 2YEARS WARRANTY" states no SIM
  type, so SIM stays unknown for those two variants.
- Beytech: "1 Sim" in the product name → `physical+esim`, read as one physical SIM slot plus
  eSIM, as Apple sells the iPhone 17 in the region (a dual-physical model would say two SIMs).
  This is an assumption to confirm with the store.

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
