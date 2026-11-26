# Domestic search design

This design describes the catalog and buyer search approved on November 23. The [buyer contract](buyer-product-contract.md) governs seller eligibility and quotes. The [experiment record](buyer-architecture-comparison.md) identifies what each implementation actually achieved.

## Catalog and maintenance

Store variants separately from sellers’ offers. Keep the URL, scope and timestamp for each observed fact. PostgreSQL handles structured filters, full-text search and pgvector retrieval. Reuse passages and embeddings when only a price changes. Three background jobs discover retailers, find new listings and refresh known offers. Record when a check fails so the previous observation keeps its original timestamp. [Bootstrap](catalog-bootstrap.md) · [Maintenance](catalog-refresh.md)

## 2. How the five discovery channels work

| Channel | Role |
|---|---|
| Tavily Search | Initial default for general discovery, using Lebanon targeting and queries in Arabic or English. |
| Google Search through SerpApi | An alternative for exact model codes, localized searches, and provider outages. |
| Brave Search API | An additional web index to explore uncovered sellers and products. |
| Manufacturer retailer directories | A regular background source of named shop leads, such as the ASUS and Lenovo Lebanon directories. |
| Firecrawl Search | A separately selectable web discovery tool, included in the background rotation and available for targeted buyer searches. |

Each connector returns leads with channel attribution. The controller enforces source scope, spend, cancellation and deadlines. Read known retailer URLs through the shared reader, which may use one configured Firecrawl Scrape fallback. A newly discovered shop enters background onboarding. [Discovery](discovery.md) · [Extraction](shared-extraction.md) · [Onboarding](retailer-onboarding.md)

## Buyer search

Look up the requested variant or needs in the catalog, then investigate facts that could change the decision. Recheck finalist price and stock on the selected listing. Compare equivalent configurations across eligible sellers using applicable charges. Surface useful partials with the exact listing and missing fact. A refinement carries the buyer’s feedback into a new revision. [Buyer acceptance scenarios](buyer-acceptance-matrix.md)

## Operating defaults at approval

| Decision | Operating default |
|---|---|
| New-retailer discovery | Run one sweep at startup, then daily, with extra targeted searches for important gaps. |
| New products at known shops | Scan a rotating batch every six hours. Tune each shop's frequency using its yield. |
| Existing offers and supporting facts | Agreed starting priorities are 12–24 hours for high-use offers and 3–7 days for others. Reuse specifications for up to 30 days and general policies for up to 7 days, with earlier checks for conflicts or expiry. |
| Buyer runtime | Existing ceiling is 120 seconds of active execution. Reserve about 30 seconds within that ceiling for final verification and writing. Finish earlier when evidence is sufficient. |
| Comparison depth | Two sellers is a search-effort target for price comparisons. Verification decides which offers can appear. |
| Resource limits | All channels share the existing collection, per-source, and project budgets. Provider rotation and agent parallelism draw from those same allowances. |

The 120-second ceiling above belongs to the original runtime design. The [buyer correctness protocol](buyer-correctness-acceptance.md) scores the displayed answer and reports timing separately. [OpenRouter configuration](openrouter.md) · [Full November design](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/domestic-search-design.md)
