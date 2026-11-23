# Domestic development calibration, 23 November 2026

The buyer completed 27 real development submissions. All ended in abstention. The paired comparison covered 12 requests, the eleven domestic cases from the calibration set plus the black Soundcore H30i comparison. Each arm used a separate copy of the same corpus and the same shared, operational ledger. The order of the two arms alternated by case. Two further submissions measured sequential reads and one exercised a controlled discovery outage. No held-out request was submitted. [Per-request receipts, source outcomes and route settings](development-calibration-2026-11-23.json)

## Buyer results

| Configuration | Requests | Median active time | Longest | Checked seller occurrences | Qualifying offers | Settled USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Twelve audited priority shops, up to three independent readers | 12 | 0.883 s | 2.266 s | 0 | 0 | 0 |
| Broader gated discovery, up to three independent readers | 12 | 1.570 s | 8.281 s | 3 | 0 | 0.001834 |
| Broader discovery, sequential reads on two matched requests | 2 | 12.079 s | 13.016 s | 4 | 0 | 0.002796 |
| One-seller H30i corpus, all discovery transports disabled, live direct reads | 1 | 11.015 s | 11.015 s | 1 | 0 | 0.000774 |

The broader arm gained three checked seller occurrences across twelve requests. All twelve still lacked a qualifying result in both arms. There were zero additional qualifying models or same-variant payable comparisons. No best payable total was established in either arm. The cost of the broader arm increased by USD 0.001834. Median active time increased by 0.687 seconds. Each arm recorded 31 failed source outcomes. Failures included unavailable reads and pages without a matching SKU. The shared limiter remained active throughout this consecutive-request workload.

On the H30i request, sequential and parallel runs each completed five retrievals, one model call and two current seller checks. Active time was 11.141 seconds sequentially and 8.281 seconds with independent reads. On calibration case C12, the sequential run checked two sellers in 13.016 seconds and the parallel run checked one in 6.437 seconds. The source outcomes differed, so the C12 timing cannot isolate the effect of parallelism. The outage run checked one seller directly and retained the unresolved cost. Total buyer spend was USD 0.005404 with zero unresolved reservations.

## Discovery channels

Each category had the same exact-model and city objective expressed in Arabic and English. The ASUS directory returned ten successful reads with median latency 554.5 ms and zero provider charge. Its five distinct retailer identifiers recurred across the ten queries. The links were shop leads. None supplied a current payable comparison. The gate refused or could not configure the two known shop roots attempted per query.

| Channel | Query slots | Actual provider reads | Result |
| --- | ---: | ---: | --- |
| Manufacturer directory | 10 | 10 | Five distinct lead identifiers, zero qualifying incremental sellers or pairs |
| Tavily | 10 | 0 | Credential and account credit rate unavailable |
| SerpApi | 10 | 0 | Credential and account search rate unavailable |
| Brave Search | 10 | 0 | Credential and account search rate unavailable |
| Firecrawl Search | 10 | 0 | Credential and account search-credit rate unavailable |

The four unavailable channels have no measured latency, results or marginal market coverage. Their slots are recorded individually in the receipts. A fresh series can run just those channels once the backend credentials and rates are present.

## Storage, workload and budget

The laptop had an Intel Core i7-1360P, 12 physical cores, 16 logical processors, 15.73 GiB RAM and Python 3.11.5 on Windows. The warm application database contained 13 resources, 16 document versions, 68 observations, 50 passages and 48 embeddings. Each trial used a private SQLite copy and the project-wide PostgreSQL accounting service.

The original seed job took 19.014 seconds from creation to last metered completion, with nine retailer reads, eight embeddings and USD 0.00001562 billed. The revised seed job took 9.622 seconds, with nine reads and zero paid calls. The separate bounded refresh and discovery subsequently spent USD 0.00088698 across nine calls. It enumerated ten product URLs, extracted three new exact prices and queued three leads. These are fixed collection costs. Measured buyer savings per qualifying result are undefined because the cohort produced no qualifying total. [Seed results](domestic-seller-coverage-2026-11-23.json) · [Maintenance run](catalog-refresh.md)

Retain the 120-second buyer limit, 30-second final reserve, 40 external calls, USD 1 per buyer run and up to three independent readers. The two H30i reads benefited from concurrency with equal completed work. Keep startup/daily discovery, the six-hour rotating catalog scan and offer cadences of 12–24 hours and 3–7 days. The sample supports preserving exact-listing work after enumeration. It gives no evidence to justify more frequent full-corpus reads or higher source limits. Prioritize listing-specific condition, purchasability, dispatch, destination, tax and fee evidence. A larger pool of directory roots alone did not resolve those facts.

At 21:23 UTC the shared ledger accounted for USD 3.03707574 against the USD 500 allowance across devices. The ledger had zero recorded credit purchases. Each recorded OpenRouter call settled against routed usage. The 300-run maximum evaluation allowance still fits. Account funding and the campaign ceiling are separate quantities.

## Route and freeze

Protocol 1.1.0 was committed before the series. It pins `openai/gpt-6.1-sol` on OpenAI through OpenRouter for the three model-driven evaluation configurations. Automatic retries, fallbacks and model substitution are disabled. A validated development evaluator response reported the same model and provider. OpenRouter omitted the system fingerprint. The roles and writer configuration fingerprints were `5250196f5e947ff1` and `a5519600e0fd430f`. The receipts contain token counts, latencies and actual charges.

The issued key's metadata returned HTTP 200, a positive remaining allowance and a key-level cap. At 21:17 UTC the endpoint advertised structured outputs and tool selection. The short-context advertised rates per million tokens were USD 1 input, USD 0.05 cached input, USD 1.25 cache write and USD 5 output. The protocol used conservative reservation ceilings of USD 2, USD 0.10, USD 2.50 and USD 10. Settlement uses the reported charge. The routed OpenAI embedding model returned 1,536 compatible dimensions at USD 0.02 per million tokens. The indexed seven-token sample took 2.125 seconds and cost USD 0.00000014. The three-token query took 2.734 seconds and cost USD 0.00000006. Sparse compatible specification passages limit hybrid retrieval on the development corpus. [OpenRouter key metadata](https://openrouter.ai/docs/api/reference/limits) · [Provider endpoint metadata](https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model)

The machine-readable `freeze_inputs` contains the demonstrated model version, settings, pricing date, caps and route fingerprints for the eventual held-out pre-run freeze. Complete the pending channel measurements and release inputs before recording that freeze. The held-out study remains unopened. [Protocol](evaluation-protocol.md)

## Reproduce

Use a migrated domestic SQLite corpus, its matching evidence directory, the shared accounting environment and backend credentials. Replace the example paths with a local seed and fresh output directory. Each invocation below makes real, metered requests.

```sh
python eval/freeze.py
python -m eval.calibrate --database seed.db --evidence evidence --output trials --cases D-H30i C01 C02 C03 C05 C06 C07 C08 C10 C11 C12 C13
python -m eval.calibrate --database seed.db --evidence evidence --output trials --cases D-H30i C12 --arms broader --parallelism 1
python -m eval.channel_calibration --series configured-providers-01 --output channel-receipts --channels tavily serpapi brave firecrawl
```

The channel command uses the operator's configured application database and registry. Each series records stable job IDs and preserves dispatches with uncertain outcomes. For the controlled outage arm, use a separate corpus copy with one H30i seller paused and pass `--arms outage`. [Channel configuration](discovery.md)
