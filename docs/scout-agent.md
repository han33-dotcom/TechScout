# Scout agent

The scout is a new buyer search engine that sits beside the existing control flow and the single shopping agent. An LLM plans and judges. Code retrieves shop data, checks every claim and does the money. It reaches about 35 Lebanese shops through the public endpoints they already publish and answers in about one minute.

This document explains why it exists, how it works, what code still enforces, and how to run and evaluate it.

## Why: what was stopping useful answers

A fresh analysis of main, the frozen-20 receipts and Hasan's `test/two-agent` and `scratch` branches found five causes. They are ranked by how many answers they cost.

| Cause | Mechanism on main | Evidence |
| --- | --- | --- |
| 1. Reach and retrieval | Only 5 shops had product-read capability. Leads at other Lebanese shops went to background onboarding and were never read during the run. The WooCommerce Store API already returned price, stock and attributes, but the code kept only the links. It then re-read HTML that real pages could not satisfy. | 46 inspect actions in the 26 Nov frozen-20 run: 34 out of scope or unconfigured, 7 reader rejections, 3 ok. "No matching SKU on the current listing" appeared 210 times in the 230-trial study. |
| 2. Rigid perception | A regex parser turned the buyer's words into a model string. The model check was exact string equality. All-or-nothing per-shop readers returned nothing for out-of-stock or multi-variant pages. | 7 of 20 prompts produced broken model strings and 15 of 20 got permanent "unresolved constraints". B11: the exact iPad at Technodel, USD 475 and in stock, was rejected because the parsed model was "iPad". |
| 3. A blind agent | The LLM picked an index from a menu built by code. It could only reorder code-generated search words and never saw page text. | `app/shopping_agent.py`: `Decision.action_index`, `public_search_terms`. In the 230-trial study, fixed rules, the single agent and the full controller scored the same (0 priced matches). Changing who decides changed nothing, because every arm shared the same readers and gates. |
| 4. An answer bar no code path could meet | "Recommendable" needed item-bound tax, shipping and purchasability facts that Lebanese pages publish as shop-wide prose, if at all. | One verified delivered total ever (ITWorks audio). On `test/two-agent`, `Selection.partial` hard-codes `delivery-unchecked`, so none of its 18 receipt files reached `complete`. |
| 5. Slow, fragile mechanics | Firecrawl raw-HTML transfers took 45-171 s per page. A failed re-read erased a partial already shown, and one failed model call ended the run. | B31: a matching USD 1,448 laptop was on screen at 36 s and gone at 45 s. On the two-agent branch, blind H111 comparisons took 180-293 s. |

So the hypothesis that the project relied too much on fixed deterministic rules is half right:

- **Right:** reading the request, matching identity, judging features and writing queries were rigid code. That work belongs to the model.
- **Wrong:** the money, evidence and honesty rules are not what killed answers. They produced zero misleading claims and should stay in code. The biggest single loss was retrieval reach and speed, and more agency alone does not fix that. Hasan's most agentic branch still had zero complete answers.

## How it works

```text
buyer text ─▶ LLM turn ─▶ tools (parallel) ─▶ observations P1.., T1.. ─▶ LLM turn ─▶ ... ─▶ final offers
                                                                                            │
                                         code verifier (app/scout/verify.py) ◀──────────────┘
                                                      │
                                 checked offer cards: best / alternative / unavailable / excluded
```

**The model** (`app/scout/agent.py`, the pinned OpenRouter route from `var/inference.json`) reads the buyer's own words. Each turn it either issues up to six tool calls, which run in parallel, or finishes with proposed offers. It decides:
- what the request means;
- which real models fit a "help me choose" request;
- when a configuration does not exist;
- which queries to send and which products to open;
- whether a listing is the same product;
- which delivery rule applies;
- what to tell the buyer.

**The tools** (`app/scout/tools.py`) are free direct reads:

| Tool | What it does |
| --- | --- |
| `search` | Asks every directory shop at once: Shopify `/search/suggest.json`, the WooCommerce Store API, or a local index built from the shop's sitemap or `/products.json` when robots.txt forbids live search. It pre-opens the three strongest hits. Typical time is 3-6 s for about 35 shops. |
| `open` | Reads one product from the shop's own data. Shopify `/products/<handle>.js` gives every variant's price in cents, stock, options and SKU. The WooCommerce Store API gives variations with prices in minor units. Other shops are read through schema.org JSON-LD. Each read becomes an observation `P<n>` with URL, UTC time and SHA-256. |
| `read` | Reads a policy, FAQ, contact or product page, with search terms. Returns `T<n>`. |
| `shop_info` | The shop's published location (Shopify `meta.json` gives country and city), home-page delivery lines and policy links. |

**Price scope (buyer contract v2, 28 November).** The scout reports the shop's listed price, before
VAT and delivery, and says so in every answer. The checker (`check_offers`, listed scope by default)
no longer reads or requires a delivery fee or a VAT statement. An exact, in-stock offer with a price
read in the current run counts as verified when its condition is evidenced new, or not stated (the
answer then says the shop does not state it), or other than new for a buyer who accepted that. The
contract v1 rule (a delivered total) remains only as `scope='total'`, which no entry point uses; the
shop-terms job below belongs to it.

**Shop terms** (`app/scout/terms.py`) are each shop's published delivery fees, VAT statement and addresses. A daily job extracts them with exact quotes. Code keeps a rule only when its quote occurs on the page and contains the amount. During a buyer run the pages are re-read once per shop and only quotes still present are offered to the model. Delivery evidence is therefore current-run, not cached.

**The shop directory** (`config/scout-shops.json`, `app/scout/shops.py`) lists 36 enabled shops with their platform and provenance, detected on 27 Nov. Shops known only from frozen evaluation references are deliberately left out. Shops that answer with a bot challenge, block automated reads or report a non-Lebanese country are listed as disabled with the reason. Challenges are never bypassed.

## What code still enforces

`app/scout/verify.py` decides what the buyer is told is true. The model only proposes.

- **Price, currency and stock** come from the shop's structured data in a product observation made in this run, never from the model. A page without structured data needs a price quote that occurs in the capture.
- **Quotes must exist.** Each claim cites an observation, and its quote must occur in that observation's text. A missing quote turns "met" into an "unknown" gap; it never passes.
- **Identity can only be downgraded.** Numeric storage conflicts, tier words (Pro, Max, Ultra, mini, Plus, FE...) and screen-size conflicts on an exact-model request turn "exact" into "near". Used, open-box, refurbished or "activated" markers in the listing exclude it, whatever the model says.
- **Condition:** "new" needs a "new" quote from the product data, a read of the same product page, or the same shop's policy page. Otherwise it is a named gap.
- **Money is Decimal.** A delivered total needs a fee quoted from the same shop's own page. The quote must contain the amount and fit the destination: a fee quoted "inside Beirut" never prices Batroun. Unknown fees stay unknown, never zero. VAT is a named gap unless the shop states it.
- **A stated maximum is a hard ceiling.** When only the listed price is known, the offer says it may exceed the budget once delivery is added.
- **The summary cannot invent amounts.** Every money amount in the model's message must match a price, fee or total read in this run, or the buyer's own figure. Otherwise the message is replaced by a deterministic summary of the checked offers.
- A shop outside the directory is never read, and a seller outside it is never presented as Lebanese.

**Security:**
- `app/scout/web.py` sends HTTPS GET only, to hosts whose every DNS answer is public, over a connection pinned to the checked address.
- No cookies, no proxy, and an honest `TechScout/0.1` user agent.
- robots.txt is obeyed (RFC 9309 with wildcards), with per-shop pacing and caps and size limits.
- Cart, checkout, account, login and admin paths are never read, and redirects are rechecked.
- Retailer text reaches the model only as data, and the prompt forbids following instructions inside it. The offline tests feed a product description that tells the buyer the price is USD 1 and check that the shop's real price is what the buyer sees.

**Ledger:**
- Every model call is reserved, dispatched and settled in the shared ledger as a `buyer` job.
- Research calls must start by 62 s and the final call by 92 s, inside the 120 s buyer window.
- A failed call is retried once. If the model cannot finish, a deterministic fallback lists the products read in this run as unreviewed alternatives.
- The shop-terms job is a `maintenance` job, batched six shops per call to respect the team's 30 maintenance calls a day.

## Run it

From the repository root, in a PowerShell window where `Use-TechScoutLive.ps1` has been loaded (ledger settings), with `OPENROUTER_API_KEY` in `.env` and `var/inference.json` present:

```text
python -m app.scout.catalog refresh          # daily: sitemap and /products.json indexes (free, about 3 min)
python -m app.scout.terms refresh            # daily: shop delivery/VAT/address terms (about 6 model calls)
python -m app.scout "New black Sony WH-CH520 headphones, cheapest delivered to Batroun" --destination Batroun
python -m app.scout.server                   # local demo page at http://127.0.0.1:8010
python eval/run_scout.py acceptance/scout/requests/development.json --out var/scout-eval/dev
python -m unittest tests.test_scout -v       # offline: synthetic shops, scripted model, no network
```

## Evaluation

[The scout evaluation](../acceptance/scout/README.md) has the development iterations, the edge-case runs, and the held-out results. The held-out results were scored by independent graders with the frozen protocol, and set next to the earlier baselines.

**Locked candidate, frozen 20, compared with the single agent on 26 Nov:**
- **Useful offers:** a correct, current, linked, in-stock offer with the open facts named in 8 of 10 full cases (baseline: 2 of 20 showed a price and link).
- **Delivered totals:** evidenced on 4 cases (baseline: 0).
- **Partials and blockers:** 4/5 strict partials (0/5) and 5/5 explained blockers (4/5).
- **Speed and cost:** median 76 s, all within 120 s, USD 1.27 for the 20 runs.

**What still fails:**
- **The full acceptance gate.**
  - No verified payable total in any case, because new condition, the delivery fee or VAT was rarely published.
  - No verified seller pair.
- **Overclaims in three cases** (B05, B07, B11). They were fixed afterwards, each with a regression test, and the fixes are recorded separately from the score.

## Limitations

- **Delivered totals need a published fee.** Shops that compute the fee at checkout (Mobileleb, iSolved, OutGeeked, Classic Phones) stay "fee to confirm", because cart and checkout are never read.
- **Condition and VAT.** New condition and VAT are often not stated, so they show as open facts.
- **Blocked shops.** Shops behind bot challenges or blocks (Class Cellulars, Mojitech, PC and Parts, Ayoub, Expert-Zone, DSLR Zone, Carrefour) are not read.
- **Shop list.** The directory is a fixed, reviewed list, and new shops need a detection run. It lives in `config/scout-shops.json`, separate from `config/sources.json`; merging the two is a team decision.
- **No main-UI integration yet.** The scout runs from its CLI and demo page, not the normal buyer UI, API and worker. Wiring it in means mapping checked offers to the v2 contracts, which is a shared-contract change.
- **Unchecked summary text.** The model's summary text is checked for amounts and free-delivery wording only. Other statements in it are labelled as the assistant's summary.
