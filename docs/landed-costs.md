# International landed costs

For [#63](https://github.com/han33-dotcom/TechScout/issues/63). `app/landed_costs.py` works out what
an overseas offer costs once it reaches the buyer in Lebanon. It covers direct delivery (Microless
UAE, B&H US) and forwarding (Amazon US through MyUS or Stackry). Like the domestic engine in
`app/costs.py`, it uses fixed rules and exact `Decimal` arithmetic and makes no network or model
calls. It returns the shared v2 cost union (`VerifiedCost`, `EstimatedCost` or `IncompleteCost`), so
an offer's verified status means the same thing at home and abroad.

This is the fixture-first slice the issue allows. Live route data still depends on
[#61](https://github.com/han33-dotcom/TechScout/issues/61) (direct offers),
[#62](https://github.com/han33-dotcom/TechScout/issues/62) (forwarding quotes) and the open checks of
[#60](https://github.com/han33-dotcom/TechScout/issues/60). The research for #60 found no item-specific
Lebanon quote for any route, so live international costs will start as estimated or incomplete until
those tasks supply current quotes.

## Using it

~~~python
from app.assessment import assess_full_product, shortlist
from app.landed_costs import RouteInputs, calculate_landed_cost

landed = calculate_landed_cost(request, offer, RouteInputs(...), fx=rates, now=now,
                               run_started_at=run_started)
# landed.cost: the v2 cost; landed.components: every charge slot; landed.support: route verdict

assessment = assess_full_product(request, offer, fx=rates, now=now, route=RouteInputs(...),
                                 run_started_at=run_started)
ranked = shortlist([(offer, assessment), ...])  # primary, uncertain, rejected
~~~

`assess_full_product` sends every overseas offer (an overseas seller or origin, or a forwarded route)
to this engine. Domestic offers still go to `calculate_domestic_cost`, unchanged. Overseas offers used
to come back as incomplete with "international cost evaluation required". Now they come back with
each missing charge named, even when no route inputs are given yet.

## The input interface for #61 and #62

`RouteInputs` is the integration point. A producer fills three things.

| Field | What goes in it |
|---|---|
| `item` (`ItemFacts`) | The shipped item: lithium `battery` (`none`, `installed`, `packed_with`, `loose`), package `weight_g` and `dimensions` (mm). These are v2 `Fact`s, so each one is known with receipts, unknown with a reason, or conflicting. |
| `quotes` (`RouteQuote`) | One record per provider statement for this route: a retailer checkout, a forwarder calculator result, a customs estimate or a courier price. |
| `evidence` | The receipts for the item facts (field paths `package.battery`, `package.weight_g`, `package.dimensions`) and for each exchange rate (field path `fx.<BASE>.<QUOTE>`, for example `fx.USD.AED`). |

A `RouteQuote` has:
- `provider_id` and an optional `service` name (one carrier option of that provider).
- `route_id` (the offer's route) and one route-scoped `evidence` record. Its `observed_at` is the
  quote's time.
- `valid_until`, when the provider states it.
- `seller_id` with `source_variant_id`: the listing the quote was issued for. Retailer and order
  charges need them. When the provider is the offer's `retailer_id` and the receipt comes from the
  offer's own source for that listing, the quote is the retailer's own receipt, which speaks for the
  item itself.
- `package` (the weight, size and battery declaration the provider priced) and `battery_accepted`
  (whether the service takes that battery).
- `limits` (maximum weight, longest side, length + width + height).
- `declared_value` (what customs charges were computed on).
- `lines`: one `QuoteLine` per charge. A line's state is `additional` (with an amount), `included`,
  `not_applicable` (with a note), `unknown` (with a note) or `estimated` (a `lower`/`upper` range,
  with the assumption as the note).

Each line names one charge slot: a leg plus a component.

| Leg | Components | Required? |
|---|---|---|
| `retailer`: what the retailer charges to ship its order (to Lebanon if direct, to the hub if forwarded) | `shipping`, `handling`, `insurance`, `dangerous_goods`, `other` | `shipping` always |
| `forwarding`: hub to Lebanon | `shipping`, `consolidation`, `handling`, `storage`, `insurance`, `dangerous_goods`, `other` | `shipping` on a forwarded route; `dangerous_goods` there too, unless the item is evidenced to have no battery |
| `import` | `duty`, `import_vat`, `clearance` | all three |
| `final`: delivery inside Lebanon | `delivery` | yes |
| `order` | `sales_tax`, `payment_fee`, `fx_fee`, `other` | `sales_tax` |

These slots follow the quote components recorded for #60 in
[the international audit](international-source-audit.md): merchant shipping to the warehouse,
international freight, handling/consolidation/storage, battery charges, insurance,
duties/tax/brokerage, payment or currency fees, and final delivery.

A charge already listed on the offer (`Offer.costs`) is read as the retailer's statement:
- shipping goes to `retailer` or `forwarding`;
- duty and customs handling go to `import`;
- payment goes to `order.payment_fee`;
- the order's tax goes to `order.sales_tax`;
- any other kind becomes that leg's `other`.

This way nothing listed is dropped. If two listed charges land in one slot (for example payment on
the order and on the retailer leg), they are separate charges, so that slot stays open.

## Rules

- **No double counting.** Lines in the same slot that agree count once, however many providers list
  them. "Agree" means the same amount and currency; included, not applicable and an additional zero
  all count as nothing extra. If a retailer prepays duty and the customs estimate states the same
  duty, the duty is added once.
  - Lines that disagree leave the slot open: different amounts, or "included" against "additional".
  - Several separately listed `other` charges are never merged, as in the domestic engine, and two
    listed charges of the offer that land in one slot leave it open.
  - A current line outranks an estimate.
  - A listing that does not publish a charge gives way to any current line.
  - A current quote that states a charge as `unknown` (charged, amount not given) gives way only to a
    stated amount. Another provider's included or not applicable never turns it into zero.
- **Expiry.** A quote is current until its `valid_until`; at that instant it has ended. A quote
  with no stated validity counts only if it was read in this run (observed at or after
  `run_started_at`), following the full-product plan's freshness rule. Without `run_started_at`, an
  undated quote can only supply estimates.
- **Scope.**
  - A quote that names a listing must name the offer's own seller and listing variant. A quote
    that names none cannot state retailer or order charges.
  - A quote's receipt from the offer's own source must belong to the offer's listing.
  - A quote from the retailer that names the listing must be backed by the retailer's receipt for it.
  - Forwarding lines must come from the route's forwarder.
  - On a forwarded route the retailer ships only to the forwarding address. Quotes from the retailer
    or the seller, and the offer's listed charges, settle only the `retailer` and `order` legs, never
    freight, import charges or final delivery in Lebanon.
  - Customs lines from any quote other than the retailer's own receipt need a declared value of at
    least the device price, in the price currency. Naming the listing does not exempt a quote.
- **The actual item.**
  - A quote that carries the item (forwarding or final delivery) must state the package it priced.
  - The item's evidenced weight must not exceed the quoted weight, and its size must fit the quoted
    size whenever the quote states a size.
  - The declared battery must match the item's battery. Any quote other than the retailer's own
    receipt must confirm that the service accepts that battery.
  - Missing weight, size or battery leaves the charge open, never zero. Size is needed only when the
    quote prices by size or has a size limit.
- **Unsupported routes.**
  - A provider that refuses the item (its battery is not accepted, or the item is over a weight or
    size limit) cannot price it.
  - If that refusal comes from a current quote for this seller and route, and no other current quote
    that covers the item addresses that leg's carriage, the route is `unsupported` and
    `assess_full_product` rejects the offer. Another service that only estimates its price, disagrees
    with a third quote or states the charge without an amount still counts: the offer stays uncertain.
  - The offer's listed charges never count as another carrier, and cannot settle carriage on a leg a
    provider refused.
  - A refusal from an expired, undated or mismatched quote is only a doubt: the offer stays
    uncertain.
  - Quotes from one service (same `provider_id` and `service`) that both refuse and accept the item
    contradict each other. Neither settles anything, and the route stays unresolved.
  - A route eligibility fact that says no (`item_eligible` false) also makes the route unsupported.
- **Exchange rates.**
  - Only sourced rates convert: an `FxRate` whose evidence record has the field path for its
    currency pair (`fx.USD.AED`) and the same observation time, observed at most `FX_MAX_AGE`
    (24 hours) before now and never after it. A receipt that two different rates cite backs neither.
  - As in the domestic budget check, the rate must be quoted in the needed direction. The least
    favourable fresh rate is used, and rounding is upward, so conversion never lowers a total.
  - A charge in another currency with no such rate stays open.
- **Outcome.**
  - `verified`: every slot settled from current quotes. The total is in the price currency. It is
    also converted into the budget currency (the maximum's, or the target's when there is no
    maximum) when that differs. A hard maximum in another currency needs a sourced conversion,
    otherwise the cost is incomplete; a target alone is a preference, so the total stays verified.
  - `estimated`: some slot rests on an explicit estimate; the range and its assumptions are shown.
  - `incomplete`: anything else. It shows the known subtotal and names every missing charge, worded
    so the writer and history views can print it.
- **Card fees excluded.** The total excludes any fee the buyer's own card issuer adds for paying in a
  foreign currency, just as the domestic total excludes travel. The basis line says so. A currency or
  payment fee stated by a provider is an ordinary slot.

## Ranking without false savings

`FullProductAssessment` has a new optional `route_support` field (`supported`, `unsupported`,
`unresolved`, or absent for domestic offers). An offer is `recommendable` only when eligibility
passes, the cost is verified and within any hard maximum, and the route is supported.

`shortlist` splits assessed offers into three groups:
- `primary`: recommendable offers, cheapest first, by verified total in one ranking currency. Each
  total is compared in the budget currency when it was converted, otherwise as payable. The
  ranking currency is the `currency` argument, or else the one most recommendable offers share.
- `uncertain`: unresolved offers, as v2 `UncertainAlternative`s with fixed-wording reasons and their
  cost. A verified offer whose total is in another currency also lands here, with a reason, rather
  than being ranked against amounts it cannot be compared with.
- `rejected`: offer IDs.

An estimate or known subtotal that is lower than a verified domestic total still stays in
`uncertain`. It never appears as a cheaper delivered purchase.

## Fixtures and tests

[`tests/fixtures/landed/routes.json`](../tests/fixtures/landed/routes.json) holds two route cases
built on the v2 offer fixtures. **Every provider, price, fee, rate, limit and date in it is
synthetic**; none is an observed fee.
- **Direct:** UAE seller, AED price, USD customs and courier charges converted at a sourced rate.
- **Forwarded:** US seller, forwarder with an installed-battery fee, customs, and final delivery
  included.

`tests/test_landed_costs.py` covers:
- both totals;
- a fee listed by two providers, and two listed charges landing in one slot;
- listed charges against quotes, and a retailer refusal that its own listing cannot override;
- quotes that name the listing but are not the retailer's own receipt;
- expired and undated quotes;
- battery refusal (with another service priced, estimated or contradicting it), battery declaration
  and an unknown battery;
- missing dimensions and weight;
- quotes that do not cover the item, seller or declared value;
- weight and size limits;
- exchange rates without evidence, for another currency pair, sharing a receipt with another value,
  with the wrong time, stale or from the future;
- item-fact receipts;
- route eligibility;
- unknown required costs;
- the primary ranking split, its order and mixed currencies;
- input validation.

Every test runs offline.

## Limits and integration points

- **Publishing an international `VerifiedOffer`.** The v2 `Offer` contract keeps one charge per kind
  and leg, and accepts evidence only from the offer's own source. A forwarder's or customs
  estimator's receipt cannot be attached to the offer today. Publishing an international verified
  offer (#76) therefore needs a deliberate contract revision, for example route-scoped third-party
  evidence plus a projection of these slots onto `Offer.costs`. This change does not alter the wire
  contract.
- **Publication recheck.** Recomputing an international total at publication time belongs with that
  change too; the domestic recheck in `app/buyer_runs.py` covers domestic offers only.
- **Percentage fees.** Fees stated as a percentage (for example a dangerous-goods fee that is a share
  of goods value) must reach the engine as computed amounts.
- **CIF valuation.** Duty and VAT are taken as quoted. The engine checks that the declared value
  covers the device price, but it does not rebuild a CIF valuation.
- **Conditional listing charges.** A charge listed with a condition stays open. The domestic
  condition grammar is not applied abroad.
- **Item-fact receipts.** The engine checks that each receipt is bound to the right field and, for
  the offer's own source, to the right listing. Matching a manufacturer's product to the listing is
  the producer's job, as in the identity module.
- **One slot per fee kind.** Two different fees of one kind from two providers (for example a
  broker's and a courier's clearance fee) share one slot. Equal amounts count once and different
  amounts leave it open. A producer that knows two such fees apply should state their sum, or one of
  them as an `other` charge.
- **Exchange-rate receipts.** A receipt is bound to its currency pair and time, but its text is not
  parsed for the rate value. Producers must record the observed rate exactly.
- **Listed unknown charges.** An offer can be recommendable here although its own `Offer.costs`
  still lists an unpublished charge that a quote later settled. `VerifiedOffer` rejects any unknown
  listed charge, so publication must replace the listed charges with the settled slots (the contract
  revision above).
- **Explicit domestic estimates.** `assess_full_product(estimates=...)` applies to domestic offers
  only. International estimates arrive as estimated quote lines.
- **Assumption length.** The writer shows an estimate's assumption only when it fits its 80-character
  limit (label plus note), and withholds longer ones.
