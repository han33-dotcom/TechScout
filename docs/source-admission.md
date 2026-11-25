# Initial retailer sources

**MVP 01: complete.** Reviewed 12 October 2026 (Asia/Beirut).

## Project assumption

The user explicitly instructed Tech Scout to assume there are no restrictions on obtaining data from public websites. This is the project's working assumption. Public data retrieval does not require retailer permission emails, licensed-feed agreements or a retailer reply.

Source selection is now a **technical check**: accessible public endpoints, usable product/variant fields and enough overlap for the smartphone MVP. The previous permission-first gate is removed. No outreach is required or planned.

## Selected sources

Current public API requests returned HTTP 200 and parseable iPhone 17 product/variant data. The observed amounts below are published product prices, not verified delivered totals.

| Source | Public product | Observed product price | Usable evidence |
|---|---|---|---|
| [Class Cellulars](https://classcellulars.com/product/iphone-17/) | iPhone 17 256 GB | USD 1,100 | Parent ID 18392; four actual color variants; price/currency, stock and purchasability fields. |
| [Mojitech](https://mojitech.net/shop/brands/apple-brands/apple-iphone-17-256gb/) | iPhone 17 256 GB | USD 1,100 | Parent ID 91706; twelve options, two previously checked priced/purchasable ABM-warranty Blue/White variants. API price precision is one fractional digit. |
| [Beytech](https://beytech.com.lb/product/apple-iphone-17-1-sim-2-years-warranty/) | iPhone 17 256 GB | USD 1,079 | Parent ID 139968; four priced/purchasable color variants. API price precision is two fractional digits. |

The fresh API observations are timestamped 12 October 2026 at approximately 14:49 UTC (17:49 Beirut). URLs, times and response hashes are in `docs/source-evidence.json`; raw snapshots remain in the private research archive.

All three have overlapping model/storage/color options. Exact SIM/region and warranty compatibility still require the later offer checks; this does not prevent retrieving their public data. Mojitech's zero-price/non-purchasable variants must be excluded by the later parser.

## Configuration and next implementation work

`config/sources.json` marks these three sources active and records their public product/API and relevant information endpoints. The existing `admission` field now records technical review and the user's project assumption; it does not represent merchant approval.

`app/source_registry.py:get_active_source` validates configured source identity, review provenance, endpoint scope and positive execution limits. No permission-email field exists. Its selected-source bounds are our engineering defaults: six requests/minute, one concurrent request and six requests per shopping run. They control polling/workload, not admission negotiations. The per-run cap went from four to six for the final live re-check (issue #70): a search (two requests), every policy page a run can fetch, and one final re-read of the finalists' product (two requests). The minute limit stays six, so one run never exceeds a store's minute allowance, but one run can now use all of it. A second search within about a minute may then find that store's minute window full:

- If the window clears within 10 seconds, the gate waits and then sends the request.
- Otherwise the gate's limiter refuses it (`RATE_LIMIT`).
- In production, the shared accounting ledger also counts every process's requests. When it already holds six requests to that store in the last minute, it denies the request, and the gate reports `BUDGET_EXHAUSTED`.

Either way, the refused request is not sent. That store's search may then return offers from its API reply only, or nothing. On demo day, leave about a minute between searches. The cap also bounds discovery and Firecrawl jobs on these stores. Revisit both numbers in the limit calibration (#80).

MVP 04 implements the runtime retrieval boundary. MVP 05–06 implement the actual adapters and additional coverage. This task does not implement a shopping assistant or an end-to-end scraper.

## Seller quote capability to implement

The [buyer contract](buyer-product-contract.md) allows a seller-specific pre-order delivery quote, including a fee exposed during checkout. [Quote task #186](https://github.com/han33-dotcom/TechScout/issues/186) owns the scoped operation, its allowed inputs and the evidence binding the fee to the item, quantity and selected destination. A buyer selects which named sellers may receive a precise address. Stop before ordering, paying or reserving. The registry and workload limits apply to every quote request. The capabilities below describe the existing reader.

## Scoped retrieval capabilities

Review each operation and record it under `endpoint_capabilities` in the technical source
registry. A capability declares its ID and version, the source's canonical HTTPS host,
operation and response format, and the permitted path and query. The source must be
active and selected for live access, with technical review evidence and positive
workload limits. Capability IDs are unique within each source. A retailer directory
record can reference a capability, while the current registry controls dispatch.

| Field | Bounds |
|---|---|
| Operation | `search`, `product`, `variant`, `catalog`, `feed` or `sitemap` |
| Path | Ordered literal segments and optional typed segments after the first literal. Match the exact segment count and trailing slash. An empty path matches `/`. |
| Parameters | A length-limited ASCII token, an inclusive nonnegative integer range, or a finite choice list. Query values can also be length-limited ASCII search text with spaces written as `+` or `%20`. |
| Query | List every accepted key and mark optional keys explicitly. Unknown keys, duplicates, and missing required values are refused. |
| Format | `json`, `html` or `xml` as declared in the rule. XML covers sitemap and feed media types and is returned as capped bytes. Discovery code owns parsing and subsequent URL selection. |

The [offline source registry fixture](../tests/fixtures/source-capabilities.json)
shows search, variant detail, catalog pages, a feed and a sitemap. Supply the
configured reference when calling the existing gate.

~~~python
from app.source_capabilities import CapabilityRef

result = gate.fetch(
    budget, 'alpha', 'https://alpha.example/catalog/laptops?page=2&limit=10',
    expect='html', capability=CapabilityRef(capability_id='catalog-page', version='1'),
)
~~~

Call the same gate for each next page or discovered product URL using its corresponding
capability. Each redirect stays within the original rule or the original exact allowlist
and each followed hop is checked against the current registry and counted against the
same run budget. Public IP, pinned TLS, method, response size, timeout and workload
checks apply to all requests. Arbitrary URL templates, wildcard hosts, encoded paths
and known action routes are rejected. Existing `allowed_endpoints` still work for
calls without a capability reference. Live retailer registrations retain their reviewed
exact endpoints pending separate validation of broader routes.

## Automatic retailer onboarding

The [full-product plan](full-product-plan.md#53-automatic-retailer-onboarding-confirmed)
adds an agent that proposes and tests narrow technical capabilities for newly
discovered shops. [Retailer onboarding](retailer-onboarding.md) (#99) implements it:
- A short-lived probe goes through the source gate.
- Backend validation activates passing product and variant capabilities in the shared
  database and records their evidence.
- Teammates handle unresolved validation failures, such as shops that need a custom parser.

The reviewed file stays authoritative. `ActivationRegistry` adds activated capabilities to
each fresh registry read, so ordinary fetches still go through the same gate checks.

## Remaining candidates

Nine candidates are recorded in total. Abed Tahan, iSTYLE, Tech Zone, iStore Plus, KAYSTORE and Ayoub remain outside the initial three-source set pending technical work only. They are **not blocked on permission**. Their known data gaps—unpriced offers, variant identity, tax/fees or branch stock—can be addressed when expanding coverage.

## Price and fulfillment validation

Source access is ready. Final recommendations still need the later cost and eligibility work: selected variant, condition, stock scope, warranty, delivery charge, VAT and arrival evidence. Unknown mandatory charges cannot become zero. Generic online stock is not proof of pickup stock at a selected branch.

These are product-correctness requirements, not permission blockers or reasons to stop development. Missing facts should be carried as unresolved fields and handled by MVP 07–09 and 13.

## Outreach and test data

The previously created Gmail requests remain **unsent and no longer needed**. They are archived as a superseded approach; do not send them. No further retailer outreach is planned.

Six owned synthetic offers remain available in `tests/fixtures/synthetic_offers.json` for failure cases. They are optional development fixtures, not a substitute for the three selected public sources.

## Verification

```powershell
python -m unittest discover -s tests -v
```

Nine tests pass, including selection of Class, Mojitech and Beytech without permission emails, valid public review metadata, inactive/malformed-source rejection and revocation of a technical source configuration.
