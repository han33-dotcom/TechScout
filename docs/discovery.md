# Domestic search and retrieval

Both buyer research and background collection use `DomesticDiscovery`. A search accepts a public query, a decision objective, a channel, a language (`ar` or `en`), and a result limit of 1–20. Include the relevant city, category, or exact model code in the query. The objective stays with the controller. Only the query and localization settings go to the selected service. A page read accepts an existing source ID, URL, reviewed profile, resource key, and processing version.

## Channel configuration

Set credentials and all-inclusive Decimal USD rates on the backend. Missing credentials or an invalid rate make that connector unavailable. The controller can still call the other channels. All four API connectors use direct HTTPS without redirects or automatic network retries.

| Channel | Connection and localization | Backend settings |
| --- | --- | --- |
| `tavily` (default) | Basic general search with Lebanon targeting, selected language, usage reporting, and snippets. [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) | `TAVILY_API_KEY`, `TAVILY_USD_PER_CREDIT` |
| `serpapi` | Google organic search with `gl=lb` and the selected interface language. Limits output locally to the requested count. [Google Search API](https://serpapi.com/search-api) | `SERPAPI_API_KEY`, `SERPAPI_USD_PER_SEARCH` |
| `brave` | Web results with the selected search language and Lebanon in the query. [Brave Web Search](https://api-dashboard.search.brave.com/api-reference/web/search/get) | `BRAVE_SEARCH_API_KEY`, `BRAVE_USD_PER_SEARCH` |
| `manufacturer` | One configured regional retailer directory per channel attempt. The source gate reads its exact permitted page. Only outbound shop links within the configured domestic section are accepted. | `TECHSCOUT_MANUFACTURER_DIRECTORIES` |
| `firecrawl` | Web search localized to Lebanon with page scraping disabled. Reserve two credits per ten requested results, rounded up. [Firecrawl Search](https://docs.firecrawl.dev/features/search) | `FIRECRAWL_API_KEY`, `FIRECRAWL_SEARCH_USD_PER_CREDIT` |

Configure manufacturer directories with a JSON array. Each entry needs `brand`, its admitted `source_id`, the exact HTTPS `url`, the Lebanese shop container's HTML `section_id`, and the seller website anchor's `link_class`. Review those selectors against the actual page before enabling it. A missing or changed section produces an explicit error. Returned shop URLs include the directory URL as provenance. They go through retailer onboarding and have no inherited fetch permissions. The [ASUS where-to-buy directory](https://www.asus.com/me-en/content/where-to-buy/) and the [Lenovo Lebanon locator](https://www.lenovo.com/lb/en/wtb/) are candidates. The checked-in registry contains no admitted manufacturer locator or production HTML selectors. The connector is available once a reviewed page and its selectors are configured. A dynamically rendered locator needs an accessible, reviewed representation before this HTML connector can use it.

The `read` operation uses the existing source gate, direct page reader, and variant-bound extraction. Set `FIRECRAWL_SCRAPE_USD_PER_CREDIT` with the Firecrawl key to enable one managed fallback for a permitted URL whose retrieval or extraction needs it. Scrape requests fresh content and validates the returned URL, response status, and freshness metadata. It works independently of the search connector and its key/rate configuration. [Shared extraction](shared-extraction.md)

## Execution and accounting

The planner can select a channel or revise its query and language after inspecting prior results. On a provider failure the buyer worker tries the configured alternatives, with at most three available channels in one search. A controller cancellation, deadline, or exhausted shared budget stops failover. Existing admitted pages can still be checked when search services are down.

The background command rotates channels even when the first one succeeds. Its output contains channel statuses, request IDs and scores when supplied, latency, reserved and reported cost, deduplicated public leads with provenance, onboarding outcomes, and the next cursor. Pass the returned cursor to the next sweep. One configured brand-matching directory is preferred when the query identifies a brand. Otherwise the manufacturer pages rotate across sweeps. Supply a stable maintenance job ID and public catalog objectives. Scheduled execution belongs to #88.

```text
python -m app.discover_retailers --job-id domestic-laptop-development --category laptop --language en --query "ThinkPad X16 Beirut Lebanon" --objective "Find a second independent seller" --cursor 0 --count 2
```

The shared ledger reserves each API request before dispatch. Each API connector permits one concurrent request, six per minute, and six per job across participating workers. Directory and product-page reads use the source registry's own limits. Missing usage or an ambiguous sent request retains the full quote, with actual cost unknown. An observed overcharge stops the campaign. Provider snippets stay separate from purchase evidence.

Use `report.coverage(verified_offers, baseline=already_verified)` to measure additional usable sellers and new same-variant seller pairs by channel. Each supplied offer must have current-run verification, a matching source and seller, and conflict-free item-specific evidence of dispatch inside Lebanon from the listing. Shop-wide delivery or shipping statements alone do not establish this metric. Comparison credit requires the discovered listing URL. A known domestic seller may be linked through a reviewed alias. Duplicate URLs retain each channel's provenance. Incremental credit goes to the first channel that adds coverage in the observed sequence.

Offline fixtures cover all five channel types, duplicate and aliased sellers, Arabic and English configuration, shared limits, missing usage, overcharges, failover, and a direct exact-variant price/stock read during search outage. Live provider availability, current directory markup, and marginal market coverage are operational checks for calibration in #80.

## Existing bounded retailer selection

`Tavily.search(request, job)` uses the [direct search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search),
with fixed basic depth, automatic parameter selection disabled, no generated answer
or raw page content, and at most 20 leads per search. An explicit Decimal USD/credit
rate is required. The shared ledger reserves each call before dispatch; failed or
ambiguous calls retain the quote. No implicit retries run. An explicit retry is a
new billed attempt. Reported overcharges stop the campaign.

The query preserves intent, category requirements, allowed conditions, and the
distinction between a budget target and maximum. Lebanon is the default country
boost. International search is opt-in. The country parameter is only a search
ranking hint: trusted retailer market checks still apply before retrieval.

`Discovery(provider, directory, gate).discover(request, job)` selects up to three
retailers by default (configurable to 20), considering search relevance, category
coverage gaps and whether a seller is already covered. Search is open-web; it is
not restricted to the audit's priority list or a fixed 15-store universe. The
bounded results can include any newly reviewed retailer present in the directory.

New/unresolved domains, marketplaces and aggregators remain leads. Register a
reviewed canonical retailer identity/market and technical source capability before
retrieval; discovery never writes an admission grant. Selection matches canonical
directory origins; alternate hosts require their own reviewed origin record.
Current release selection, endpoint scope and capability version are checked, then
the source gate repeats admission, public-network and workload checks at dispatch.

`DiscoveryReport` retains all leads and separately tracks known retailers as
unchecked, failed or checked. A refusal before any request stays unchecked; a
failed sent request counts attempted. A successful listing retrieval is checked,
but qualifying remains zero until downstream `VerifiedOffer` validation supplies
current-run price/stock receipts bound to that source and retrieved response hash.
Snippets have no evidence IDs and cannot become purchase facts. This module does
not rank offers or claim that the returned model satisfies every buyer constraint.

Offline tests exercise 20 synthetic sellers, three selected sources, newly reviewed
retailers beyond the initial sample, unknown/aggregator leads, opt-in, exhausted
budget, mid-run revocation, provider failures, overcharges and response bounds.
The domestic implementation and validation evidence above describe the current search interface.
