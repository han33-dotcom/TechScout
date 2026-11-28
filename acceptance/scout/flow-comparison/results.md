# Buyer-flow comparison: results (28 November 2026)

Four buyer-flow designs answered the same 16 requests (`requests.json`), in the
listed-price scope (shop's listed price, before delivery and VAT). The scoring
rules (`rubric.md`) and the code were fixed and hashed before the first run
(`preregistration.txt`). Independent graders scored the 61 answers without
knowing which design produced which (`grades.json`; `key.json` maps the blind
codes to designs). The measured numbers are in `metrics.json`. The full run
reports are in `var/scout-eval/flow-compare/` on the machine that ran them.

| Design | What it is |
|---|---|
| A | One agent in a tool loop. |
| B | Pipeline: a planner agent, code searches and opens listings, verifier agents judge them in parallel, code writes the answer. |
| C | Team: an orchestrator agent directs a search agent and the verifier agents, then writes the answer. |
| D | B, but the search step reads a local copy of the shops' catalogs. Price and stock are still read live. |

**Measured before issue #273 changed the checker.** At the time, an offer counted as confirmed only
when new condition was proven. Under buyer contract v2 an offer whose condition the shop does not
state also counts as verified, so the "confirmed offer" row below understates every design by the
same rule. The code hashes in `preregistration.txt` are those of the measured version, not of the
code merged later.

## Result on the 15 requests all four designs answered

| | A | B | C | D |
|---|---|---|---|---|
| Confirmed card that was wrong (overclaim) | 0 | 0 | 0 | 0 |
| Answers with a misleading statement | 2 | 0 | 1 | 0 |
| Useful answers (graders) | 14 | 14 | 15 | 12 |
| Special cases passed (of 6) | 5 | 5 | 6 | 5 |
| Requests with a confirmed offer (of 11 where one is possible) | 5 | 7 | 7 | 7 |
| Lowest correct price found (of 13) | 10 | 8 | 11 | 8 |
| Shops with an in-stock match, average | 2.0 | 2.7 | 3.3 | 2.5 |
| Median time per request | 46 s | 31 s | 60 s | 30 s |
| Requests over 60 s | 2 | 1 | 8 | 1 |
| Model cost per request | $0.059 | $0.022 | $0.061 | $0.029 |
| Model calls per request | 3.2 | 3.8 | 6.6 | 3.7 |
| Requests sent to shops per request | 70 | 70 | 90 | 19 |

## Decision by the rules fixed beforehand

1. Honesty first: B and D had no overclaim and no misleading statement. A had
   two misleading statements and C had one. All three were in text a model
   wrote; the two designs whose answer is written by code had none.
2. Between B and D, B gave more useful answers (14 against 12), so B ranks first.
3. D is not adopted over B by the rule set for it (it lost two useful answers and
   was not 20% faster). Its one clear advantage is the load on shops: 19 requests
   per buyer request instead of 70.

**Ranking by the rules: B, D, C, A.**

## What the numbers do and do not show

- C found the most shops and the lowest price most often. The difference comes
  from its search agent choosing which listings to open, which worked better
  than B's code ranking. It is also the slowest (three more model calls in a
  row) and about three times B's cost.
- No design showed a wrong offer as confirmed. Every confirmed card the graders
  checked was the right product, new, in stock and at the listed price.
- Known defects seen in this run:
  - B, C and D showed a USD 12.50 copy of a Sony headphone (shop Corex) as a
    match, not confirmed. A did not.
  - B and D did not say that the buyer's pasted link could not be read.
  - A answered the out-of-scope console request with offers.
  - C fell back once when its verifier calls timed out.

## Limits

- Fifteen requests, one run per design: a difference of one or two requests is noise.
- The requests were written by the builder, who knew what shops list.
- The graders are language models.
- F08 is missing for A, B and C: the internet connection dropped during those
  three runs.
- From F09 on, several shops answered "slow down" (HTTP 429) to all designs,
  after about 25 minutes of back-to-back runs. F01 to F07 were unaffected; on
  those seven, the median times were A 49 s, B 36 s, C 68 s, D 37 s.
- D ran on a laptop short of memory, with the catalog held in Python memory.

## Changed after the comparison

The pipeline code in this folder's pull request differs from the measured version in four ways:

- It follows buyer contract v2 (issue #273): an unstated condition is a caveat, and the answer says so.
- It tells the buyer when a pasted link could not be read (the F15 defect above).
- Verifier calls start 1.5 s apart and have a longer time allowance. In a later trial run, four calls
  started together all timed out while queueing in the shared ledger, and their reservations were
  booked as uncertain spend (USD 0.54 for that run).
- A model call that fails in the ledger or the network is recorded in the report instead of ending the run.

These changes were checked with offline tests and four live trial runs, not with a new comparison.
