# Buyer product contract v1

Adopted 25 November 2026 for the domestic buyer experience. A buyer should be able to choose a device, understand why it suits them, compare what Lebanese sellers actually offer, and open the exact listing to buy. This contract governs product behavior and acceptance. The [18 development scenarios and implementation review](buyer-acceptance-matrix.md) track the work against it. The existing [v2 API and stored-record contracts](full-product-contracts.md) have their own versions.

## Advise the buyer across turns

Start with what the buyer knows about their needs, a model, or a retailer link. For “a big, powerful laptop”, find out what they want to do, what they want to spend and whether they already have specifications in mind. Ask a follow-up when plausible answers would change the advice. Once there is enough information, show options and explain the specifications in terms of the buyer’s work. Someone who edits 4K video can compare suitable machines without first choosing a processor.

Group sellers under each exact configuration. For a needs request, show up to three suitable models with reasons and tradeoffs. Carry hard constraints, preferences, destination and earlier feedback through the chat. When the buyer adds that they commute daily, reconsider weight and explain how the choice changes. For comparable offers, identify the cheapest verified total, explain what a documented warranty premium buys and ask about that tradeoff when it could change the recommendation.

## Compare the item the buyer meant

Match the requested model and variant, including relevant suffix, memory and storage. For “is this a good deal?” with a product link, establish what the linked seller offers, then look for the same configuration at other Lebanese sellers. Show the exact links, explain differences in terms, and disclose the scope if only one or no offer can be verified. If an exact configuration is unavailable, label a near match, explain the difference and offer to search other models while preserving the buyer’s priorities.

Treat “up to” as a hard ceiling and “around” as a preference. Default to new condition. Used or refurbished items require explicit opt-in to qualify. Show a promising incomplete offer with the current listed price, exact link, specific missing fact and its effect on the decision. A buyer comparing a $745 delivered offer against a $730 listing with unknown shipping can see that the latter may exceed a $750 ceiling. Rank on price only for comparable, verified totals. Identify ties. For an empty result, explain the constraint and the sellers checked.

## Establish who sells it and what it will cost

Identify the actual merchant as based in Lebanon and establish service to the buyer’s destination or pickup branch. On marketplaces, distinguish the merchant from the platform and fulfiller. Warehouse location and sourcing have no bearing on domestic eligibility or the amount charged. Unknown seller identity, service or mandatory fees remain visible gaps.

Use the retailer’s applicable published terms or a retailer-issued pre-order quote for the selected item, quantity and destination. Show the listed price, included and additional mandatory charges, and total where supported. Count each charge once. When a fee appears during checkout, TechScout may use a reviewed quote operation that stops before an order, payment or reservation. Respect source scope, workload and spend limits. If the retailer supplies no usable quote, keep the fee unknown. The buyer completes purchases on the retailer’s site. Retailer account creation and seller messaging are outside the buyer flow.

## Obtain a precise address when it matters

Show offers and available city-level quotes promptly. If a street address could change the comparison, name all sellers currently waiting for it, explain why, and ask once. Let the buyer choose which of those sellers may receive the selected address for this search. Reuse that answer for the pending quotes. A newly introduced seller needs a separate choice. If the buyer declines, continue with the known offers, exact retailer links and explicit unknowns.

Buyers can create a **TechScout** account, sign in and manage multiple private saved locations. Let a signed-in buyer select a location for the named sellers. If they need to add one, take them from the chat to Locations and back to the same chat after selection. Retain the comparison and earlier turns, automatically resume pending quotes, and show the updated totals and recommendation. Permit reuse in a later signed-in search. Saving an address and selecting its recipients are distinct actions.

## Verify before recommending

Recheck the finalist’s exact variant, price and stock in the current run. Bind condition, charges and fulfillment evidence to the offer. For pickup, verify availability at the selected branch and recheck after a branch change. If only online stock is known, say so and give the branch’s published number when available. The buyer handles any reservation. An estimated arrival cannot satisfy a guaranteed deadline. Recalculate when a price changes and label missing, stale or conflicting facts. Display preliminary and uncertain offers with their status and working product links.

The [acceptance matrix](buyer-acceptance-matrix.md) specifies buyer-visible evidence for each scenario. The [independent correctness protocol](buyer-correctness-acceptance.md) governs the separate live checkpoints and frozen cohort. The [earlier rules audit](buyer-product-rules-audit.md) records the evidence behind the changes.
