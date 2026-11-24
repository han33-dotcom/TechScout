# Why every recorded run abstained, 24 November 2026

For [#75](https://github.com/han33-dotcom/TechScout/issues/75) (open box: a verified
qualifying result where source facts support one) and the held-out study in
[#82](https://github.com/han33-dotcom/TechScout/issues/82). Offline analysis of the
recorded receipts, the rule code at `7254524`, the store fixtures and eight manual
page reads on 24 November. No TechScout run, paid service or held-out case was used.

## Summary

- **35 recorded runs, 35 abstentions.** Only 8 of them got as far as a seller's
  offer and its fact checks. The other 27 stopped before that: 16 never sent a store
  request, 6 read pages that yielded no usable product, and in 5 study runs none of the
  attempted pages or leads could be checked.
- **The recorded reason applies to those 8.** For the three sellers whose offers are
  identified (Class Cellulars, OutGeeked, WinCell), the item's condition is
  unpublished, delivery to the buyer's city has no item-bound evidence, and at least
  one mandatory charge is unknown. A fourth seller, Beytech, was checked in one C12
  run whose per-offer reasons were not kept.
- **Condition is the binding gap.** None of the five admitted sellers publishes
  the item's condition on the pages read, and no "new only" statement was found
  (Class Cellulars and OutGeeked were checked; OutGeeked also sells used devices).
  AGENTS.md makes new the default and the rules treat an unknown condition as
  unresolved, so no admitted seller can produce a verified result today.
- **Two extraction gaps block the domestic path even when a seller publishes more.**
  Purchasability is read only from a structured-data property that no observed real
  page uses, and WooCommerce variant data is not used for price, stock or
  purchasability.
- **The rules are achievable.** In `tests/test_domestic_decisive_facts.py`, a seller
  page that publishes every decisive fact verifies a USD 902 payable total through
  the normal domestic controller. Removing one fact gives an honest abstention that
  names it.

## What a verified domestic recommendation needs

The domestic controller (`app/domestic_buyer.py`) publishes an offer only when all of
these hold for the selected SKU. Each must come from a response read in the same run.

| Decisive fact | Enforced in | AGENTS.md basis |
| --- | --- | --- |
| Price, positive, in one currency | `current_problem`, `calculate_domestic_cost` | "Main recommendations require price and stock checks within the current run" |
| In stock | `current_problem`, `check_full_product` | Same |
| Purchasable | `current_problem`, `check_full_product`, `VerifiedOffer` | Not named; a reasonable part of the stock check |
| Condition known and allowed | `check_full_product`, `VerifiedOffer` | "New condition is the default and other conditions require explicit opt-in" |
| Dispatch from Lebanon, with a receipt | `current_problem`, `dispatch_receipts` | "dispatch from inside Lebanon" |
| Delivery of this item to the buyer's destination | `current_problem`, `check_full_product`, `VerifiedOffer` | "Bind evidence to the actual field, product/variant and branch where required" |
| Tax treatment and delivery fee known | `calculate_domestic_cost`, `current_problem` | "Unknown mandatory charges stay unknown, never zero" |
| Exact model and every requested specification | `check_full_product` | "Preserve explicit buyer constraints" |

The new test removes one fact at a time from an otherwise complete page. The buyer's
uncertain alternative then includes the reason below, word for word, alongside others
(for example the matching "required evidence is missing" line):

| Missing | Status | Reason shown on the uncertain alternative |
| --- | --- | --- |
| Nothing | complete, USD 902.00 verified | — |
| Condition | abstained | condition: required evidence is missing or conflicting |
| Purchasable | abstained | Purchase availability lacks a current response for this exact configuration. |
| Stock | abstained | Stock lacks a current response for this exact configuration. |
| Dispatch origin | abstained | Dispatch from Lebanon is unverified for this listing and configuration. |
| Delivery to Beirut (city changed to Tripoli, so the fee is not read either) | abstained | item delivery eligibility: required evidence is missing or conflicting |
| Delivery fee | abstained | costs.order.shipping |
| Tax treatment | abstained | costs.order.tax |
| Condition, dispatch, delivery area, fee and VAT stated only as store-wide prose, plus a plain add-to-cart button (structured condition, Purchasable, VAT and shipping data removed) | abstained | condition, purchasable, delivery eligibility, dispatch, tax and shipping all unresolved; price and stock still known |

## Where each recorded run stopped

| Series | Date (UTC) | Request | Runs | Stage reached | Source |
| --- | --- | --- | ---: | --- | --- |
| Live browser checkpoint and repeat | 23 Nov | iPhone 17 256 GB, Beirut, Class Cellulars only | 2 | **B**: 3 finalists rechecked live each run | [#75 comment](https://github.com/han33-dotcom/TechScout/issues/75#issuecomment-7113100350), PR #161 |
| Soundcore demo | 23 Nov | Soundcore H30i, Beirut | 1 | **B**: OutGeeked and WinCell, both in stock | [demo](domestic-buyer-demo.md), PR #163 |
| D-H30i broader (sequential and parallel), outage | 23 Nov | Soundcore H30i, Beirut | 3 | **B**: 1–2 sellers checked; per-offer reasons not retained | [calibration receipts](development-calibration-2026-11-23.json), PR #164 |
| C12 broader (sequential and parallel) | 23 Nov | Earbuds for travel (needs) | 2 | **B**: Beytech and OutGeeked checked in the sequential run, OutGeeked in the parallel run; per-offer reasons not retained | Same |
| C01, C02 (both arms), C03 broader, D-H30i priority | 23 Nov | Phones; Soundcore | 6 | **A2**: pages read, no usable SKU | Same |
| C03 priority, C05, C06, C07, C08, C10, C11, C13 (both arms), C12 priority | 23 Nov | All five categories | 16 | **A1**: zero store requests in the ledger | Same |
| C02 study: production, full-warm, single-agent, fixed control, full-cold | 24 Nov | Galaxy S25 256 GB, Beirut | 5 | **A2 or A3**: 9–11 sources found, 3–5 attempted, 0 checked; the receipts keep only counts | [planning receipts](domestic-search-planning-2026-11-24.json), PR #171 |

Stage A means no fact check was reached. **A1:** the ledger recorded no retrieval
attempt, and every source outcome reads "Listing read unavailable". **A2:** at least one
page was read, but none yielded an offer for the request ("No matching SKU on the
current listing", "outside the active source scope"). **A3:** search leads were found
but none could be read. The C02 study receipts keep only counts, which also include the
directory sample pages, so A2 and A3 cannot be told apart there. Stage B means the
reader built at least one offer for the request, and the fact checks then held it back.

### Stage B: the offers that reached the fact checks

The facts below come from the 23 November receipts and coverage manifest, the
24 November page reads (listed at the end) and the 14 November fixtures. The
calibration receipts keep only counts per run, so the D-H30i rows are inferred from
the same seller pages and the code. Classes: **(a)** the seller does not publish the fact; **(b)** the seller
publishes it but TechScout does not read or bind it; **(c)** the request choice
caused it.

| Seller, offer, runs | Price, stock | Purchasable | Condition | Dispatch from Lebanon | Delivery to Beirut | Delivery fee | Tax |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Class Cellulars, iPhone 17 256 GB, 3 finalists in each of 2 live runs | Known (USD 1,030 in the 14 Nov capture; the 23 Nov receipt records no prices) | Known (WooCommerce reader) | Unknown **(a)**: not on the product page or FAQ (14 Nov), nor on the homepage or About page (24 Nov) | Set by the reviewed reader | Unknown **(b, rule)**: FAQ "Yes, we deliver all over Lebanon" is store-wide | Unknown **(b, rule)**: product-page banner "Free Shipping On All Orders Over $50" is store-wide (`NOT_ROUTE_EVIDENCE`) | Included, "(Inc. VAT)" |
| OutGeeked, H30i black, demo and three D-H30i runs | Known (USD 20) | Unknown **(b)**: enabled "Add To Cart" for variant 47452782133506 is not read | Unknown **(a)**; the store also sells a "Used Devices" range | Known: "Ships from Beirut, Lebanon" | Unknown **(b, rule)**: the product's own FAQ data says "we ship to Beirut and everywhere in Lebanon" | Unknown **(a)**: "Taxes and shipping calculated at checkout" | Unknown **(a)**: same |
| WinCell, H30i black, demo and two D-H30i runs | Known (USD 40) | Unknown **(b)**: enabled "Add to cart" for variant 53481525805354 is not read | Unknown **(a)** | Known in the demo, from the reviewed collection page | Unknown **(b, rule)**: "Delivery across Lebanon" is store-wide | Unknown **(a)**: none published; `/policies/shipping-policy` returns 404 | Unknown **(a)** |

Grouped by cause, over the six runs with identified offers (two live, the demo and
three D-H30i, whose reasons are inferred):

| Blocking fact | Runs | Sellers | Class |
| --- | ---: | --- | --- |
| Condition unknown | 6 of 6 | Class Cellulars, OutGeeked, WinCell | (a) |
| Delivery to the destination not item-bound | 6 of 6 | All three | (b), needs a rule decision |
| Delivery fee unknown | 6 of 6 | Class Cellulars (b, rule); OutGeeked, WinCell (a) | Mixed |
| Tax unknown | 4 of 6 | OutGeeked, WinCell | (a) |
| Purchasable unknown | 4 of 6 | OutGeeked, WinCell | (b) |
| Price or stock | 0 | — | — |

By category: only phones (the two live Class Cellulars runs) and audio (the demo, three
D-H30i and two C12 runs) reached stage B. Laptops (6 runs), tablets (2) and monitors (2)
never sent a store request. The other phone runs (10 in calibration, 5 in the study)
stopped at stage A.

## Root causes

### Before any fact check: TechScout's own coverage and limits

1. **Own workload limits refuse reads before sending (16 runs).** The shared ledger
   refuses a source reservation when the shop already has its concurrent request in
   flight, six attempts in the previous 60 seconds across all jobs, or its per-run cap.
   This is the "Shared source workload limit" in `app/accounting.py` (lines 184–192),
   with the limits in `config/sources.json`. The gate's limiter also refuses any wait
   over 10 seconds (`app/sources.py`, `acquire`). Both refuse before a request is sent,
   which matches the zero retrieval attempts. The timing fits the six-per-minute limit:
   24 of the 27 requests started between 21:14 and 21:15 UTC on 23 November. Six runs
   between 21:14:44 and 21:15:08 read Beytech, Class Cellulars and Mojitech. From the
   seventh (C03 priority, 21:15:11), every read of those three shops in the burst was
   refused. The receipts cannot confirm which refusal fired: `app/domestic_buyer_sources.py` gives refused and
   failed reads the same reason, "Listing read unavailable".
2. **Unreadable WooCommerce pages, and one sample page per shop (A2).** Every "No
   matching SKU" in the receipts is at Class Cellulars or Beytech. The domestic reader
   builds no offer from their WooCommerce pages, whatever the request ((b)2 below), so
   these reads fail for any phone. On top of that, the directory's only product URL at
   Class Cellulars, Beytech and Mojitech is the audit sample: the iPhone 17 Pro, the
   iPhone 17 and a Lenovo IdeaPad respectively. `find_candidates` reads that page for
   any request in any category, so even with (b)2 fixed, a Galaxy S25 or H30i request
   would read an iPhone page and find nothing. Mojitech's sample URL is outside its
   active source scope.
3. **Search leads outside the reviewed sources.** The C02 study receipts keep only
   counts (9–11 sources found, 3–5 attempted, 0 checked), so its failures cannot be
   attributed. In the separate
   [discovery calibration](development-calibration.md#discovery-channels), 12 follow-up
   checks of SerpApi and Firecrawl leads found 7 URLs outside approved source scope and
   5 without a reader configuration; 12 Brave follow-ups found 9 and 3.

### (a) The seller does not publish the fact

- **Item condition** at all five admitted sellers: Class Cellulars, Beytech and
  Mojitech in the 14 November captures and the [source coverage](source-coverage.md)
  blockers; OutGeeked and WinCell in the 24 November reads. Class Cellulars' homepage
  and About page state no condition policy. OutGeeked sells used devices, so no
  store-wide "new only" statement could cover it. Beytech's and Mojitech's other
  pages were not searched for such a statement.
- **Delivery fee and tax** at OutGeeked (calculated at checkout, which TechScout never
  enters) and WinCell (not published). At Beytech neither is published.
- **Price** at Mojitech in the 14 November capture ("Call for Price"). The audit warns
  this may have changed.
- **SIM type** at Beytech ("1 Sim"), for SIM-specific requests such as C03.

### (b) Published, but TechScout does not read or bind it

1. **Purchasability on the domestic path.** `app/extraction.py` (line 225) reads
   `purchasable` only from a JSON-LD `additionalProperty` named "Purchasable". The
   test fixtures use that convention, but no real page observed does. The Shopify
   enrichment for OutGeeked and WinCell (`app/domestic_retailer_profiles.py`) never
   sets it. Both stores publish an enabled add-to-cart form for the exact variant. As
   coded, **no observed real page can verify on the domestic path**, whatever else it
   publishes.
2. **WooCommerce variant data on the domestic path.** Class Cellulars and Beytech
   publish each variant's price, stock and purchasability in the Store API and the
   variation form, and the reviewed v1 readers parse them. The domestic reader uses the
   variation form only to confirm a product link's single SKU. For offers it instead
   needs a JSON-LD `Product` with a matching `url`, a phone/laptop/… `category`, a
   brand and a SKU (`_page_listings`). In the 14 November captures, Class Cellulars'
   JSON-LD has no brand or category, and Beytech's has no `url` and the brand
   "Electronics". This is the likely reason the right product page still gave "No
   matching SKU" (C01 at Beytech's iPhone 17 page, C03 broader at Class Cellulars'
   iPhone 17 Pro page, assuming the same WooCommerce template).
3. **Store-level delivery and fee statements.** All five admitted sellers publish
   "delivery all over Lebanon" or equivalent wording; OutGeeked publishes it inside
   the product's own structured FAQ. Class Cellulars publishes "Free Shipping On All
   Orders Over $50" on the product page, and the cost grammar can already express it
   (`min_subtotal:USD:50.01`). The rules reject both, because item eligibility and the
   delivery fee must be route-scoped (`app/buyer_sources.py`: `NO_ITEM_ELIGIBILITY`,
   `NOT_ROUTE_EVIDENCE`; `app/domestic_buyer_sources.py`: `_offer`). This is a rule
   choice; see the next section.
4. **The new-only policy mechanism is never used.** `NewOnlyPolicy` and
   `apply_new_only_policy` (`app/eligibility.py`) turn a dated, seller- and
   category-scoped "we sell only new" statement into item condition evidence, with
   tests. Neither controller calls them. Wiring them now changes nothing, because no
   admitted seller publishes such a statement.
5. **Latent:** the OutGeeked/WinCell enrichment replaces the generic extraction, so
   `itemCondition`, `shippingDetails` or VAT data would be dropped if those stores
   added them.
6. **Ayoub Computers** (not admitted) publishes per-item "Condition: New" and
   "Shipping: Free Shipping", plus free delivery to every district. Its VAT is a rate,
   "Prices exclude 11% VAT, applied at checkout", which the v2 `Charge` contract
   cannot hold, so the reader keeps it unknown by design.

### (c) Caused by the request choice

- **C05** (iPhone 17, 2 TB) is designed to have no match. Abstaining is correct.
- **Pickup instead of delivery** would remove the dispatch, eligibility and delivery-fee
  needs. It would add branch-specific stock evidence, which no store publishes ("online
  stock does not prove selected-branch pickup stock"). Condition would still block, and
  the checkpoint controller does not support pickup. Switching would not have produced
  a result.
- **Needs-based requests** add a suitability check. In the two C12 runs that reached
  offers, the per-offer reasons were not kept, so its role there is unknown. The other
  needs-based runs never reached an offer. **C03** adds a SIM requirement; it never
  reached an offer either.

No recorded abstention is shown to have been caused by the request choice, apart from
C05 by design.

## Blocking rules checked against AGENTS.md

| Rule | Verdict |
| --- | --- |
| Unknown condition is not new | Required. Keep. |
| Unknown mandatory charges stay unknown, never zero | Required. Keep. |
| Online stock is not branch stock | Required. Keep. |
| An arrival estimate does not satisfy a guaranteed deadline | Required. Keep. (No recorded run set a deadline.) |
| An estimated fee range never verifies | Consistent with "conflicts cannot masquerade as known facts". Keep. |
| Dispatch from Lebanon needs a receipt | Consistent with the domestic scope and "do not claim … without evidence". Keep. |
| Delivery eligibility must be bound to the item and route; a store-wide delivery-area statement is not enough | **Stricter than AGENTS.md requires.** AGENTS.md asks for binding "where required" and does not say store-wide delivery statements are insufficient. Proposal F3. |
| A store-wide fee statement is not route evidence | **Stricter than AGENTS.md requires**, for the same reason. Proposal F3. |
| Condition and charges must come from a response read in this run | **Stricter than AGENTS.md requires**: AGENTS.md names only price and stock. Low impact today, because a policy page can be re-read in the run. No change proposed. |
| A published VAT rate stays unknown | Contract limit, not an AGENTS.md rule. Proposal F7. |

## Candidate sellers and products

| Seller | Admitted | Publishes | Missing | Can it qualify? |
| --- | --- | --- | --- | --- |
| Class Cellulars, iPhone 17 256 GB | Yes | Price, stock, purchasable, VAT included, free shipping over USD 50 (banner), delivery all over Lebanon (FAQ) | Condition **(a)** | Only if the store publishes condition, and after F3 |
| Ayoub Computers, Xiaomi 4K Monitor A27Ui (17 Nov capture) | No | Price USD 367, in stock, purchasable, "Condition: New", "Shipping: Free Shipping", free delivery to every district (border villages excluded) | VAT only as a rate; the listing does not name its marketplace seller; the gate gets HTTP 403 and pages exceed 2 MiB | Closest to complete; needs F7, a seller decision and an access route without bypassing protection |
| OutGeeked, Soundcore H30i | Yes | Price, stock, add-to-cart, dispatch from Beirut, product-level delivery statement | Condition **(a)**; fee and tax only at checkout **(a)** | No |
| WinCell, Soundcore H30i | Yes | Price, stock, add-to-cart, "Delivery across Lebanon" | Condition, fee, tax **(a)** | No |
| Beytech | Yes | Price, stock, purchasable (Store API), "Delivery all over lebanon" | Condition, fee, tax **(a)**; SIM ambiguous | No |
| Mojitech | Yes | Delivery and timing footer | Price **(a)** in the last capture; direct access 403 in the audit | No |
| Unchecked Shopify leads (iSTYLE, Abed Tahan, 961Souq, Macrotronics, HiCart) | No | Unknown. Shopify themes and feed apps sometimes emit `itemCondition` and `shippingDetails`. | Macrotronics calculates shipping at checkout (audit) | Check one product page each first |

## Proposed fixes, ranked by impact and effort

| | Fix | Impact | Effort | Needs a team decision |
| --- | --- | --- | --- | --- |
| F1 | Record which limit refused a read (ledger workload limit, gate wait, per-run cap, store status) in source outcomes and receipts. Pace consecutive batch runs below each shop's six requests per minute. | High: 16 of 27 calibration runs measured TechScout's own throttling, not retrieval, and the study's 337 "Listing read unavailable" outcomes cannot be split | S | No |
| F2 | Read purchasability from the selected variant's own signal: the add-to-cart form bound to the variant ID with an enabled submit (Shopify), or `is_purchasable` from the reviewed WooCommerce readers | Removes a blocker on every real page; alone it adds no verified result while condition is missing | S–M | Light: confirm the signal |
| F3 | A dated `DeliveryAreaPolicy`, modelled on `NewOnlyPolicy`: explicit areas and exclusions, seller and category scope, re-read in the run, binds item eligibility and any stated fee with its conditions to the route. It never covers deadlines or checkout-calculated fees. | Clears delivery eligibility at all five admitted sellers and the fee at Class Cellulars | M | **Yes**: changes a rule that is stricter than AGENTS.md requires |
| F4 | Use the reviewed WooCommerce readers inside the domestic controller, and seed shop reads by category and product, not the one audit sample URL | Lets the 6 A2 runs reach the fact checks (where condition still blocks) and stops wasted reads | M | No |
| F5 | Wire `NewOnlyPolicy` into both controllers, with adapters that extract the statement and its exclusions | None today; ready when a seller publishes one | S–M | No (already approved and tested) |
| F6 | Check one product page at each unchecked Shopify lead for `itemCondition`, `shippingDetails` and VAT data; admit the best one through source admission | The only route found to an admitted seller that publishes condition | M | Source admission review |
| F7 | A versioned rate-based tax charge (percentage of the listed price, rounding stated), plus a decision on marketplace seller naming, for Ayoub | One near-complete seller | L | **Yes**: contract change; access route still open |

None of these weakens a rule AGENTS.md requires. F2 with F3 is still not enough on its
own: some admitted seller must also publish condition, which is F5 or F6.

## Plan for one bounded live run (#75 box 2)

A live run today would abstain at every admitted seller (condition). So the run is gated
on free offline checks, and spends nothing unless they all pass.

1. **Go/no-go, offline and free.**
   - `tests/test_domestic_decisive_facts.py` passes on the target commit.
   - F2 has landed, and F3 if the seller states delivery only store-wide.
   - One manual read of the candidate listing on the day shows every decisive fact.
     Record the URL, time and SHA-256. If any fact is missing, **do not run**: add that
     read to this diagnosis instead.
   - `python -m app.live_check --check-ledger` reports no `MISSING` line.
2. **Case.** One exact-model request, new condition, delivered to Beirut, for a product
   that is not a held-out target. The candidate is the first seller from F6 that passes
   step 1. If Ayoub's blockers are cleared first, use the Xiaomi 4K Monitor A27Ui,
   category monitor. Its expected facts from the 17 November capture are: USD 367, in
   stock, purchasable, "Condition: New", "Shipping: Free Shipping", free delivery to
   every district, and VAT 11% (needs F7).
3. **Where.** Hasan's PC, which holds the ledger and the OpenRouter key. Follow the
   [runbook](live-flow.md) steps 1–3, but start the worker with the normal domestic
   controller, `--controller app.buyer_flow:run`, not the runbook's single-source
   `run_checkpoint`. The direct OpenAI key stays unset and fallback stays disabled.
4. **Spend cap.** One submission plus at most one stored-evidence repeat, and stop at
   USD 0.25 combined. Recorded domestic-controller runs settled at most USD 0.001874
   each (many settled zero), the two checkpoint runs USD 0.013705 and USD 0.015102, and
   the writer runs only when an offer verifies. The ledger's USD 1 per-run cap stays the
   hard limit.
5. **Record.** Post the `app.live_check summarize` output on #75 and tick box 2 only if
   the run verified an offer. A second, unresolved seller in the same run supplies the
   honest unresolved case.

If no seller passes step 1 by the date the team sets, the evidence for box 2 is the
recorded unresolved runs plus this diagnosis. Whether that satisfies "where source facts
support one" is a team decision.

## Relation to the held-out study (#82)

The study was frozen in #173, the base commit of this analysis, and published in #174
on 24 November. Its 230 trials on 23 domestic requests ended in 229 abstentions, one
timeout and zero verified recommendations. The
[study report](evaluation-report.md) lists the same three stage A causes as its most
frequent failed source outcomes: "Listing read unavailable" (337), "No matching SKU on
the current listing" (210) and "Listing URL is outside the active source scope" (106).
Its operator search also found no checked offer that established the exact item,
condition, stock, fulfillment and all mandatory charges together.

- **This diagnosis explains that outcome; it changes no study result.** The protocol
  keeps any held-out fix or rerun out of the results. So F1–F7 are post-study
  development, and any later measurement is reported separately.
- **The 337 "Listing read unavailable" outcomes mix two causes.** The text covers both
  TechScout's own refusals and store failures (root cause 1). If the sanitized study
  traces keep the gate's refusal codes, an offline re-count could separate them
  without rerunning anything.
- **Demo.** A live demo on today's admitted sellers will abstain, for the reasons
  above. The offline test gives a reproducible verified path to show next to an
  honest live abstention.

## Page reads on 24 November 2026

Read-only GETs, one at a time, no login, cart or checkout. The scripted client sent
`TechScout-research-check/1`; browser reads used an ordinary desktop browser. Times are UTC;
browser times are to the nearest few minutes.

| # | Time | URL | Method | Result |
| ---: | --- | --- | --- | --- |
| 1 | 12:10:40 | https://outgeeked.net/products/anker-soundcore-space-h30i?variant=47452782133506 | curl | HTTP 429, not retried |
| 2 | 12:10:54 | https://wincell.store/products/anker-soundcore-h30i-wireless-on-ear-headphones-black?variant=53481525805354 | curl | HTTP 429, not retried |
| 3 | ≈12:12 | Same as 1 | Browser | JSON-LD `ProductGroup`, black USD 20 in stock; no `itemCondition`, `shippingDetails` or VAT; "Add To Cart" enabled for the variant; product FAQ data "we ship to Beirut and everywhere in Lebanon"; menu "Used Devices" |
| 4 | ≈12:14, ≈12:16 | https://outgeeked.net/policies/shipping-policy | Browser, then a fetch tool | 200; no areas, fee amounts or VAT in the policy text; cart note "Taxes and shipping calculated at checkout" |
| 5 | ≈12:16 | https://wincell.store/policies/shipping-policy | Fetch tool | 404 |
| 6 | ≈12:22 | Same as 2 | Browser | JSON-LD `Product`, USD 40 in stock; no `itemCondition`, `shippingDetails` or VAT; "Add to cart" enabled; "Delivery across Lebanon"; no fee or condition text |
| 7 | ≈12:26 | https://classcellulars.com/ | Browser | "(Inc. VAT)"; no condition, used or refurbished wording |
| 8 | ≈12:28 | https://classcellulars.com/about/ | Browser | No "new", "used", "refurbished", "sealed", "genuine" or "authentic" wording; the only "original" refers to repair parts |

Read 4 is counted once: the browser loaded the page but its text extraction timed out,
so the fetch tool read it again two minutes later.
Ayoub Computers and Class Cellulars' product page and FAQ were not re-read; their facts
come from the 14 and 17 November fixtures under `tests/fixtures/stores/`.

## Reproduce

```sh
python -m unittest discover -s tests -p test_domestic_decisive_facts.py -v
```
