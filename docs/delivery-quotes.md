# Retailer pre-order delivery quotes

Issue [#186](https://github.com/han33-dotcom/TechScout/issues/186) covers scenarios 11, 12, 14 and 15 of the [acceptance matrix](buyer-acceptance-matrix.md). It also finishes box 2 of [#187](https://github.com/han33-dotcom/TechScout/issues/187): pending quotes resume after the buyer picks an address.

A search can leave a Lebanese seller's delivery fee unpublished for the buyer's destination. Its payable total then stays unknown. Where a reviewed quote route exists, TechScout asks the seller's own pre-order step for that fee. The question covers one item, variant and quantity for one destination. The chat then shows the quote and a revised comparison, without a new search.

TechScout never orders, pays, reserves, creates a retailer account or messages a seller.

`app/quote_capabilities.py` defines the route. `SourceGate.quote` in `app/sources.py` is the gate exception. `app/delivery_quotes.py` holds the quote records, planning, the worker loop and the revised comparison. Migration `0011` adds the `buyer_quotes` and `buyer_quote_plans` tables.

## The quote route and the gate exception

Every retailer read is a GET. The gate refuses cart, checkout and account paths for reads, and that does not change. The single exception is `SourceGate.quote`: one POST, under strict conditions.

- **The protocol is fixed in code.** A source's `quote_capabilities` entry supplies only typed settings:
  - host, API version and currency;
  - the quantity cap;
  - whether the seller needs a city or a street address (`destination_detail`);
  - an optional `TECHSCOUT_…` token variable;
  - review evidence.

  The entry cannot supply a URL template, a request body or a header.
- **There is one protocol: Shopify's Storefront API `cartCreate`.** It builds a new anonymous cart holding exactly one variant and quantity for one Lebanese destination. The reply contains the line's price and the cart's delivery options, with each option's method. The fixed request asks for no checkout URL and echoes no address. Shopify carts hold no stock, so nothing is reserved.
- **The gate checks every request before sending it.** It refuses anything other than the reviewed document, including:
  - another mutation or a checkout field;
  - a second line or a quantity above the route's cap;
  - a buyer email, phone or note;
  - an address field the route does not need;
  - markup or control characters;
  - duplicate JSON keys or an oversized body.

  It also refuses a route whose status is not `active`.
- **Every other gate control applies:**
  - the registry is re-read on every attempt;
  - the store must resolve to a public address, over pinned and verified TLS;
  - per-source rate, concurrency and per-run limits apply;
  - the shared ledger must reserve the request;
  - the reply must be strict JSON within a 256 KiB cap.

  On top of those rules, a quote:
  - follows no redirect;
  - sends and keeps no cookie;
  - is never retried.
- **Refusals use the documented error codes.** A refused route or request is `BLOCKED` with the existing `blocked:url` prefix. A required token that is missing is `SOURCE_DISABLED`.

### ITWorks Lebanon, pending its live check

`config/sources.json` declares `itworks-delivery-quote` for `shop.itworkslb.net`. Its settings are:

- the `shopify_storefront_cart` protocol at API version `2025-01`;
- a city-level destination;
- USD;
- quantity 1.

ITWorks' purchase form is a Shopify storefront `LinesAdd` cart form (#191). The route was built from Shopify's public documentation and offline fixtures. Nobody has checked it against the live store, so it ships with status **`pending_live_check`**. The gate refuses it, and no ITWorks quote is sent.

To activate it, someone on the machine with live access must:

1. Confirm that `https://shop.itworkslb.net/api/2025-01/graphql.json` accepts the reviewed `cartCreate` document. If the store needs its public Storefront token, put the token in an environment variable such as `TECHSCOUT_ITWORKS_STOREFRONT_TOKEN` and name it in `token_env`.
2. Make one quote for a known in-stock variant to Beirut. Record the response hash and observation time, and check that no order or checkout was created.
3. Set `status` to `active` and update `review` with that evidence.

If the store serves the Storefront API only on another host, the route needs a reviewed change first. Quote hosts must equal the source's origin.

## Which sellers are quoted, and when

The worker plans each chat's latest finished search. It only plans for a Lebanese seller's direct delivery whose total is open because of the delivery charge (`costs.order.shipping`).

| Seller's route | What happens |
| --- | --- |
| Active, city-level | Quoted at once for the search's own city. The buyer is not asked. This is scenario 11's city quote. |
| Active, street-level | Named once in the chat's address prompt (`waiting`). Quoted only after the buyer selects a saved location and ticks this seller. Only this seller receives that address, through `BuyerAccounts.release_address`. |
| None or inactive | Never asked. If the buyer ticks it, its quote is recorded `unavailable` without any request and without releasing the address. |

Pickup requests, unfinished searches and sellers with a published or verified fee need no quote.

Further limits on each quote:

- **Same city only.** An address in a different city from the search is not sent.
- **At most six requests per search, across all destination choices.** Choosing another address does not renew the allowance; the rest are recorded as over the allowance.
- **One unit only.** Every quote is for a quantity of one, because the request has no quantity field.
- **Still current when sent.** Before each send, the worker checks that the quote still applies to the chat's latest search and destination. One that no longer applies is not sent.

## Quote states

Each quote (`DeliveryQuote`) has one of these states:

| Status | Meaning |
| --- | --- |
| `pending` | Planned, not yet sent. |
| `requested` | Sent, waiting for the reply. A lease of 60 seconds guards it. If the lease expires, the quote becomes `unavailable` and is not resent. |
| `quoted` | Usable. Records the source route, endpoint, observation time, response hash, item price, the included charges from the seller's terms (for example tax), the added fee once, and `quoted_total` (the item price plus that fee). `quoted_total` is not a payable total while another mandatory charge is unknown; the revised comparison decides that. |
| `unavailable` | No usable quote. Possible causes: no route, another city, the allowance, a busy workload limit, a refused, failed or malformed reply, no delivery option, an item not for sale, or a withdrawn address. The total stays unknown. |
| `conflicting` | The pre-order price disagrees with the listing, or the quoted fee disagrees with a published one. The total stays unknown. |

Only options that deliver (Shopify's `SHIPPING` or `LOCAL` method) can be the fee. The cheapest of them is used, and a free in-store pickup is never a delivery fee. A reply of any unexpected shape settles that one quote as `unavailable` and never stops the others.

`selection.quote_state` on the chat's destination summarizes its street-level quotes:

| State | Meaning |
| --- | --- |
| `pending` | Waiting to be sent. |
| `requested` | At least one quote is in flight. |
| `quoted` | Every quote is usable. |
| `partial` | Some quotes are usable. |
| `unavailable` | No quote is usable. |
| `not_needed` | No named seller's total depends on the address. |
| `none` | The buyer declined. |

A request is identified by its seller, variant, quantity and destination.

- **Sent once.** A request that reached the seller, or might have, is never sent again in that chat. That includes a reply that failed and a quote interrupted mid-request.
- **Unsent requests are reconsidered.** One that was never sent stays visible as `unavailable` with its reason. The causes are no route, another city, the allowance, a withdrawn address or an inactive route. The next new search or destination choice reconsiders it, so a fixed cause does not block the quote for good.
- **Busy limits are paced.** TechScout's own rate limit or the shared ledger can refuse a request before sending. That request waits 30 seconds and is tried again, up to four times, before it is recorded as unsent. A search that just used the seller's per-minute allowance therefore does not lose its quote.

**A destination change invalidates earlier quotes.** Every new choice, a deleted location or a new search re-plans the chat. Quotes for other destinations stop applying and drop out of the chat's quotes and revision. If the buyer switches back, the earlier quote for that same destination is reused rather than requested again.

## What the buyer sees

`GET /api/runs/{id}/destination` (`DestinationState`) gains three additive fields:

- **`waiting`:** the sellers to name once in the prompt.
- **`quotes`:** the quotes that apply to the latest search and current destination.
- **`revised`:** the comparison after those quotes.

The search snapshot itself never changes.

The revised comparison works like this:

- **The quote becomes evidence.** It turns into a route-scoped delivery charge on its offer. The existing eligibility and cost rules then recompute that offer.
- **Ranking uses verified totals only.** Verified, eligible totals are ranked in one currency, and equal totals share a rank.
- **Unknown stays unknown.** No usable quote, a conflicting quote, a declined address or a price that changed after the quote all keep the total unknown. They add a fixed reason, and the product link stays.
- **The advice is fixed text.** It names the cheapest verified total and the seller it replaced. It says which sellers gave no usable quote, and that prices and stock come from the search.
- **No arrival date.** A quote never sets one. The retailer's option name, for example "Guaranteed next-day", cannot satisfy a guaranteed deadline.

Retailer text never becomes a reason. Only a sanitized option name, at most 60 characters, labels the fee.

## Privacy

A precise address exists only inside the single quote request to a seller the buyer named. It is never stored in quote rows, plans, snapshots, history, exports or logs. The quote records the saved location's ID, label and city. `release_address(..., location_id=…)` returns nothing once that location is no longer the chat's choice. Signing in moves the chat's quotes with its runs.

## Running it

Start the buyer worker with `--quotes` to run the quote loop in a background thread. It uses the same database, the shared ledger and the live registry as the search worker. Each quote is a ledger job `quote:<quote ID>` of one retrieval:

```text
python -m app.run_worker --controller app.buyer_flow:run --quotes
```

The API reads quote routes from the reviewed `config/sources.json`. Onboarding never adds one.

## Validation

`tests/test_quote_gate.py` and `tests/test_delivery_quotes.py` use only synthetic sellers, scripted Storefront replies, a fixture ledger and the invented addresses in `tests/fixtures/accounts.json`. Nothing reaches a retailer, a model or a live service. They cover:

- **The exception stays narrow.**
  - The exact POST is sent with no cookie.
  - Pending and disabled routes are refused, including the real ITWorks route.
  - Many altered requests are refused before anything is sent.
  - Redirects are refused, and failures are not retried.
  - Budgets, per-run caps and tokens apply.
  - Unsafe route settings disable the source.
  - Reads still refuse cart, checkout, account and quote paths.
- **The buyer flow works offline.**
  - A city quote arrives without asking, and street sellers are named once.
  - A selected address resumes quotes in the same chat for the ticked sellers only.
  - The quote record fields, and the fee counted once.
  - A destination change, and no repeated request.
  - No usable quote, a failed reply, a conflicting quote and a declined address each keep the total unknown and the links.
  - A seller without a route never receives the address.
  - An address in another city is not sent.
  - An estimate against a guaranteed deadline.
  - An interrupted quote, and the chat moving at sign-in.
  - The shared ledger: a busy refusal paced and then sent, or given up unsent and asked again later.
  - The per-search allowance across destination choices.
  - An unsent quote reconsidered after the search changes, and a pending quote that no longer applies left unsent.
  - Malformed replies settling only their own quote, and a free pickup option never used as the fee.
  - No street address in storage, logs, responses or exports.

No quote has been made against a live retailer.
