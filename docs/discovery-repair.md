# Buyer request and listing discovery

The v2 buyer write API accepts optional `buyer_text` from the form when its wording changes. The API interprets it before saving the revision. Structured callers and stored records retain their existing meaning and defaults. A model request may carry its original `wording`. Exact RAM and storage, minimum specifications, laptop storage type, allowed colors, budget and fulfillment feed the existing rule checks. Additional unresolved clauses prevent a verified recommendation. Needs descriptions remain available to the suitability selector.

The retailer search term controls discovery only. The worker checks candidate and finalist pages with the full request. A shorter shop search never relaxes the eligibility rules. A shop directory entry establishes no listing, price or stock.

## Technodel technical review for #194

The existing public GET operations cover a 64-character search, a single product slug, and the FAQ. The category review used the dated page captures for observations A18 (laptop), F3 (tablet), T01 (monitor), T13 (phone) and the earlier headset review. Each page exposes a selected purchase component, a Product offer, and the merchant’s ElectronicsStore record. A fresh bounded search for `24GB` returned 24 category-tagged cards and corresponding product-page anchors. These schema observations inform the reader. No reviewed product URL is supplied to buyer runs. [Dated source observations](../acceptance/buyer-v1/evidence-v1-2026-11-25.json)

The expanded reader requires matching category, seller, URL, SKU, price, currency, current stock and single selection across the purchase component and Product data. It reads named specifications from the selected product. Missing fields remain unknown. The previous checkout/tax review was scoped to wired headsets. Other devices retain an unknown tax/mandatory-charge state even when the listing and current FAQ agree on shipping. The registry retains its six requests per run and minute and one concurrent read.

