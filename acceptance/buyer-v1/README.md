# Independent buyer-request screening, 25 November 2026

The [buyer correctness protocol](../../docs/buyer-correctness-acceptance.md) calls for 20 independently classified cases. In this first pool, 12 of 25 requests had a useful partial answer, five had a demonstrable blocking constraint, and eight remained unclassified. The checked evidence established zero fully verifiable requests and zero complete two-seller comparisons. The cohort is **open**. These are market-reference observations; TechScout has not been scored on these requests.

| Reference class | Needed | Supported in this screen | Tentatively reserved |
| --- | ---: | ---: | ---: |
| Fully verifiable | 10 | 0 | 0 |
| Useful partial result | 5 | 12 | 5 |
| Known blocking constraint | 5 | 5 | 5 |
| Two independent, comparable sellers within the fully verifiable group | 5 | 0 | 0 |

## Candidate pool and evidence

The [25 buyer inputs](requests.json) cover all five categories. Each has a domestic delivery destination or pickup location. The pool includes model-name and needs-based entry. The prompts were committed at `f47746e` before retailer or manufacturer lookup for this screen. Treat every request in the pool as held back from product development. None of the 25 was submitted to the buyer app.

The [screening log](screening-2026-11-25.json) links every request to the observations supporting its classification. The log records 63 individual direct page requests, including five manufacturer checks for hard constraints. Of those requests, 49 returned a complete HTTP 200 body with a SHA-256 fingerprint. The remaining 14 returned an HTTP error/redirect or a transport failure. Nine additional primary pages were consulted through a web index. Their observation records identify the review date and access method. An indexed price or stock claim did not establish a fresh offer. Search-result snippets and retailer directories were used to locate sources.

The operator read public product and policy pages separately from the buyer application. Direct requests used a research user agent, a 2 MiB per-response bound, serial reads within each host and a pause between reads at the same host. Direct checks stopped at a host refusal. The screen performed no checkout, retailer messaging, source activation, or paid buyer run. Repeat the decisive reads near any eventual acceptance run because stock, price and policies can change.

## What kept cases open

- **Known charges can still leave the fulfillment route unresolved.** For B17, the ITWorks listing identifies the new, in-stock Samsung part, a USD 233.88 price and a USD 5 delivery rate. Its sales terms include applicable tax. Its public store and delivery pages establish a Lebanese shop and delivery to Tripoli. The reviewed evidence does not identify where that unit would be dispatched from. Two other shops had a checkout-calculated fee or a different regional part number. See B17 for the linked observations.
- **Conflicting fees matter even when the price and stock are clear.** For B06, LYNQED's exact silver MacBook variant shows USD 950, new and in stock. The collection promises free nationwide delivery. Both the variant's structured offer and the detailed shipping policy give USD 5 for Beirut. The policy also describes sourcing some inventory to order. The conflict and origin remain open.
- **Published facts can support a useful partial result.** For B08, the retailer documented the required keyboard, operating system, RAM and SSD, and showed a new laptop in stock at USD 833. It excludes VAT and calculates it at checkout. For B24, an online price for the selected black headphones did not establish availability in Hamra.
- **An unsuccessful search is insufficient to establish a blocker.** Manufacturer specifications resolve the five hard product contradictions. B16 remains unclassified because the priced candidate was an import, and B19 remains unclassified because the checked portable monitors were out of stock.

The five tentative partial slots are B01, B08, B11, B18 and B24, one from each category, including needs-based entry and pickup. Seven additional partial candidates remain in the screening log. The five known-blocked cases are B05, B10, B15, B20 and B25. The 25 prompts are a deliberately varied screening pool, with five specification-contradiction requests. The counts do not estimate the frequency of those outcomes for Lebanese buyers overall.

## Freeze status

[Issue #178](https://github.com/han33-dotcom/TechScout/issues/178) still needs dated reference evidence for ten fully verifiable requests, including five independently comparable seller pairs. Supplement the existing records when a source resolves a specific missing fact. For a new pool, commit buyer requests before checking retailer coverage and retain every screening outcome. Keep the development example in [#176](https://github.com/han33-dotcom/TechScout/issues/176) separate from all screened requests.
