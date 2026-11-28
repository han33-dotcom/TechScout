# Buyer product contract v2

Adopted 28 November 2026; replaces v1 of 25 November. A buyer should be able to choose a device, understand why it suits them, compare what Lebanese sellers actually offer, and open the exact listing to buy. This contract governs product behavior and evaluation. The [scout agent](scout-agent.md) is the implementation measured against it.

**What changed from v1.**
- **Prices are compared before VAT and delivery.** Mandatory charges and delivery fees are hard to obtain from Lebanese shops, and requiring them prevented useful answers. They are out of scope.
- **Unstated condition is a caveat, not a failure.** If the buyer asks for new and the shop doesn't say, the recommendation is still given, with the gap stated.
- **The address, delivery-quote and branch-stock flows are out of scope.**
- **Contract v1** is kept at the [`baseline/pre-scout` tag](https://github.com/han33-dotcom/TechScout/blob/baseline/pre-scout/docs/buyer-product-contract.md).

## Advise the buyer across turns

Start with what the buyer knows about their needs, a model, or a retailer link. For "a big, powerful laptop", find out what they want to do, what they want to spend and whether they already have specifications in mind. Ask a follow-up when plausible answers would change the advice. Once there is enough information, show options and explain the specifications in terms of the buyer's work. Someone who edits 4K video can compare suitable machines without first choosing a processor.

Group sellers under each exact configuration. For a needs request, show up to three suitable models with reasons and tradeoffs. Carry hard constraints, preferences and earlier feedback through the chat. When the buyer adds that they commute daily, reconsider weight and explain how the choice changes.

## Compare the item the buyer meant

Match the requested model and variant, including relevant suffix, memory, storage, colour and connectivity. For "is this a good deal?" with a product link, establish what the linked seller offers, then look for the same configuration at other Lebanese sellers. Show the exact links and disclose the scope if only one or no offer can be found. If an exact configuration is unavailable or doesn't exist, say so, label any near match, explain the difference and preserve the buyer's priorities.

Treat "up to" and "under" as hard ceilings and "around" as a preference. Rank on listed price only for the same variant, and identify ties. For an empty result, explain the constraint and the sellers checked.

## Condition

Default to new. Used, refurbished or open-box offers qualify only when the buyer opts in, and are labelled with their condition. If the buyer wants new and the shop states it, show that evidence. If the shop does not state condition, still recommend the offer and say so plainly, for example "I couldn't verify these are new; the shop doesn't say". Never call an offer new without evidence from this run.

## Who sells it and what it costs

Identify the actual merchant as a Lebanese seller. On marketplaces, distinguish the merchant from the platform. Show the current listed price and currency. Prices are before VAT and delivery, and the answer says so. Never present an unknown amount as zero or invent a price. The buyer completes purchases on the retailer's site. Checkout, payment, reservations, retailer account creation and seller messaging are outside TechScout.

## Verify before recommending

Read the recommended offer's exact variant, price and stock in the current run, and bind each claim to the page evidence it came from. Out-of-stock exact matches may be shown, labelled as such. Label missing, stale or conflicting facts. Display preliminary and uncertain offers with their status and working product links.
