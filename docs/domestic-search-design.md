# TechScout domestic search design

**Approved 23 November 2026**

This design governs discovery and comparison of devices sold by Lebanese retailers and dispatched from inside Lebanon. The five categories are phones, laptops, tablets, monitors, and headphones/earbuds, with deeper initial coverage for phones and laptops. New condition is the default. The two pipelines share one catalog. Background jobs build and maintain it. Buyer searches use it to make a current, evidence-backed comparison.

## 1. What the catalog stores

Organize products by category, model, and exact variant. Link each seller's offer to that variant. Keep a separate retailer directory with the actual seller, aliases, supported categories, and the specific pages or operations TechScout can use. A retailer's ability to deliver to a buyer's city and evidence of dispatch from inside Lebanon are separate facts. A shop-wide policy applies only to listings it actually covers.

Use PostgreSQL for structured fields such as model code, configuration, price, currency, stock, condition, charges, and observation time. Keep the underlying source URL and evidence for each fact. Store useful specification, review, and policy passages with their original text. Use PostgreSQL full-text search and pgvector embeddings to retrieve those passages by wording or meaning. A price-only change adds a dated observation and reuses existing passage embeddings.

## 2. How the five discovery channels work

| Channel | Role |
|---|---|
| Tavily Search | Initial default for general discovery, using Lebanon targeting and queries in Arabic or English. |
| Google Search through SerpApi | An alternative for exact model codes, localized searches, and provider outages. |
| Brave Search API | An additional web index to explore uncovered sellers and products. |
| Manufacturer retailer directories | A regular background source of named shop leads, such as the ASUS and Lenovo Lebanon directories. |
| Firecrawl Search | A separately selectable web discovery tool, included in the background rotation and available for targeted buyer searches. |

The agent chooses another search channel when results repeatedly lead to known shops or leave a meaningful gap. The controller handles outages and rate limits within the shared budget. Exercise alternatives periodically even when the default succeeds. Merge aliases and duplicate leads. Attribute each discovery to its channel and compare additional usable domestic sellers, new same-variant comparisons, cost, and latency. Those measurements guide how often each channel runs.

**Firecrawl Scrape is a retrieval tool.** For a known, permitted URL, fetch directly first. If retrieval fails or returns a page without usable content, the shared reader can make one configured Firecrawl Scrape attempt within budget. For final price and stock checks, request fresh content and validate the returned page and selected variant. A lead from any discovery channel can follow this same path.

## 3. Ahead of runtime — build and maintain the catalog

1. **Start with an empty catalog.** Seed known shops and run a broader retailer search in parallel. Audit roughly 10–15 priority Lebanese retailers in depth and keep the wider inventory open. At startup, use the five categories, price bands, cities, and Arabic and English queries to give the agent a starting point.
2. **Schedule three jobs independently.** Discover new retailers, scan known retailers for new product URLs, and revisit existing offers for changes. A busy onboarding job should leave room for due refreshes. Track overdue work and reserve some background capacity for broad exploration.
3. **Assign one agent per discovery objective.** For example, find a competing seller for an exact laptop configuration. The agent can inspect coverage, write a query, compare the results with the catalog, and try a different provider, language, or query. Run separate agents for independent gaps. Stop when the objective is met, the results repeat without a promising next move, or the job reaches its time or spend allowance.
4. **Evaluate a new shop before using it.** Identify the seller and evidence for domestic dispatch. Test whether TechScout can discover new URLs through a catalog, feed, sitemap, or category page. Separately probe representative listings, including a variant case, for correct identity, price, currency, and stock. A shop may support known product URLs before its catalog can be explored. An LLM can propose samples or reading rules. Backend checks activate only the operations supported by evidence.
5. **Store and revisit useful findings.** Prioritize missing variants, underserved price ranges, and second sellers for popular configurations. Measure distinct configurations with a recent priced offer and with offers from two independent sellers. Record changed, unchanged, temporarily unavailable, confirmed removed, and unresolved outcomes. Preserve observation history and schedule retries or the next check.

## 4. At runtime — answer one buyer

1. **Interpret the request.** Recognize a retailer link, an exact model, or a needs-based request. Preserve hard requirements, preferences, domestic dispatch, delivery destination, and the distinction between a target budget and a hard ceiling. Ask for clarification only when the answer could materially change the search.
2. **Start with the catalog.** Use structured lookup for exact variants and searchable passages for suitability or policy questions. Historical price and stock help prioritize candidates. A plausible listing just above budget on its last check can still merit a fresh visit. Show stored candidates with their original timestamps while checks run.
3. **Investigate a decision-changing gap.** State which fact is missing, what it could change, and which source could settle it. Revisit a known listing or policy directly when possible. Use web search selectively when another model, seller, or source of evidence is needed. Up to three research workers can pursue independent questions and share results under one run budget. An evaluator can request one targeted follow-up for a material gap or conflict.
4. **Hand new discoveries to the right pipeline.** Check a new listing at a known shop immediately when its URL and operation are within the validated scope. Queue a new shop for background onboarding and continue with usable sources. Save reusable public facts with provenance. Keep the buyer's private request and preferences in the session.
5. **Verify and rank.** Reserve time to re-read finalist prices and stock for the exact variant during this run. Use applicable evidence for seller, condition, local dispatch, delivery to the destination, and all mandatory charges. Backend rules calculate the payable total. Recalculate or check a replacement when an offer changes. Missing decisive facts produce an unresolved alternative with the reason.
6. **Present the answer the buyer requested.** For a retailer link, verify that seller's offer. For an exact-model price comparison, aim to check two independent sellers when feasible and disclose how many were actually checked. One verified seller can still be shown. For needs-based searches, show up to three distinct qualifying models, explain the tradeoffs, and group sellers per model. The writer explains checked facts and links to the evidence. A buyer refinement creates a new request revision and rechecks affected decisions.

## 5. Approved operating defaults

| Decision | Operating default |
|---|---|
| New-retailer discovery | Run one sweep at startup, then daily, with extra targeted searches for important gaps. |
| New products at known shops | Scan a rotating batch every six hours. Tune each shop's frequency using its yield. |
| Existing offers and supporting facts | Agreed starting priorities are 12–24 hours for high-use offers and 3–7 days for others. Reuse specifications for up to 30 days and general policies for up to 7 days, with earlier checks for conflicts or expiry. |
| Buyer runtime | Existing ceiling is 120 seconds of active execution. Reserve about 30 seconds within that ceiling for final verification and writing. Finish earlier when evidence is sufficient. |
| Comparison depth | Two sellers is a search-effort target for price comparisons. Verification decides which offers can appear. |
| Resource limits | All channels share the existing collection, per-source, and project budgets. Provider rotation and agent parallelism draw from those same allowances. |

**Acceptance examples.** An empty catalog can grow to two locally dispatched offers for one exact variant. A later scheduled job independently discovers a new product, records a price change, and preserves the old observation after a timeout. A buyer run with good catalog coverage can verify directly with zero web searches. A one-seller comparison can trigger targeted discovery. A new shop becomes a background lead. Known retailer pages remain usable during search-provider outages.

## 6. Model access and delivery tasks

Use the backend `OPENROUTER_API_KEY` for all application and evaluation model calls, including discovery planning, onboarding proposals, buyer roles, passage indexing and semantic queries. Pin the intended inference route after checking account access. For embeddings, validate `openai/text-embedding-3-small` at 1,536 dimensions through OpenRouter for both indexing and queries. Model vendor names identify routes on OpenRouter. Search and retrieval services use their own service credentials. If the embedding route is unavailable, retain SQL and full-text retrieval and report the gap. Keep incompatible vectors in separate index versions. [OpenRouter embeddings API](https://openrouter.ai/docs/api/reference/embeddings)

The [full product plan](full-product-plan.md) defines the broader opt-in international workstream and shared resource ceilings. Delivery is tracked in the [backlog](https://github.com/han33-dotcom/TechScout/issues/36), including [discovery channels](https://github.com/han33-dotcom/TechScout/issues/145), [OpenRouter embeddings](https://github.com/han33-dotcom/TechScout/issues/146), [background scheduling and discovery](https://github.com/han33-dotcom/TechScout/issues/88), and [domestic buyer search](https://github.com/han33-dotcom/TechScout/issues/147).
