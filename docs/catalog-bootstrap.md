# Seed the evidence corpus

Apply the normal corpus migrations and load the active source policies into the product database. Load the existing shared accounting configuration. From the repository root run

    python -m app.bootstrap config/domestic-bootstrap-validation.json

Run the same command after interruption. Each manifest ID binds to its exact contents. The accounting database stores the lease, per-resource attempts, conservative reservations, response hashes and outcomes. The corpus stores the public captures and evidence. A completed page is skipped only when its corpus head and processing recipe still match. Inspect the returned page statuses, gaps and counts before planning another batch. A new manifest is still charged against the same campaign and bootstrap limits. Set the accounting job's cancellation flag to stop subsequent work.

Catalog seeds use the reviewed source capability and category slugs, including explicitly listed pagination URLs. Search hits outside those categories are discarded. Catalog names are product evidence. SKU-bound HTML and reviewed source policy statements use the shared extractor. To index passages, set the backend `OPENROUTER_API_KEY`. Supply the verified rate with `TECHSCOUT_EMBEDDING_USD_PER_MILLION` or pass `--embedding-rate` for this invocation. The same command resumes pending embeddings through OpenRouter without refetching completed pages. The report includes `semantic_gap` when the route is unavailable. Structured fields and keyword passages remain usable. [Routed index and accounting](evidence-services.md#openrouter-vector-index-146). Verification of finalist prices and stock belongs to the buyer run.

The November 18 Beirut sample acquired 30 product fields across five categories and one warranty passage from two retailers. The committed report records the source hashes and UTC times. Repeating it returned an identical report with no further calls. A separate domestic validation indexed three of those passages with text-embedding-3-small. The completed ledger receipt for bootstrap:domestic-vector-validation-20261118 recorded 66 tokens and USD 0.00000132. A fresh retrieval process found the seeded product evidence without a previous buyer run. Pending embeddings can be filled on resume within the same manifest allowance. International sources still require technical admission under issue #60. No stored listing is a verified purchase recommendation.

## Domestic exact-variant listings

The November 23 manifest binds the selected source product, source SKU, category and canonical directory retailer on each individual product URL. First populate the product database from the checked-in registry and seller directory. Use the existing shared accounting database and the configured OpenRouter route.

```sh
alembic upgrade head
python -m app.storage load-sources
python -m app.directory import-seeds
python -m app.bootstrap config/domestic-seller-coverage.json
python -m app.domestic_catalog --category audio --query "H30i" --limit 5
python -m app.domestic_catalog --coverage --as-of 2026-11-23T23:59:59+00:00 --recent-days 7
```

An exact listing seed uses `format: html`, `content: listing`, `product_id`, `sku` and `listing: {"retailer_id": "..."}` alongside the normal source, URL, category and optional capability. On each read the collector checks the active source and directory identity. A known product URL may have a direct-read grant. Enumeration requires a separate catalog/search/feed/sitemap capability or an admitted directory catalog URL.

A reviewed collection statement can supply dispatch evidence for one item when the same page explicitly lists its URL, product ID and variant ID. That seed uses the actual collection URL and `dispatch_for: {"retailer_id": "...", "listing_url": "..."}` with the same product and SKU. The collector keeps the collection's own document, URL, receipt and read time. If its latest version loses the explicit membership, the catalog drops that supporting claim. Each bootstrap manifest permits one seed per source URL. A collection read using a capability needs the corresponding capability reference in the seed.

The catalog queries the current documents in the shared corpus. Generic first-party Product metadata must select one seller offer for the reviewed SKU. Cross-seller matching uses a checksum-valid GTIN, a manufacturer plus MPN, or a reviewed source-specific model and complete options identifier. The dated report excludes conflicting identities and options from exact-variant counts. Counted sellers have independent canonical seller IDs and permission for the listing revisit. Catalog names captured previously remain available through evidence search.

Each result carries the observed positive price and currency, applicable stock, source URLs, field-specific timestamps and evidence IDs. Dispatch in Lebanon and delivery to a particular destination have separate fields. Missing dispatch, delivery, tax, shipping or stock stays explicit. An unknown buyer payable total cannot qualify a finalist.

Repeat the same manifest to resume outstanding indexing. A new bounded manifest ID requests another source check. On a price change the corpus preserves the original price observation and records the new one. Stable specification passages keep their IDs and routed vectors. From a fresh process, `DomesticCatalog(storage).lookup(category=..., listing_url=...)` returns historical candidates. `price_history(listing)` returns dated observations newest first. Buyer workers must acquire current-run evidence before ranking a verified offer.

## Domestic validation on 23 November 2026

The seven-day snapshot contained one recently priced, exact configuration and two independent local sellers for it. OutGeeked listed the black Anker Soundcore H30i at USD 20.00 at 19:20:40 UTC. WinCell listed it at USD 40.00 at 19:20:42 UTC. Both selected variants showed online stock. OutGeeked identified Beirut as its dispatch point on the product page. WinCell described delivery from its shelves in Kaslik on its Soundcore collection. The collection card contained the same product link, product ID and variant ID as the listing. The WinCell dispatch finding depends on that collection membership. Neither source supplied a complete payable total or confirmed delivery to a particular buyer address. [Dated receipts and source URLs](domestic-seller-coverage-2026-11-23.json)

The bounded seed also collected nine phone names, sixteen laptop names, three tablets and four monitors from Beytech. Two phone queries and two laptop queries supplied the deeper samples. These names support discovery. Only the two exact headphone listings contributed to the cross-seller count. The final manifest indexed all nine pages within each source's limits. Its predecessor reached Beytech's per-run allowance on a redundant seventh query. The final manifest omits that query and uses the verified headphone listings for audio coverage. [November 18 five-category sample](domestic-bootstrap-validation.json)

The first routed indexing pass used eight calls and 781 tokens. The fresh query used eleven tokens and retrieved a seeded passage through both keyword and vector search. The settled cost of those nine calls was USD 0.00001584. The unchanged replay yielded the same checkpoint, 46 observations, 34 vectors and zero additional requests. This database contained zero buyer sessions and zero buyer runs when the lookup returned both listings.

An isolated synthetic price-change demonstration used the live OpenRouter route. Initial indexing took one call and stored four vectors. Changing only the price from USD 100 to USD 125 added one dated observation, preserved the earlier price and reused all four vectors without calling the provider. That separate demonstration used 24 tokens and cost USD 0.00000048. [Usage and price history](domestic-seller-coverage-2026-11-23.json)
