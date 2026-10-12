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

`app/source_registry.py:get_active_source` validates configured source identity, review provenance, endpoint scope and positive execution limits. No permission-email field exists. Its selected-source bounds are our engineering defaults: six requests/minute, one concurrent request and four requests per shopping run. They control polling/workload, not admission negotiations.

MVP 04 implements the runtime retrieval boundary. MVP 05–06 implement the actual adapters and additional coverage. This task does not implement a shopping assistant or an end-to-end scraper.

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
