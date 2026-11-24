# Domestic study evidence

Protocol 1.3.0 covers 23 domestic requests. The case texts, code, models, costs and order were frozen before the first dispatch. Each original automated slot uses its own PostgreSQL database and evidence directory. [Protocol](../../protocol.json) · [Run freeze](../../run-freeze.json) · [Warm corpus](../../corpus/warm.json)

The compressed JSON Lines file contains one record per submitted automated slot. Each record preserves the API polling snapshots, the worker receipt, the final buyer-visible outcome and the ledger entries for that run. Each polling sample records the state actually returned by the API. Database identifiers are consistently hashed and provider request identifiers are omitted. Acquisition and scheduled maintenance are separate from these run-level costs. All requests and clarification replies come from the frozen study dataset.

Run `python eval/results/domestic-1.3.0/summarize.py` from the repository root for configuration-level metrics. The generated counts distinguish source identifiers displayed in the application from registered sellers with actual outbound retrieval receipts. Unadmitted domains are reported separately. Censored latency percentiles and rates with an empty denominator use JSON null.

The 40 external slots retain their case IDs and submission flags. During H15 the study browser reached a security challenge at LebTech. One Souq showed an i7 laptop with 8 GB after a model-only reformulation of a request for an i5 with at least 16 GB. The two One Souq queries preceded the discovery of its restriction on automated access to the search page. The timestamps and screenshots are retained for descriptive reporting. The other 18 shopping-service slots and all 20 consumer-assistant slots are unmeasured. No price win, loss or tie can be established from these observations. [LebTech terms](https://lebtechlb.com/terms) · [One Souq access rules](https://onesouqlb.com/robots.txt) · [OpenAI terms](https://openai.com/policies/terms-of-use/) · [Anthropic consumer terms](https://www.anthropic.com/legal/consumer-terms)

The ledger snapshot includes the project usage at export, in USD. A receipt records its kind, source, status, creation time and the five ledger values in their original order. These values are source-call units, paid-call units, elapsed milliseconds, USD and external-call units. Uncertain charges retain the reservation amount. [Ledger implementation](../../../app/accounting.py)

The [study report](../../../docs/evaluation-report.md) gives the findings and their limits. The [configuration summary](summary.json) contains the denominators and timing distributions. The [paired comparisons](paired-outcomes.json) record the outcome of each scheduled pairing. The [deviations](deviations.json) preserve timing interruptions and access restrictions.

## Reproduce the results

Use Python 3.11 from the repository root.

```sh
python eval/freeze.py
python eval/results/domestic-1.3.0/verify.py
python eval/results/domestic-1.3.0/summarize.py
python -m eval.scoring eval/results/domestic-1.3.0/assessable-trials.jsonl --acquisition-usd 0.00001562 --maintenance-usd 0.00088698
```

These commands reproduce the published measurements offline. The original PostgreSQL dumps and evidence directories stayed on the study machine. Their frozen hashes identify what each trial restored. Live inventory, access decisions and responses may change on a later run.

The first three commands use only the standard library. The fourth uses the project dependencies. It scores the subset with an adjudicated primary case classification. Its missing-run fields count records excluded from that scoring input. The full dispatch inventory is in `trials.jsonl.gz`. The scorer input uses settled USD, with uncertainty carried separately in the configuration summary.

The case records preserve each operator query, source URL, observation time, access errors and decision. Browser captures include hashes of the visible text and screenshots. Selected public screenshots are included under `screenshots/retailer-checks/`. Page titles and catalogue results do not establish a valid offer. Prices, current stock, condition, fulfillment and mandatory costs must bind to the same item and purchase route.

The case classification is unresolved when an unavailable local comparison could change it. H06 has independent manufacturer evidence that its requested capacity does not exist. The conditional observed-methods analysis describes the methods that actually produced observations. Each case has equal weight across repetitions. The whole-cohort bounds allow each unresolved case to succeed or fail.
