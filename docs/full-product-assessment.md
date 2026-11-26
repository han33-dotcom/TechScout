# Full-product eligibility and domestic costs

`assess_full_product(request, offer, now=..., fx=..., estimates=...)` returns
`FullProductAssessment`, reusing v1 checks/gaps and exact monetary helpers while
accepting the published v2 contracts. Existing v1 entry points remain unchanged.

Eligibility checks category, exact model/manufacturer, each explicitly requested
category attribute, colors, payment, allowed condition and market, route eligibility,
pickup branch/destination, warranty and deadline. Optional specifications do not
become requirements merely because a category supports them. Unknown and conflicting
required facts remain unresolved. Model punctuation/suffixes are preserved.
An estimated arrival cannot satisfy a guaranteed deadline. Pickup stock still
requires a receipt bound to the selected variant and branch.

`NewOnlyPolicy` represents an explicitly extracted, dated source policy limited to
a seller and categories, with product exclusions. `apply_new_only_policy` can fill
an unknown condition only while it applies. It does not override a known condition
or conflict. The derived variant receipt retains the source URL, hash, observation
time, pointer/excerpt and corpus provenance. A store name or reputation is never
enough; source/category adapters must establish the policy and its exceptions.

Domestic totals add each applicable additional charge exactly once, leaving
included tax/fees within the listed price. Tax treatment is required; delivery
also requires shipping treatment. Listed unknown charges, expired quotes,
unrecognized conditions and cross-currency fee amounts remain incomplete. The
supported condition grammar is the existing `min_subtotal:CURRENCY:amount`,
`city:name`, and `payment:cash|card`; unknown payment/city does not prove a waiver.
Pickup has no implicit delivery/travel surcharge.

Costs retain the v2 `verified`, `estimated`, and `incomplete` states. An estimated
range is available only when the caller supplies an explicit same-currency range
with assumptions for **every** missing fee path. It cannot produce a verified
recommendation. Decimal sums are exact within the existing bounded precision;
unrepresentable sums remain incomplete. FX uses the least favorable fresh quote
and rounds upward at bounded precision. A target budget is a preference; only an
explicit maximum rejects an otherwise eligible offer.

This module evaluates evidence already held. `recommendable` is an eligibility/cost
decision, **not a live verification receipt**. Publication still requires
`VerifiedOffer` and `RunSnapshot` with current-run price/stock evidence.

For product links, resolve the selected variant before comparing another seller. Suitability claims require evidence. The domestic worker checks the actual merchant and service to the destination under the [buyer contract](buyer-product-contract.md). The older [international cost engine](landed-costs.md) remains available for legacy routes. `shortlist` ranks recommendable offers and keeps incomplete or estimated totals among the uncertain alternatives.
