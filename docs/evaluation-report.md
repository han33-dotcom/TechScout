# Domestic held-out evaluation

TechScout ran all 230 scheduled trials on 23 domestic requests. It abstained in 229 trials and timed out once. No trial displayed a matching product with a price or returned a verified recommendation. With zero selected offers, the study could measure neither offer validity nor a payable-price advantage. [Original traces](../eval/results/domestic-1.3.0/trials.jsonl.gz) · [Machine-readable metrics](../eval/results/domestic-1.3.0/summary.json)

The 23 domestic requests covered seven phones, seven laptops, three tablets, three monitors and three audio products. Fourteen used exact-product entry and nine described buyer needs. The requests included 14 in English, five in Arabic and four in Arabizi. [Case texts](../eval/heldout.json) · [Domestic scope decision](https://github.com/han33-dotcom/TechScout/issues/155)

## Results by configuration

Active time measures the buyer controller. Across the four configurations, 229 of 230 trials finished within the 120-second active limit. First-useful and verified-display latency percentiles were unreached. Every original API run has a matching worker receipt and its own PostgreSQL clone. [Freeze and order verification](../eval/results/domestic-1.3.0/verify.py)

| Configuration | Runs | Completed abstentions / timeouts | Active p50 / p95 (s) | Known run cost (USD) | Uncertain reservations (USD) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full system, warm | 69 | 69 / 0 | 27.3 / 45.2 | 0.065668 | 0.5365200 |
| Single agent, warm | 69 | 69 / 0 | 29.8 / 47.9 | 0.062452 | 0.5629600 |
| Fixed rules, warm | 69 | 68 / 1 | 27.4 / 68.4 | 0 | 0.030 |
| Full system, cold | 23 | 23 / 0 | 17.1 / 39.0 | 0.025342 | 0 |

A Windows Modern Standby interval interrupted H01, fixed rules, repetition two. The harness last polled at 39.5 seconds and next polled at 699.7 seconds. It cancelled the run and retained the timeout. A process-scoped execution request was added for the remaining trials. The original timing and cost are included. [Timing deviations](../eval/results/domestic-1.3.0/deviations.json)

## What the abstentions establish

The operator searched public sources for all 23 requests. Each search began during its first warm block or within nine minutes of the last automated run in that block. No checked offer established the exact item, condition, stock, fulfillment and all mandatory charges together. For 14 cases the planned evidence supports the protocol classification `no_match_observed`. This includes H06, where Apple lists no 64 GB iPhone 16 Pro. For the other nine cases the unavailable comparison sessions leave the primary classification unresolved. The remaining market outside these observations is unknown. Sources distinguish fresh browser captures from published pages returned through a web reader. Some search hits led to non-retailer pages or access failures. [Dated case observations](../eval/results/domestic-1.3.0/adjudications.json) · [Apple specifications](https://support.apple.com/en-us/121031)

The success rule credits a timely abstention on an adjudicated no-match case only if the buyer sees a relevant missing fact or blocking constraint. A bare count of zero offers receives no credit. The whole-cohort bounds count only timely, eligible runs as possible successes on unresolved cases. Confidence intervals in parentheses use 10,000 case-clustered bootstrap samples with seed 78. The study established no answerable-case denominator for useful-result success, recall or cost per successful request across the whole cohort.

| Configuration | Successful / assessable runs | Success on assessable cases (95% interval) | Whole-cohort bounds |
| --- | ---: | ---: | ---: |
| Full system, warm | 29 / 42 | 69.0% (47.6%–88.1%) | 42.0%–81.2% |
| Single agent, warm | 29 / 42 | 69.0% (47.6%–88.1%) | 42.0%–81.2% |
| Fixed rules, warm | 29 / 42 | 69.0% (47.6%–88.1%) | 42.0%–79.7% |
| Full system, cold | 12 / 14 | 85.7% (64.3%–100.0%) | 52.2%–91.3% |

## Retailer coverage

The runs identified 16 distinct sellers from the frozen directory and attempted to retrieve listings from five. Only Beytech returned a usable product read. No seller supplied a qualifying offer. The table gives the median and interquartile range per run, followed by the number of distinct sellers seen across that configuration. Discovered counts merge known domain aliases and exclude unadmitted leads. Attempted counts require an outbound retrieval receipt. [Directory identities](../eval/results/domestic-1.3.0/directory-identities.json) · [Per-run API and ledger evidence](../eval/results/domestic-1.3.0/trials.jsonl.gz)

| Configuration | Discovered | Attempted | Retrieved | Qualifying |
| --- | ---: | ---: | ---: | ---: |
| Full system, warm | 4 (3–5) / 13 | 3 (2–3) / 5 | 0 (0–0) / 1 | 0 (0–0) / 0 |
| Single agent, warm | 4 (3–5) / 13 | 3 (2–3) / 5 | 0 (0–0) / 1 | 0 (0–0) / 0 |
| Fixed rules, warm | 4 (3–5) / 15 | 3 (2–3) / 5 | 0 (0–0) / 1 | 0 (0–0) / 0 |
| Full system, cold | 4 (3–5) / 11 | 3 (3–3) / 3 | 0 (0–0) / 0 | 0 (0–0) / 0 |

Application counters include failed or out-of-scope source attempts and unadmitted domains. In the H20 full-warm first run, the interface reported five attempted sources. The ledger recorded retailer retrievals to three. No retrieved offer passed verification. All four methods produced zero additional verified sellers and zero price-comparable same-variant alternatives. The purchase flow gave no buyer a verified two-seller comparison. [Coverage definitions and source outcomes](../eval/results/domestic-1.3.0/summary.json)

The full warm system exposed stored candidates and attempted a direct listing recheck in all three warm repetitions of H29. A recheck records a listing submitted to the verifier even if source policy or the read failed. None established a currently payable offer. Decisive-fact freshness and selected-offer evidence integrity therefore have empty denominators.

The most frequent failed source outcomes were “Listing read unavailable” (337 source outcomes), “No matching SKU on the current listing” (210 source outcomes), “Listing URL is outside the active source scope” (106 source outcomes). A single retailer can contribute outcomes on successive runs.

### Illustrative cases

- H02 had a new, in-stock 256 GB Galaxy A56 listed at USD 400. The shop disclosed the exact Tripoli delivery charge only at checkout. [Retailer capture](../eval/results/domestic-1.3.0/screenshots/retailer-checks/H02.png)
- H15 had six units of the exact laptop in a retailer’s online inventory. The evidence did not establish a price, Beirut pickup-branch stock or card acceptance at that branch. [Retailer capture](../eval/results/domestic-1.3.0/screenshots/retailer-checks/H15.png)
- H24 had a 27-inch Samsung monitor listed at USD 210 excluding VAT. Stock and the delivered Tyre total remained unresolved. [Retailer capture](../eval/results/domestic-1.3.0/screenshots/retailer-checks/H24.png)
- H27 had a delivery estimate for the requested Sony headphones. The seller did not guarantee arrival by the buyer’s December deadline. [Retailer capture](../eval/results/domestic-1.3.0/screenshots/retailer-checks/H27.png)

## External comparisons

Each service retained ten predeclared domestic cases. H15 reached both public comparison interfaces during the paired block. LebTech presented a security challenge. One Souq showed 420 broad results for the full text and an i7/8 GB configuration after a model-only search for the requested i5/16 GB laptop. Both queries preceded discovery of its search-path restriction. The result is descriptive. The other 18 local-service slots and all 20 consumer-assistant slots are explicitly unmeasured. The verified plans were ChatGPT Pro and Claude Free. No study prompts were submitted to either consumer service. Human shopping, human effort and independent human verification were unmeasured under the approved agent-operated design. [Sessions and screenshots](../eval/results/domestic-1.3.0/external.json) · [LebTech terms](https://lebtechlb.com/terms) · [One Souq access rules](https://onesouqlb.com/robots.txt) · [OpenAI terms](https://openai.com/policies/terms-of-use/) · [Anthropic consumer terms](https://www.anthropic.com/legal/consumer-terms)

All 40 external price comparisons were inconclusive. There were zero wins, ties or losses and no sample for a savings estimate or sign test. In repetition one, neither submitted internal configuration had a valid selected offer in any of the 23 warm-versus-single-agent or 23 warm-versus-fixed pairs. The exploratory warm-versus-cold comparison yielded the same classification in 23 pairs. [Pair-by-pair outcomes](../eval/results/domestic-1.3.0/paired-outcomes.json)

## Buyer behavior and protocol limitations

The submitted structured fields survived the API round trip. The system asked no clarification questions in 230 trials. H08 and H19 each provided a clarification answer in the dataset. The system did not elicit it. On H29, nine trials displayed headphones as provisional or uncertain candidates for a buyer who explicitly required wireless earbuds. These appeared outside the main recommendations and failed the useful-display check. The success calculation flags those runs for the form-factor mismatch. The machine-readable tables separate fields already supplied by the dataset from behavior demonstrated by the buyer. [Interpretation checks by language](../eval/results/domestic-1.3.0/interpretation-checks.json)

The worker recorded 143 model calls. All 126 completed calls reported the frozen model version. Twelve transport errors and five model-call timeouts returned no model version. These call-level failures appear in 17 trials. The full configurations used the domestic planner, the single-agent configuration made sequential reads, and the fixed configuration used rules. Omitted model calls and refused discovery channels are visible in the raw traces. New leads went to background onboarding. No held-out fix or rerun is mixed into the results. [Frozen buyer settings](../eval/run-freeze.json) · [Protocol](evaluation-protocol.md)

## Cost and reproducibility

Across the automated trials, the shared ledger recorded USD 0.153462 in settled costs and USD 1.1294800 in uncertain reservations. Open reservations at export were USD 0. Source admission and bootstrap cost USD 0.00001562, and maintenance before the warm checkpoint cost USD 0.00088698. Including these fixed costs gives USD 0.15436460 known, or USD 1.28384460 with all unsettled amounts retained at their reservation values. The figures use the project’s configured rates and included allowances. They exclude unmeasured external-service infrastructure, human effort and laptop electricity. The closing project ledger held USD 4.37370574 against the USD 500 cap. [Closing ledger](../eval/results/domestic-1.3.0/ledger.json) · [Corpus receipts](evaluation-corpus-2026-11-24.json)

The warm and cold templates had the same 28 directory entries and schema revision 0009. The warm template held 16 documents, 68 observations, 50 passages and 48 embeddings. The cold template held zero of each. Both restored from verified dumps. All 230 original slots ran in their frozen sequence. The run freeze records the model routes, caps, budget reserve and code commit. Python 3.11.5 and Docker 28.4.0 ran on the primary Windows machine. The external observations used a logged-out Chrome 157.0.8103.79 browser. [Warm manifest](../eval/corpus/warm.json) · [Reproduction instructions](../eval/results/domestic-1.3.0/README.md)

The seven international requests H07, H09, H11, H18, H22, H26 and H30 were retired before collection. They contribute to no domestic denominator. The 27 development calibration submissions, the broader-discovery experiment and the planner smoke checks are separate from these results. [Original 30-case freeze](https://github.com/han33-dotcom/TechScout/tree/3be08f4ea285aa61c0dc4063cf3defacc6ebf255/eval) · [Development calibration](development-calibration.md) · [Study issue](https://github.com/han33-dotcom/TechScout/issues/82)
