# Lebanese retailer audit — 16 November 2026

For [#50](https://github.com/han33-dotcom/TechScout/issues/50).
The [importable seed](../config/retailer-directory-seed.json) contains 20 directory
records: 12 priority audits and eight wider leads. The
[machine-readable audit](retailer-audit-2026-11-16.json) preserves entry points,
category coverage, identity uncertainty, policy references and 41 direct probe
results with UTC timestamps and hashes for successful replies.

## What the counts mean

All 20 are discovered candidates. Twelve received the bounded direct audit:
homepage plus a representative product or catalog route and a policy/identity
route, with six additional targeted checks. Nine priority homepages returned
200; three returned 403. Product routes also returned 429, and a 200 catalog
did not always expose usable product cards. **Zero qualifying offers were
established by this audit.** This is neither a claim that these stores have no
eligible offers nor a working adapter count.

The eight wider leads received only discovery/identity research, not the
product-and-policy retrieval audit. Retailer-owned pages verified Technodel,
iStore Plus, KAYSTORE and Microcity; Tech Zone's retailer-owned search result
identified the site but its browser open timed out. GCS, M2 and Qabass remain
[MEIT distributor leads](https://www.meit.biz/resellers/), with identity and
category verification pending. A browser error is recorded as a limitation, not
a negative reputation judgment.

A single 15-minute wider discovery sweep was bounded separately from the priority
audit. Future sweeps should add competing configurations, missing categories and
regional shops. There is no inventory ceiling or store-count integration quota.
[Competitor directories](https://lebtechlb.com/shops) supplied leads only;
none of their prices, stock or rankings became purchase evidence.

## Method and limits

Direct read-only HTTPS GETs ran at 18:51 UTC onward (exact times in the sidecar), with a 20-second timeout,
4 MB body cap, at most four different hosts concurrently, one request per host
at a time, and at least 11 seconds between each source's detail requests.
No retries, paid APIs, logins, carts, messages or purchases were used. These
were explicitly scoped research checks outside the application's runtime;
the existing source registry was not broadened or activated.

Separate browser search/open checks supplied retailer-owned navigation and
policy context. That tool may return cached content: browser visibility does
not undo a direct 403/429 or establish current-runtime reliability. Dates below
are review dates; direct metadata supplies exact observation times. Only metadata
and our summaries are committed, not entire retailer pages or private contacts.
Hashes identify successful response bytes; they are not reproducible archived
purchase evidence. Later ingestion must retain excerpts and variant-bound facts.

Category coverage means navigation/catalog evidence, not qualifying products.
P = phone, L = laptop, T = tablet, M = monitor, A = headphones/earbuds category
lead. Unlisted categories remain **not established**, not unsupported.

## Priority findings and implementation order

| Retailer / canonical storefront | Observed category leads | Direct sample result | Next step |
|---|---|---|---|
| [Beytech](https://beytech.com.lb/) | P L T M A | Homepage, iPhone, warranty and Store API 200 | First WooCommerce expansion; preserve SIM and fee unknowns |
| [Expert-Zone](https://ezonelb.com/) | L M A | Homepage, Lenovo monitor and warranty 200 | Reuse WooCommerce profile; separate used/refurbished listings |
| [Ayoub Computers](https://ayoubcomputers.com/) | L T M A | Homepage, laptop catalog and guide 200; search found Acer product route | BigCommerce/rendered discovery; identify merchant and fulfiller |
| [iSTYLE Lebanon](https://istyle.com.lb/) | P L T A | Homepage, iPhone Air and policy 200; sale collection 429 | Shared Shopify profile; exact part number and selected branch |
| [961Souq](https://961souq.com/) | P L T M A | Homepage, monitor and policy 200 | Headless Shopify/structured data; reconcile contradictory stock |
| [Macrotronics](https://www.macrotronics.net/) | L T M A | Homepage/policy 200; laptop 429 | Shared Shopify profile with backoff; strong configuration overlap |
| [Abed Tahan](https://abedtahan.com/) | P L T M A | Homepage/policy 200; laptop 429 | Shared Shopify profile; verify price and delivery per item |
| [Class Cellulars](https://classcellulars.com/) | P L T A | Homepage, phone, FAQ and existing API 403 | Preserve reader; diagnose direct access, then expand scope |
| [Mojitech](https://mojitech.net/) | P L T M A | Homepage, laptop, policy and existing API 403 | Preserve reader; recover direct access and configuration extraction |
| [PC and Parts](https://pcandparts.com/) | P L T M A | Homepage, laptop and shipping policy 403 | Valuable laptop/monitor overlap; diagnose direct access |
| [HiCart](https://www.hicart.com/) | P A | Homepage/warranty 200; phone 429 | Marketplace merchant extraction before qualification |
| [Amhaz Official candidate](https://amhazofficial.com/) | P T A | Homepage, phone and contact 200 | Resolve intended identity; retain missing policy and stock evidence |

These priorities weigh usable coverage and observed extraction obstacles.
They do not measure retailer trustworthiness or promise twelve bespoke adapters.
Platform labels are implementation hints from route/page structure and existing
readers; they do not prove that a public JSON endpoint or all variants work.

## Purchase facts and policy gaps

- **Class:** the [FAQ](https://classcellulars.com/faq/) describes delivery estimates,
  cash-on-delivery and conditional returns. Generic delivery claims and branch
  addresses cannot establish a selected item's deadline or pickup stock.
- **Mojitech:** its [homepage](https://mojitech.net/) mixes priced and inquiry-only
  laptop entries. The [policy route](https://mojitech.net/refund_returns/) timed out
  in browser research as well as returning direct 403. The November 14
  all-zero iPhone snapshot remains historical; do not generalize it to today's
  catalog.
- **Beytech:** the [sample phone](https://beytech.com.lb/product/apple-iphone-17-1-sim-2-years-warranty/)
  and [warranty policy](https://beytech.com.lb/warranty-terms-conditions/) remain
  accessible. The title's SIM description is ambiguous; duration alone does not
  establish warranty provider/territory, condition, stock scope or delivered total.
- **PC and Parts:** [shipping](https://pcandparts.com/shipping-options/) depends on
  zone and package weight, with possible surcharges. Pickup requires an
  appointment. Tax wording on one listing cannot be transferred to another.
- **Ayoub:** its [delivery guide](https://ayoubcomputers.com/quick-delivery-guide/)
  explicitly covers Ayoub, partners and marketplace vendors. Keep actual seller
  and fulfiller separate. The catalog footer excludes VAT; establish the exact
  item price and applicable charges before computing a total. Large navigation
  HTML is not proof of extracted products. A separately located
  [Acer laptop](https://ayoubcomputers.com/acer-aspire-5-a515-58p-53sd-15-6-laptop-intel-core-i5-1335u-ram-8gb-ssd-256gb-intel-iris-xe-nx-khjem-007/)
  exposes specifications and marketplace terms, but is marked out of stock.
- **Abed Tahan:** [shipping policy](https://abedtahan.com/policies/shipping-policy)
  was retrieved directly; no complete selected-item delivery quote was extracted.
  Product 429 prevents declaring the sampled listing usable. Do not confuse
  similarly named domains with the canonical site.
- **Macrotronics:** [shipping policy](https://www.macrotronics.net/pages/shipping-and-delivery-policy)
  varies charges by package, method and destination and calculates them at checkout.
  The audit did not enter checkout. Unknown shipping remains unknown; distinguish
  renewed items and bind tax statements to the selected variant.
- **961Souq:** the homepage marked the [sample monitor](https://961souq.com/products/aoc-24b36h3-monitor)
  sold out while its retrieved structured data said in stock. Treat that as a
  contradiction to resolve, not permission to choose the favorable value. The
  [shipping route](https://961souq.com/policies/shipping-policy) is available, but
  no complete item-specific fee or deadline was established.
- **HiCart:** [terms](https://www.hicart.com/pages/terms-conditions) describe
  independent sellers; its [warranty page](https://www.hicart.com/pages/warranty)
  assigns coverage to suppliers where stated in the listing. The sampled offer's
  actual merchant remains unknown. Apple/Nokia/vendor brand text is not a seller.
- **iSTYLE:** [store policy](https://istyle.com.lb/pages/store-policy) describes
  conditional exchange and DOA handling. The [iPhone Air sample](https://istyle.com.lb/products/iphone-air-mg2y4af-a)
  has a model part number; neither the generic store picker nor a collection's
  availability filter proves selected-branch stock.
- **Expert-Zone:** [warranty policy](https://ezonelb.com/return-warranty-policy/)
  makes duration item-specific. Resolve new versus used/refurbished condition,
  tax and delivery before qualification. Branch lists alone do not establish stock.
- **Amhaz:** [contact](https://amhazofficial.com/contact) identifies **Amhaz Official,
  Saint Therese, Hadath**. This does not establish that it is the intended Amhaz
  or the same entity as Amhaz Original. The [phone sample](https://amhazofficial.com/product/1018)
  shows unavailable/availability-check wording; shipping/FAQ labels did not
  provide usable policy links. Keep a distinct candidate ID, no inferred aliases
  or merchant identity, and no qualifying offer. Resolving user intent can follow
  independently; the issue explicitly allows a documented unknown.

## Import and handoff

Each item in `config/retailer-directory-seed.json` validates as the existing v2
`RetailerDirectoryEntry`. Import the JSON array through the future #86 directory
repository. Join the sidecar by `retailer_id` to retain catalog/product/policy
entry points, evidence-linked policy findings, category unknowns and dated
technical observations. The sidecar has its own `audit_schema_version: 1`;
it does not change the shared v2 contract.

`audited` means this technical research was performed, including failed probes;
it does not mean active or qualifying. Every seed omits a live capability.
`policy_evidence_ids` stays empty because research notes are not field-bound
corpus evidence. Sidecar policy findings reference the reviewed source URLs;
#86/#87 should ingest actual source excerpts before producing those evidence IDs.
The four distributor/search leads retain unresolved identity in the sidecar and
coverage gaps; import must not silently promote them to confirmed sellers.

#51/#52 can use the route samples and failure cases for adapters. #86 consumes
the seeds, #87/#88 perform bounded collection/maintenance, and #99 validates
automatic onboarding against representative products and variants through
SourceGate. This audit is not activation evidence for those capabilities.

Offline validation:

```powershell
python scripts/check_retailer_seed.py
python -m unittest discover -s tests -v
```
