# Development evidence, 26 November 2026

Both pinned buyer UIs selected real Galaxy A07s variants at Class Cellulars. Each buyer saw a USD 165 online listing with an incomplete payable total for delivery to Batroun. General web search and Firecrawl Scrape remain unverified. Neither buyer dispatched a successful general-search call. Issue #201 remains open and scoring has not started. [Issue #201](https://github.com/han33-dotcom/TechScout/issues/201)

## What each buyer saw live

| Development submission | Buyer-visible result | From submit to result | First offer display | External calls | Settled USD |
| --- | --- | ---: | ---: | ---: | ---: |
| Control, exact model | Zero verified options. The UI reported four attempted retailers and one successful read. [Screenshot](discovery-control.png) | 47.547 s | Unreached | 8 | 0.000912 |
| Agent, exact model | Asked whether the buyer meant Galaxy A07 or specifically A07s. The model had queried the manufacturer directory and found five retailer leads. [Screenshot](discovery-agent.png) | 65.031 s | Unreached | 12 | 0.0225645 |
| Control, product link | Light Violet, USD 165.00, online stock. Displayed as an uncertain alternative. [Screenshot](selection-control.png) | 45.391 s | 45.391 s | 5 | 0.000786 |
| Agent, product link | Black, USD 165.00, online stock. Displayed as an uncertain alternative. [Screenshot](selection-agent.png) | 77.719 s | 77.703 s | 14 | 0.0241710 |

The four submissions settled USD 0.0484335, with USD 0 held or uncertain. Calls include dispatched model, retrieval and search attempts. Local work and worker leases are itemized separately. Both UIs reported zero verified matching options. The displays identified gaps in condition, delivery eligibility, dispatch origin, applicable shipping and the pinned verifier's exact-configuration price check. The listed amount is the captured product price. [Timings, buyer text, actions, receipts and accounting](paired-results.json)

## Retailer evidence before and after selection

Class Cellulars served product 26090 with an initially empty color selector. The control selected Light Violet, variant 26096. Its final recheck fetched a fresh document, observed the empty selector again and selected 26096 again. Both observations returned a variation row with a display price of 165 and `is_in_stock=true`.

The candidate selected Black (26092), changed to Light Violet (26096), then returned to Black in the same retailer session. Both variation rows showed 165 and `is_in_stock=true`. The public events retain each before/after value, selected variant, source document SHA-256, source observation time and action time. The selector operated on the live retailer HTML inside isolated, network-restricted Chromium. The reviewed Store API supplied the independent parent/variation cross-check. [Retailer page](https://classcellulars.com/product/samsung-galaxy-a07s/) · [Recorded evidence](paired-results.json)

## Provider and capability check

| Tool | Control | Candidate |
| --- | --- | --- |
| ASUS manufacturer directory | Separate setup probe succeeded. The exact-model run planned a directory search, then its discovery worker failed before returning a search receipt. | Separate setup probe succeeded. The exact-model run received five leads and subsequently encountered source budget refusals. |
| SerpApi, Brave Search, Firecrawl Search | Unavailable before dispatch | Unavailable before dispatch |
| Tavily | Disabled after unsuccessful access preflight | Same |
| Firecrawl Scrape | Key/rate configuration unavailable | Same |
| Direct retailer reads, Chromium and live model | Exercised | Exercised |
| Interactive branch stock or pre-order delivery quote | No demonstration | No demonstration |

The effective general-search rate table was empty. The historical TechScout backend provider file belonged to a removed worktree. The current checkout supplied the model key, and the configured campaign ledger was reachable. A manufacturer directory, bounded onsite search and a pasted product link do not establish general web discovery. The remaining acceptance step requires restoring the approved provider account settings, rerunning both preflights and observing general search from both buyer UIs. [Backend settings and reproduction](../README.md)

Both products used a fresh copy of the same verified 23-retailer database, an empty offer/evidence/session state, the pinned low-effort model route, matching source policy and shared ledger limits. The recorded browser was Chromium 156.0.8078.4, headless build 1248. The two different served JavaScript hashes correspond to the two pinned buyer interfaces. The catalog digest, per-file adapter hashes, asset hashes, headless binary digest, model and source configuration hashes are in the [run receipts](paired-results.json).

The setup inputs were Honor X9d for Tripoli and Galaxy A17 for Jounieh. The A07s request for Batroun was set separately before observing its buyer results. Earlier A07s verification attempts are retained alongside setup runs and identified by phase. The final four live rows above used the immutable adapter and pinned worker entry point. No scored request, reserved request, grader reference or captured scoring answer was submitted or supplied to either buyer. [Earlier attempts and costs](setup-runs.json)

The 16 setup submissions settled USD 0.2465835 and retained USD 0.1228800 uncertain. Four earlier A07s verification attempts settled USD 0.0451315 with USD 0 uncertain. The source-only preflights used the shared campaign ledger. These development runs exercise selected-variant evidence and unknown charges in buyer scenarios 04 and 08. Full buyer acceptance remains open. [Buyer acceptance matrix](../../../../docs/buyer-acceptance-matrix.md)

## Independent replay

The independent collector used the common seed and separate development jobs. It recorded one successful directory retrieval, two subsequent directory budget refusals, the retailer document and its variation transitions. Its search and selection jobs used one and four retrieval attempts respectively, settling USD 0. The same frozen bank was passed to all replay submissions, with SHA-256 `226a9352c608e35e910c2c80585e0054cac23abd3cba06c8cae5e97d8974023d`.

An offline probe imported each pinned product separately. Both returned the same five retailer leads for the captured English directory query, with normalized response SHA-256 `1f920229e10440381f1d44a6353af6fe1fcd58f7e138424960c73cf36903c7c6`. Both moved from empty color to Light Violet, product 26090, variation 26096, on the identical captured document. Their independently launched buyer UIs chose different actions. [Direct adapter comparison](replay-parity.json)

| UI replay | Buyer result | Elapsed | External model calls | Settled USD | Uncertain USD |
| --- | --- | ---: | ---: | ---: | ---: |
| Exact model, control | Zero verified offers. Four uncaptured retailer reads missed explicitly. Its chosen directory query returned the recorded budget refusal. | 12.641 s | 1 | 0.000912 | 0 |
| Exact model, agent | Received five directory leads and asked for clarification. Three uncaptured requests missed explicitly. | 49.093 s | 7 | 0.0186365 | 0 |
| Product link, control | Replayed the Light Violet selection and displayed the USD 165 partial. | 26.297 s | 1 | 0.000826 | 0 |
| Product link, agent | Inspected the captured listing. The second model decision timed out before a selection. No offer displayed. | 20.016 s | 2 | 0.001894 | 0.0614400 |
| Product link retry, control | Replayed Light Violet and displayed the USD 165 partial. | 29.093 s | 1 | 0.000786 | 0 |
| Product link retry, agent | Replayed Black. Its next color transition and later search missed explicitly. No offer displayed. | 67.000 s | 8 | 0.0255290 | 0 |

Replay used no external data-provider or retailer traffic. The live model's unreported charge retained its full reservation. The ten live and replay submissions matched their durable ledger entries and were at least 61.029 seconds apart. The detailed receipt distinguishes metered call slots, which include replay deliveries, from dispatched external calls. [Originals and retry](paired-results.json)
