# International source and route audit — 17 November 2026

For [#60](https://github.com/han33-dotcom/TechScout/issues/60). The
[initial ingestion manifest](../config/international-ingestion-manifest.json)
selects Microless UAE and B&H US for direct-delivery investigation, Amazon US
for forwarding, MyUS first and Stackry backup. It is planning data, not a source
registry extension, running collector, or offer corpus. The
[direct probe log](international-audit-2026-11-17.json) contains timestamps,
HTTP outcomes, byte counts and hashes. **Zero qualifying international offers
or item-specific Lebanon routes were established.**

## Findings and replacement decisions

| Target | Observed retrieval | Decision |
| --- | --- | --- |
| Microless UAE | Three product pages and shipping policy: HTTP 200 | Best first extraction candidate. Bind selected seller and regional variant, reconcile stock, then establish a Lebanon route. |
| B&H US | Laptop and international policy: direct HTTP 403; retailer-owned browser page available | Hold broad collection. Browser visibility does not prove runtime access. Keep as the second target, with an admitted retrieval path still required. |
| Amazon US | One product page: HTTP 200, renewed and unavailable; browser discovery was unreliable | Reject this sample for the default new-condition corpus. Find a new, in-stock offer with seller and fulfiller before expanding. |
| MyUS | Lebanon and restricted-merchandise pages: HTTP 200 | First forwarding-policy target; battery acceptance and item quote unverified. |
| Stackry | Lebanon calculator and prohibited-items pages: HTTP 200 | Backup only; calculator landing page is not a carrier quote. |

This does **not** meet the aspirational two-usable-source coverage target.
It satisfies the audit's explicit gap-reporting branch. Prefer overlapping
Microless configurations as replacement candidates for inaccessible B&H items,
within Microless's existing ceiling. Prefer B&H or Microless new-condition
alternatives to the failed Amazon sample, subject to their own unresolved
retrieval/route checks. Do not silently transfer unused allocations or call these
replacements validated. Shop & Ship remains the plan's untested directory lead
if both forwarding targets fail; it has no collection allocation here.

## Representative item and seller evidence

Exact URLs, response hashes and observation times are in the manifest. Read
prices below only as observations under the page's default destination, never
as a delivered Lebanon total or current recommendation.

- **Microless phone, SKU 256840:** black iPhone 17, 256 GB, international/eSIM
  label, selected seller SeenGo, AED 3,750.00. Fulfiller unresolved.
- **Microless phone, SKU 257832:** white iPhone 17, 256 GB, international/physical
  SIM plus eSIM label, selected seller **ITOA**, AED 3,371.73 in the direct
  capture. Earlier browser content named a different offer; do not merge seller
  identity across observations. A Microless fulfillment label on an accessory
  card does not establish fulfillment for this phone.
- **Microless laptop, SKU 265096:** ThinkPad E16 Gen 3, part **21SR005MGR**,
  Core Ultra 5 225U, 16/512 GB, English/Arabic keyboard. The offers page has
  Al Lavisha Computers Trading LLC at AED 4,499.00 and a distinct Microless
  seller/fulfiller row at AED 4,589.00. The manifest deliberately selects the
  latter row, rather than joining the cheapest price to another seller.
- **B&H laptop:** ThinkPad E14 Gen 6, **21M3000NUS**, Ryzen 5 7535U,
  16/**256** GB. These are retailer-owned browser-page observations only.
  Default delivery was Atlanta, not Lebanon. Direct retrieval failed, so no
  live seller/fulfiller, price, stock or route fact is promoted.
- **Amazon laptop:** ASIN **B0GSH4P948**, ThinkPad T14 Gen 2, i5-1145G7,
  16/512 GB, renewed and unavailable. Actual merchant and fulfiller were not
  exposed as an active offer; Amazon Renewed branding is not seller identity.
  Manufacturer part number and forwarding-address acceptance are unresolved.

Both phone pages expose stock-positive and unavailable text in the same HTML.
The selected offer/state needs structured extraction before either can qualify.
Their regional labels do not establish exact regulatory model, supported network
bands, Lebanon carrier compatibility, or transferable warranty. SIM variants
must remain separate. No package weight/dimensions or battery Wh rating was
validated for these five samples. Battery-life hours are not a shipping Wh rating.
Tablets, monitors and audio retain planned allocations but have **no item-level
sample validation** in this audit.

## Direct and forwarding demonstration cases

| Case | What was demonstrated | Missing before qualification |
| --- | --- | --- |
| Microless 21SR005MGR → Lebanon | Exact part and separate merchant/fulfiller row; international shipping policy retrieved | Actual destination acceptance, battery/package data, freight and import-charge quote, warranty territory |
| B&H 21M3000NUS → Lebanon | Exact part in retailer browser evidence; international policy describes restrictions | Reliable admitted extraction, actual merchant/fulfiller, item/destination acceptance, package data and quote |
| Amazon B0GSH4P948 → MyUS US warehouse → Lebanon | Product listing and MyUS country/restriction policies retrieved | Replace unavailable renewed listing; seller acceptance of warehouse address, installed-battery eligibility, both transport legs and full quote |
| Same forwarding path via Stackry | Country calculator and restricted-item policy retrieved | The same merchant/item checks plus carrier-specific battery decision and quote; no automatic fallback |

These are **failed/incomplete validation cases**, not successful purchase paths.
No cart, checkout, account, retailer message or purchase was used. A country
selector containing Lebanon does not establish item eligibility; neither does
the absence of Lebanon from an exclusion list. Default UAE or US delivery dates
do not establish a Lebanon arrival estimate or guarantee.

The [Microless shipping policy](https://uae.microless.com/shipping/) describes
international freight and possible additional import costs. It also refers to
checkout calculation; no such quote was obtained. Keep freight, tax, duty and
brokerage independently unknown until an item/destination observation resolves
them. The [B&H international policy](https://www.bhphotovideo.com/find/HelpCenter/International.jsp)
retains manufacturer/export restrictions and destination-dependent charges;
country-level availability is insufficient. Its policy was browser-visible,
not successfully retrieved by the direct probe.

[MyUS restrictions](https://www.myus.com/faq/restricted-merchandise/) make
dangerous-goods acceptance conditional on the item, destination and carrier.
The observed policy lists an $8 handling charge for an installed lithium battery;
uninstalled/dangerous-goods handling has different terms, including an 8% charge
with a $29.99 minimum for specified services. These are policy observations,
**not an applied fee or accepted route** for our samples. Recheck applicability
and require battery chemistry, installed/loose status, count, Wh and any requested
safety documentation. [MyUS Lebanon](https://www.myus.com/where-we-ship/lebanon/)
also distinguishes weight-based pricing from exceptions requiring dimensions.
Its country page does not settle taxes, insurance, handling or package-specific
freight for these items.

[Stackry's restricted-item list](https://www.stackry.com/prohibited-items)
flags lithium batteries. The
[Lebanon calculator](https://www.stackry.com/shipping-calculator/shipping-from-usa-to-lebanon)
requires shipment inputs; a generic destination page cannot establish which
carrier will carry the actual battery/package. The audit does not assert a
blanket ban or blanket acceptance of installed batteries.

For every eventual quote, retain merchant shipping to the forwarding warehouse
when applicable, international freight, handling/consolidation/storage,
applicable battery charges, required insurance, duties/tax/brokerage and payment
or currency fees. Bind each to item, seller, destination, package, service and
expiry. Record uncertainty separately from zero. Warranty territory and return
shipping also remain unresolved. No landed total is computed here.

## Allocation and accounting

The manifest preserves the approved **300/300/100** product-URL ceilings for
Microless/B&H/Amazon. Its proposed category split totals 245 phones, 245 laptops,
70 tablets, 70 monitors and 70 audio products: 70% phones/laptops. These are
maximum target allocations, not counts of discovered or eligible products.
Supporting evidence has separate 100 specification/review and 30 policy URL
ceilings; every attempt still consumes the shared fetch envelope.

Direct probes ran **15:41:07–15:41:46 UTC**, 11 attempts in 39.469 wall seconds,
with no retries or followed redirects. They used a 20-second timeout, 4 MB body
cap, at most four hosts concurrently, one request per host, and 11 seconds after
each same-host response. The product sample was three Microless URLs, one B&H
URL and one Amazon URL; six policy requests made up the remainder. Direct
requests incurred zero paid project-provider calls. Browser research also used
official pages, but its internal fan-out and service billing are not exposed;
the 11 direct attempts are **not a measured total for all research tooling**.
Only metadata and our summaries are committed, not full retailer pages.

**Accounting discrepancy:** these manual probes were executed outside the
ledger without reservations. That did not meet [shared accounting](shared-accounting.md).
In the subsequent [local-first setup](local-accounting.md), explicitly selected
by the user, the entire **100-attempt, 15-minute, $3** validation envelope was
posted to the authoritative local ledger as an immutable conservative hold.
The full 60-call international paid allowance is also held because browser
paid-call usage remains unresolved. This does not retroactively establish prior
reservations or claim that exactly those resources were consumed. Teammates
must not create independent ledgers or reset the allowance.

Before other existing usage, that leaves at most **900 international attempts,
45 minutes and $12**, subject to the authoritative ledger's actual lower balance.
There are **zero paid international call slots available** until the hold can be
reconciled with evidence. The parent all-market initial envelope was debited too.
Keep the existing 2,000 domestic/shared attempt slots, $500 campaign cap and
daily maintenance limits. No broad collection was started. Source admission,
workload caps and runtime enablement remain separate prerequisites.

## Validation and next implementation boundary

The manifest is parsed and checked for unique sample URLs, provenance links,
category/allocation totals and fail-closed readiness. Ordinary CI remains
offline. Live results above establish only this bounded sample's observed
retrievability; HTTP 200 alone is not successful extraction.

Next work must resolve the paid-call hold, implement an admitted Microless reader
with offer/region binding, resolve or replace B&H retrieval, and replace the
Amazon sample. Then validate actual item routes and quote components within
the remaining shared allowance. Broad ingestion and recommendations stay
disabled while these prerequisites are unresolved.
