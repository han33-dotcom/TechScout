# Held-out evaluation protocol

Protocol version **1.1.0**, updated on 23 November 2026 for
[#78](https://github.com/han33-dotcom/TechScout/issues/78). It fixes what we will measure and
how, before anyone sees a held-out result. It contains no results and claims no performance.

Later tasks build on it:
- [#79](https://github.com/han33-dotcom/TechScout/issues/79) builds the runners and the scorer;
- [#80](https://github.com/han33-dotcom/TechScout/issues/80) calibrates on development cases;
- [#82](https://github.com/han33-dotcom/TechScout/issues/82) runs the held-out comparisons and publishes the results;
- [#76](https://github.com/han33-dotcom/TechScout/issues/76) and [#81](https://github.com/han33-dotcom/TechScout/issues/81) use the same case types for integration demos and buyer walkthroughs, but never the held-out cases themselves.

The scope follows section 10 of the [full-product plan](full-product-plan.md).

## 1. What is frozen and how to check it

| File | What it holds |
|---|---|
| [`eval/heldout.json`](../eval/heldout.json) | The 30 held-out requests |
| [`eval/calibration.json`](../eval/calibration.json) | 13 calibration cases, separate from the held-out set |
| [`eval/protocol.json`](../eval/protocol.json) | Metrics, denominators, verdicts, comparability, timing, statistics and change control, in machine-readable form for the scorer |
| [`eval/baselines.json`](../eval/baselines.json) | Configurations, subsets, repetitions, run order, timed procedures, the capture record and the assistant prompt |
| [`eval/models.json`](../eval/models.json) | Model access for the main evaluation and the rules for optional experiments |
| [`eval/corpus/cold.json`](../eval/corpus/cold.json) | The frozen empty-corpus manifest for cold runs |
| [`eval/corpus/warm.template.json`](../eval/corpus/warm.template.json) | The template for the pre-ingested warm-corpus manifest |
| [`eval/run-freeze.template.json`](../eval/run-freeze.template.json) | The template for the run settings frozen before held-out runs |
| [`eval/freeze.json`](../eval/freeze.json) | Version, SHA-256 hash of each frozen file, combined hash and changelog |
| This document | The rules in plain language |

Check the freeze from the repository root:

```sh
python eval/freeze.py
```

It prints a mismatch for any frozen file that has changed. `tests/test_eval_protocol.py` runs
the same check in CI. It also pins the version and combined hash, so changing a frozen file
without a recorded bump fails the build. Hashes ignore the difference between Windows and
Linux line endings.

The freeze has two stages:

1. **Protocol freeze (now, version 1.0.0).** It covers the cases, rules, subsets and
   procedures, the cold manifest and the templates.
2. **Pre-run freeze (at the code freeze, before the first held-out run).** The plan
   schedules the code freeze for 29–30 November.
   1. Build the warm corpus snapshot and copy `warm.template.json` to `eval/corpus/warm.json`.
   2. Copy `run-freeze.template.json` to `eval/run-freeze.json`.
   3. Fill every field in both files and set their state to `frozen`.
   4. Run `python eval/freeze.py --pre-run`. It records both hashes once and refuses a second recording.

Change control:
- **Before the pre-run freeze:**
  - A change needs `python eval/freeze.py --bump X.Y.Z "reason"`, a PR and the matching pinned values in the test.
  - Held-out texts may change only to fix an error unrelated to answers.
  - Nothing may change after anyone has seen a held-out output.
- **After the first held-out run:**
  - Nothing frozen changes.
  - A fix creates a new, labelled post-freeze version, and its results are reported apart from the original ones (plan, 3 December).
- **No peeking:** nobody runs TechScout, a baseline or a manual search on a held-out case before the pre-run freeze. Smoke tests, demos and walkthroughs use calibration or development cases.

## 2. The 30 held-out requests

The cases were written on 17 November 2026:
- **No runs:** TechScout and the baselines were not run on them.
- **No lookups:** no listing, price or stock was looked up for them.
- **Product knowledge:** product names come from general familiarity with the market.

Each case's `designed_outcome` is the author's design hypothesis, not an answer. Scoring
uses the class adjudicated at run time (section 4). The cases must never be copied into the
corpus, app fixtures, UI examples or development sets.

A test fails if any of these appears in `app/`, `config/`, `examples/`, `frontend/src/`,
`migrations/` or `tests/fixtures/`:
- a held-out request ID;
- a held-out link;
- a needs description;
- a non-English typed text.

English exact-model texts are plain product names that may legitimately appear in retailer
catalogs, so they are not checked. What must not happen is copying a case, or choosing
ingestion targets because of one; the warm manifest attests to this (section 11).

Every case has:

| Field | Meaning |
|---|---|
| `buyer_text` | What the buyer types, in English, Arabic or Arabizi |
| `request` | The v2 `ShoppingRequest` the harness submits. As in the buyer UI, the typed text goes in `intent.model` (exact model), `intent.url` (link) or `intent.description` (needs). The structured fields carry the constraints the buyer set in the editable filters. Every request validates against the v2 contract |
| `hard_constraints` | The checklist a verifier uses to decide whether an offer is valid, including constraints that exist only in the text |
| `interpretation_checks` | What the system must understand from the wording; used for the language metric |
| `clarification_answer` | The scripted answer to any clarification question (section 4) |
| `target_products` | The product an exact request means; empty for needs-based requests |
| `scenarios` | The traps and conditions the case exercises |

The mix:

| Dimension | Counts |
|---|---|
| Category | phone 9, laptop 9, tablet 4, monitor 4, audio 4 (phones and laptops are 60%) |
| Entry path | exact 17 (15 model names, 2 links), needs-based 13; every category has both |
| Market | domestic 23, international 7; every category has at least one international case |
| Language | English 20, Arabic 5, Arabizi 5; Arabic and Arabizi each have exact and needs cases |
| Designed outcome | answerable 20, uncertain 6, no-match 4 |

| Case | Category | Path | Market | Language | Designed | Scenarios | Product meant |
|---|---|---|---|---|---|---|---|
| H01 | phone | exact | domestic | en | answerable | variant_trap | Apple iPhone 17 Pro Max |
| H02 | phone | exact | domestic | ar | answerable | missing_cost_trap | Samsung Galaxy A56 5G |
| H03 | phone | exact | domestic | arabizi | answerable | variant_trap, availability_trap, hard_budget | Xiaomi Redmi Note 14 Pro 5G |
| H04 | phone | needs | domestic | en | answerable | needs_selection, missing_cost_trap, hard_budget | (needs-based) |
| H05 | phone | needs | domestic | en | uncertain | needs_selection, uncertainty | (needs-based) |
| H06 | phone | exact | domestic | en | no_match | no_match, variant_trap | Apple iPhone 16 Pro |
| H07 | phone | exact | international | arabizi | uncertain | international_route, missing_cost_trap, availability_trap | Google Pixel 10 Pro |
| H08 | phone | exact (link) | domestic | en | answerable | unsupported_link, clarification_expected | Samsung Galaxy A36 5G |
| H09 | phone | needs | international | en | answerable | needs_selection, international_route, missing_cost_trap | (needs-based) |
| H10 | laptop | exact | domestic | en | answerable | variant_trap, missing_cost_trap, hard_budget | Lenovo LOQ 15 |
| H11 | laptop | exact | international | en | answerable | variant_trap, international_route, missing_cost_trap | Apple MacBook Pro 14-inch (M5) |
| H12 | laptop | needs | domestic | en | answerable | needs_selection, missing_cost_trap, hard_budget | (needs-based) |
| H13 | laptop | needs | domestic | ar | answerable | needs_selection, condition_evidence | (needs-based) |
| H14 | laptop | needs | domestic | arabizi | answerable | needs_selection, hard_budget | (needs-based) |
| H15 | laptop | exact | domestic | en | uncertain | variant_trap, availability_trap, missing_cost_trap, uncertainty | HP ProBook 450 G10 |
| H16 | laptop | exact | domestic | en | no_match | no_match, hard_budget | ASUS ROG Zephyrus G16 |
| H17 | laptop | exact | domestic | ar | answerable | variant_trap, missing_cost_trap | HP Victus 15 |
| H18 | laptop | needs | international | en | answerable | needs_selection, international_route, missing_cost_trap | (needs-based) |
| H19 | tablet | exact (link) | domestic | en | answerable | clarification_expected, variant_trap | Apple iPad Air 11-inch |
| H20 | tablet | exact | domestic | en | answerable | variant_trap | Samsung Galaxy Tab S10 FE |
| H21 | tablet | needs | domestic | arabizi | no_match | no_match, hard_budget | (needs-based) |
| H22 | tablet | needs | international | en | answerable | needs_selection, international_route, missing_cost_trap | (needs-based) |
| H23 | monitor | exact | domestic | en | answerable | variant_trap | Dell UltraSharp U2724D |
| H24 | monitor | needs | domestic | en | answerable | needs_selection, missing_cost_trap | (needs-based) |
| H25 | monitor | needs | domestic | ar | no_match | no_match, hard_budget | (needs-based) |
| H26 | monitor | exact | international | en | uncertain | availability_trap, variant_trap, international_route | LG 27UP850-W |
| H27 | audio | exact | domestic | en | uncertain | uncertainty | Sony WH-1000XM6 |
| H28 | audio | exact | domestic | ar | uncertain | uncertainty, variant_trap | Apple AirPods Pro 3 |
| H29 | audio | needs | domestic | arabizi | answerable | needs_selection, currency_conversion, missing_cost_trap, hard_budget | (needs-based) |
| H30 | audio | needs | international | en | answerable | needs_selection, international_route, missing_cost_trap | (needs-based) |

The two link cases test link handling without looking up a retailer listing:
- **H08, a social-media shop post.** This source type is common in Lebanon but unsupported, so the system must say so and ask what the product is.
- **H19, a manufacturer product-line page.** The page names a line, not one model, so the system should identify the line and ask for the size or keep sizes apart.

A retailer product link is exercised by calibration case C13 (a Mojitech listing already in the store fixtures) and by regression fixtures.

## 3. Calibration and development cases

[`eval/calibration.json`](../eval/calibration.json) holds 13 cases for scorer checks (#79) and
calibration runs (#80). They reuse inputs the team already develops with:
- the iPhone 17 example;
- a Mojitech iPhone 17 listing from the store fixtures, as a retailer-link case;
- the identity-fixture phones and laptops;
- the UI's Arabic and Arabizi presets;
- the plan's engineering-laptop walkthrough;
- the v2 request fixtures.

They cover all five categories, both entry paths (including a retailer link), international
scope, all three languages and a no-match case. Tests check that they share no case ID,
request ID, typed text, link or target product with the held-out set.

#80 may add more development cases as JSON files under `eval/development/` without a version
bump. The same overlap test checks them. Tune prompts, budgets and sources on these cases
only.

## 4. Running a case and judging its outcome

**Submission.**
- **Request ID:** each run submits the case request with the ID
  `<request_id>-<configuration>-r<repetition>` and revision 1. Nothing else changes.
- **Clarifications:** the harness answers each clarification question with the case's
  `clarification_answer`. Without one, it answers "No other requirements. Please continue
  with what I gave you." After two answers it sends that default text.
- **Polling:** the harness polls every second.
- **Wall-clock limit:** at 180 seconds of wall-clock time it cancels the run and scores the latest snapshot.
- **Active-execution limit:** results published after 120 seconds of active execution do not count.

**Adjudicated class.** Each case is scored by what was actually found during its evaluation
window, not by its designed outcome:

- **answerable:** at least one valid offer meeting every hard constraint was found by any
  method. That includes any configuration or repetition, any baseline, or the verifier's own
  10-minute search in the case's first block.
- **no_match_observed:** no method found one.

A designed no-match case that turns out answerable, or the reverse, is listed in the results
with the evidence.

**Offer verdict.** The verifier checks every decisive fact at the retailer:
- identity (every hard identity field);
- condition;
- price and currency;
- retailer-reported stock for the requested fulfillment (pickup stock must be for the pickup location);
- the delivery or pickup charge for the case destination;
- other mandatory charges;
- payment method, warranty and deadline basis when the case requires them;
- the seller's market.

An offer is **valid** when all of these hold and the presented price and total match.
Otherwise it is invalid with one or more reason codes from `eval/protocol.json`. Two rules
are conservative on purpose:

- **Unverifiable offers count as invalid.** An offer the verifier cannot check (blocked
  page, bot check, login wall) counts as invalid in the primary rate. A labelled sensitivity
  rate excludes it.
- **Price drift counts as invalid.** If the run's own evidence showed the presented price
  but the live page differs at verification, the code is `price_drift`. It counts as
  invalid and is reported separately.

Verification never logs in, adds to a cart, starts checkout, contacts a seller or bypasses a
bot check. A cost shown only at checkout stays "not shown".

**Who verifies what.** A teammate other than the operator verifies, where possible, within 60 minutes:
- **Repetition 1:** every selected offer of every main recommendation is checked by hand.
- **Repetitions 2 and 3:** an automated re-observation checks every selected offer, and a person checks a random 20% sample (seed 78).
- **Additional sellers:** sellers listed under a recommendation get automated re-observation plus the 20% human sample in every repetition, and are reported as a secondary validity figure.

## 5. Metrics and denominators

Full definitions are in `eval/protocol.json`. Every published rate states its numerator and
denominator.

| Metric | Unit | Numerator | Denominator |
|---|---|---|---|
| `valid_offer_rate` | offer | Selected main-recommendation offers judged valid by hand | All selected main-recommendation offers in repetition-1 runs over all 30 cases (at most three per run), unverifiable ones included |
| `request_success_rate` | run | Runs with the right outcome: a valid offer and no invalid one on answerable cases; a reasoned abstention with no invented offer on no_match_observed cases; within time; no constraint or scope violation | All runs, including errors, timeouts and cancellations |
| `useful_result_success_rate` | run | Successful runs on answerable cases | All runs on answerable cases. Abstaining there is a failure, so abstention cannot raise this rate |
| `abstention_rate` | run | Runs with no main recommendation | All runs; also split into correct (no_match_observed) and unnecessary (answerable) abstention. Runs ending in error, timeout or cancellation are also counted separately |
| `answer_coverage` | case | Answerable cases with a valid offer in repetition 1 | Answerable cases |
| `pooled_offer_recall` | offer | Distinct valid seller offers the configuration found | Distinct valid seller offers found by any method (exploratory) |
| `retailer_stage_counts` | retailer per run | Discovered, attempted, retrieved and qualifying retailers (definitions below) | Per run; median, interquartile range and union over the set, domestic and international separately |
| `multi_seller_products` | run | Runs with at least one exact configuration offered by two or more distinct sellers, all verified and price-comparable | Runs on answerable cases |
| `first_useful_display_seconds` | run or session | Time to the first display of a matching product with a price; provisional candidates count | All runs; never-shown runs are censored |
| `verified_result_seconds` | run or session | Time to the first verified main recommendation (TechScout) or to the operator confirming every decisive fact (baselines) | Runs on answerable cases; censored as above |
| `time_limit_compliance` | run | Runs within 120 seconds of active execution | All automated runs |
| `cost_per_successful_request` | USD | Marginal spend of all held-out runs, failed ones included | Successful runs; corpus acquisition and maintenance cost reported as a separate fixed figure |
| `evidence_integrity_rate` | offer | Selected offers whose decisive claims each cite a resolvable supporting excerpt, with current-run price and stock evidence | Selected offers in repetition-1 runs |
| `constraint_preservation` | run | Runs with no silent relaxation and no scope violation (target: all runs) | All runs |
| `paired_price_outcomes` | case pair | Win, tie, loss, techscout_only_valid, baseline_only_valid, neither_valid, not_comparable, inconclusive | Every planned pair |
| `interpretation_accuracy` | check | Interpretation checks satisfied | All checks for the language group, reported separately for English, Arabic and Arabizi |
| `clarification_count` | run | Clarification questions asked | Per run and case |
| `international_capability` | run | Domestic results retained, foreign offers by route and cost state, and wrongly promoted or wrongly "cheaper" foreign offers | Runs on the 7 international cases |
| `external_outcome_distribution` | session | Answered, no_results, unsupported_feature, access_failure, timeout | All planned sessions of each service |

**The 95% target.** The valid-offer target is at least 95%:
- **Met:** the point estimate is at least 0.95.
- **Met with confidence:** the 95% interval's lower bound is also at least 0.95.
- **Not assessed:** the denominator has fewer than 20 offers. The rate is still reported.

The valid-offer rate is always published beside useful-result success, abstention and answer
coverage, so a system that abstains on most requests cannot look successful.

**Retailer stages.** These follow `CoverageCounts`, so the counts can only fall from one stage to the next:
- **Discovered:** a distinct canonical seller identified as a candidate for the run's category and market scope.
- **Attempted:** at least one request-relevant retrieval was dispatched through the source gate in this run.
- **Retrieved:** a usable response produced a product record in the requested category.
- **Qualifying:** at least one offer passed eligibility and current-run verification.

Retailers consulted only through stored corpus evidence are reported separately as
`corpus_consulted`.

**Latency.** p50 and p95 are reported:
- **Never shown:** runs that never show the display are ranked slower than every observed time.
- **Not reached:** a percentile is "not reached" when such runs cover it.
- **Clarification wait:** waiting for the scripted clarification answer counts in wall-clock latency but not in active execution.

## 6. Price comparability

Two offers are price-comparable only when:
- **Same configuration:** `app.identity` confirms hardware equivalence on every identity field the category requires.
- **Same condition:** a refurbished offer never wins against a new one.
- **Same basis:** both totals cover the case fulfillment (delivery to the case city, or pickup) and every mandatory charge.
- **Both verified:** both are verified payable totals. An estimated range or incomplete subtotal never wins against a verified total.
- **Same pass:** both were verified in the same verification pass (section 9 defines the pass for paired sessions).

Further rules:
- **Currency:** totals in different currencies are converted with one reference rate per pass, the published Banque du Liban rate with its URL and time. Without a recorded rate the pair is inconclusive.
- **Warranty:** differences are listed with the pair and do not block comparison.
- **Ties:** a difference of at most the larger of USD 1.00 and 0.5% of the lower total is a tie.
- **Needs-based requests:** different models are never price-compared. Model fit and task success are reported separately from identical-item price outcomes.

## 7. Timing rules

| Rule | Value |
|---|---|
| Active execution per run | 120 seconds (plan section 6) |
| Harness wall-clock limit per run | 180 seconds, then cancel and score the latest snapshot |
| Poll interval | 1 second |
| Verification after the presenting run | within 60 minutes |
| Paired session (TechScout and local baselines on one case) | sides run one after another within 90 minutes (worst case about 60); the verification pass ends within 60 minutes after the last side |
| LebTech or One Souq service phase | at most 8 minutes |
| Verification phase for LebTech, One Souq and assistant sessions | at most 10 minutes, logged separately as extra buyer effort |
| Manual shopping | at most 20 minutes in one phase, verification included |
| Assistant reply phase | at most 8 minutes, including one follow-up |
| Verifier's own search per case | at most 10 minutes |
| Dated case H27 | void if run after 24 December 2026, 18:00 Beirut time |

Timestamps are ISO 8601 with an offset; durations come from one monotonic clock per session.

## 8. Configurations, repetitions and run order

| Configuration | Cases | Repetitions | Corpus |
|---|---|---|---|
| `full-warm`: orchestrator, up to three workers, evaluator and writer | all 30 | 3 | warm |
| `single-agent-warm`: one agent with the same tools, evidence, verifier, cost engine, budgets and base model | all 30 | 3 | warm |
| `fixed-heuristic-warm`: the existing rule planner's fixed search, filter and recheck path | all 30 | 3 | warm |
| `full-cold`: the full system from the empty corpus | all 30 | 1 | cold |
| `lebtech`, `onesouq`, `manual-shopping` | local subset (10) | 1 | external |
| `chatgpt-browsing`, `claude-browsing` | assistant subset (12) | 1 | external |

The automated configurations share everything except the agent design:
- the code commit;
- a copy of the same corpus snapshot;
- the same caps, base model, adapters, source gate, eligibility and cost engine, and final verifier.

External services and assistants have their own unknown corpus, compute and cost. They are
never called resource-equivalent, and no internal cost is invented for them. The existing
`no-investigation` baseline stays a development tool. A WebMall subset is optional and must be
declared in its own frozen file before it runs. It is never mixed into held-out metrics or
called an official benchmark score.

**Budget.** The 300 automated runs (270 warm, 30 cold) times the per-run spend cap are the
worst case. If that exceeds the remaining allowance minus the demo reserve, reduce
repetitions in this order:

1. The fixed heuristic goes to 1 repetition.
2. The full and single-agent configurations go to 2 repetitions together.
3. Stop and ask the team.

Cold runs are never dropped, and cases are never cut silently. Any reduction is recorded in
`eval/run-freeze.json` before the first held-out run.

**Order.**
- **Blocks:** runs go in case blocks. A block is one case's runs of every warm configuration, back to back, then one verification pass.
- **Block order:** a seeded shuffle, using seeds 78, 79 and 80 for repetitions 1–3 and 178 for the cold pass. The resulting lists are in `eval/baselines.json`, and a test re-derives them.
- **Configuration order:** the three warm configurations rotate by block. In a paired session,
  full-warm takes the TechScout slot and the other two run unattended right after it.
- **Sequence:** warm repetition-1 blocks (including the paired sessions), then the cold pass,
  then warm repetition 2, then warm repetition 3.
- **Cold runs:** they use their own empty-corpus databases.
- **Repetitions:** they measure variation. Results are averaged per case first, and intervals resample cases.

## 9. LebTech, One Souq, manual shopping and browsing assistants

**Local subset (predeclared, domestic only).** H01, H02, H03, H04, H06, H10, H12, H15, H17,
H24. Nine of the ten are phones or laptops. They cover:
- exact-variant traps (H01, H03, H06, H10, H15, H17);
- needs-based selection (H04, H12, H24);
- missing-cost traps (H02, H04, H10, H12, H15, H17, H24);
- availability traps (H03, H15);
- one designed no-match (H06);
- Arabic (H02, H17) and Arabizi (H03) inputs.

LebTech, One Souq and manual shopping run on this subset. **Assistant subset:** the local
subset plus H11 and H30, for browsing-enabled ChatGPT and Claude, because assistants can
attempt international purchases. H11 and H30 are scored only in the international capability
table (section 10), never in paired price outcomes or domestic success comparisons.

**Paired sessions.** For each local case, the four sides run one after another, within 90
minutes:
- **TechScout:** the `full-warm` repetition-1 run, at most 3 minutes. The single-agent and
  heuristic runs follow it unattended.
- **LebTech and One Souq:** each a service phase and its own verification phase, at most
  8 + 10 minutes.
- **Manual shopping:** at most 20 minutes.

The worst case is about 60 minutes. The order comes from a balanced Latin square (listed in
`eval/baselines.json`): each side takes each position once and follows each other side once
across every four cases.

**Verification pass.** The independent verifier checks each side's selected offers as soon
as that side finishes, each within 60 minutes. These checks form the case's verification
pass, which ends within 60 minutes after the last side. Paired price outcomes use the totals
from this pass, not the operators' own verification phases (those measure buyer effort).

**Operators.** Operators are pseudonymous. Where possible, each external side has a
different operator, and nobody looks at TechScout output for a case before
finishing their own session on it.

**Inputs.** Every side gets the same inputs:
- the case `buyer_text`;
- a fixed-requirements sentence rendered from the request fields;
- the destination.

They are stored per case in `eval/baselines.json`.

**Timed procedure for LebTech and One Souq.**

1. **Setup:** use the public website, logged out, with no paid account. Start the timer when the home page has loaded.
2. **Search:** type the buyer text exactly. If nothing relevant appears, reformulate using
   only the case inputs (for example the English model name). Log every query.
3. **Filters:** apply the service's own filters. Record each requirement it cannot filter as
   `unsupported_feature`. For a needs case, choose only from what the service suggests or
   filters.
4. **First useful display:** record the time of the first result page with a matching product and a price.
5. **Service phase (at most 8 minutes):** choose up to three offers. Record what the service
   shows: retailer, link, configuration, condition, price, stock, delivery cost and warranty.
   Record missing fields too.
6. **Verification phase (at most 10 minutes, logged separately):** follow the links to the
   retailer pages and confirm the decisive facts. Record the time every decisive fact of the
   chosen offer is confirmed, or that it was not reached.
7. **Outcome:** record `answered`, `no_results`, `unsupported_feature`, `access_failure` or `timeout`.
   Retry an access failure once within the paired session.

**Manual shopping** uses a search engine and retailer sites for at most 20 minutes in one
phase (verification included), with the same records.

**Assistants:**
1. Start a new chat with web search on and memory and custom instructions off.
2. Record the product, plan tier and visible model name (or `not_shown`).
3. Send the frozen prompt template, then send the single follow-up template once.
4. Verify at the retailer as above.

**Capture record.** `eval/baselines.json` defines one JSON record per session. It holds:
- the session, case and side, operator pseudonym, interface and version;
- timestamps, queries and filters;
- the results viewed and the selected offers, with their missing fields;
- the verification steps and the outcome;
- sanitized screenshots.

#82 stores these under `eval/results/` without personal or account data.

## 10. Domestic comparison versus international capability

The 23 domestic cases form the common comparison. Every LebTech, One Souq and manual
comparison, and every paired price outcome, uses domestic cases only, with matched
constraints. The assistants' two international cases (H11 and H30) count only in the
capability table below.

The 7 international cases (at least one per category) are reported as a separate capability
table (`international_capability`). It shows, per run:
- whether domestic results were kept;
- which foreign offers were found, by market and route type;
- the cost state of each foreign offer;
- any estimated or incomplete offer wrongly promoted or wrongly called cheaper.

**Outcomes.**
- **No invented outputs:** unsupported features, access failures and no-results are recorded
  as explicit outcomes with their own denominators. They are never replaced by invented
  outputs.
- **No automatic wins:** LebTech or One Souq not offering international search is a
  capability note, not a TechScout win.
- **Superiority claims:** they need paired wins on comparable valid pairs.

**Default scope and constraints.**
- **Domestic cases:** keep `include_international` off (a test enforces it). Any foreign
  purchase offer on a domestic case is a scope violation.
- **International cases:** must keep available domestic results.
- **Buyer constraints:** a silently relaxed constraint is a violation for every side,
  baselines included.

## 11. Corpus isolation

**Cold runs.** They start from the frozen empty manifest
([`eval/corpus/cold.json`](../eval/corpus/cold.json)):
- a fresh database at the frozen commit;
- the same retailer directory and source capabilities as the warm runs;
- no documents, observations, passages or embeddings;
- zero acquisition cost.

**Warm runs.** They start from the pre-ingested snapshot described in `eval/corpus/warm.json`.
That file is filled from the template before the first held-out run. It records:
- corpus and index versions, processing versions (extraction, chunk, embedding model and dimensions);
- the snapshot time, commit, migration revision, and dump and evidence-file hashes;
- each source's market, categories, counts and observation dates;
- bootstrap and maintenance acquisition costs from the ledger;
- the time background writes were suspended.

**Isolation.**
- **Background writes:** the background ingestion worker is stopped for the whole evaluation.
- **Separate copies:** every run starts from its own copy of the snapshot (a restored dump or
  a PostgreSQL template-database clone). Whatever a run writes stays in that copy for audit
  and never reaches the snapshot or another run, so no run benefits from an earlier one.

**Contamination rules.** The warm manifest must attest that:
- no held-out answers or outputs entered the corpus;
- no baseline outputs entered it;
- no verifier findings entered it;
- no held-out case text was used to choose ingestion targets;
- the snapshot was made before the first held-out run.

A test checks these values when the file exists.

## 12. Model access

[`eval/models.json`](../eval/models.json) freezes the main evaluation:
- **Access path:** OpenRouter chat completions pinned to the OpenAI serving provider.
- **Model:** `openai/gpt-6.1-sol`, for every agent role.
- **Same model:** the single-agent baseline and cold runs use the same model.
- **Embeddings:** OpenRouter `openai/text-embedding-3-small` at 1,536 dimensions.

A test checks that `config/evaluation.json` and `app.retrieval` still name these. Automatic
model substitution, provider fallbacks and automatic retries are off.

**Pre-run freeze.** It records the run-level settings: effort, token limits, timeout, prompt
version, pricing date and caps. It also records the model version the API reports in the
bounded smoke check.

**Per-call record.** Every call records:
- the requested and reported model;
- the provider, endpoint and service tier;
- tokens, latency, retries and fallback use;
- the cost.

A field the provider does not return is written `not_reported` and counted. A held-out call
that reports a different version, or none, is listed as a deviation. The historical
Anthropic planner settings in `config/evaluation.json` are not used.

**Optional experiments.** No alternative-model experiment is declared in 1.1.0. One
must be declared in `eval/models.json` with a version bump before it runs:
- its label starts with `exp-`;
- it fixes the provider order with fallbacks off and states its cases and budget;
- it records serving provider, route and gateway-fee metadata, or `not_reported`;
- its results are stored and reported apart from the main tables.

## 13. Statistics and allowed claims

**Intervals.**
- **Method:** 95% percentile intervals from a case-clustered bootstrap (10,000 resamples, seed 78).
- **Clustering:** repetitions are averaged per case first; they are never treated as independent requests.
- **Paired price outcomes:** wins versus losses get an exact two-sided sign test, which is
  descriptive at this sample size.

The predeclared comparisons are:
1. full-warm versus single-agent-warm;
2. full-warm versus the fixed heuristic;
3. warm versus cold (validity, coverage, freshness, latency and cost per successful request);
4. TechScout versus each local baseline on the local subset;
5. TechScout versus each assistant on the 10 domestic cases of the assistant subset; H11 and
   H30 are reported only in the international capability table.

Anything else is labelled exploratory. The broader-discovery experiment in #80, and any
keyword-only versus hybrid retrieval comparison from plan section 10, run on development
cases and are reported apart from held-out outcomes; neither is part of this protocol.

**Hypotheses, not claims.** The plan's targets are tested and reported whichever way they fall:
- the full system's valid-offer rate is at least 95%;
- the full system has more paired price wins than losses against each AI baseline (the
  single-agent configuration and the two browsing assistants).

**Claims we will not make:**
- **Language:** the protocol includes 5 Arabic and 5 Arabizi cases. Results are reported per
  language with these counts. We will not claim general Arabic or Arabizi support beyond the
  tested cases.
- **Other limits:** we will not claim superiority from unsupported features or access
  failures, resource equivalence with external services, an official benchmark score from an
  adapted subset, or a met target from a denominator below 20.

## 14. Choices for the team to confirm

These choices were made in #78 so work would not block. Each is labelled here and in the PR.
Change any of them with a version bump before the pre-run freeze.

1. **Verification.** Repetition 1 is human-verified in full. Repetitions 2–3 get automated re-observation plus a 20% human sample.
2. **Headline denominator.** The valid-offer rate counts the selected offer of each main recommendation (at most three per run). Additional sellers are a secondary figure.
3. **Conservative verdicts.**
   - Unverifiable offers count as invalid.
   - Price drift counts as invalid.
   - Each also has a separately reported figure.
4. **Ties and currency.**
   - The tie threshold is the larger of USD 1.00 and 0.5%.
   - Currency conversion uses the Banque du Liban published rate per verification pass.
5. **Timing.**
   - External sessions: 8 + 10 minutes.
   - Manual shopping: 20 minutes.
   - Assistants: 8 + 10 minutes.
   - Verification within 60 minutes; paired sessions within 90 minutes.
6. **Repetitions.** Warm configurations run 3 times and cold once, with the reduction order in section 8.
7. **Subsets.** The local subset has 10 cases; the assistant subset has 12.
8. **Designed outcomes.** Designed no-match and uncertain cases rest on the author's product knowledge. The pooled adjudication rule decides the scored class.
9. **Model version.** The model is pinned by ID and the reported version is recorded at the pre-run freeze. No dated snapshot ID is known yet.
10. **Corpus isolation.** Each warm run gets its own copy of the snapshot.

## 15. Limitations

- **Product knowledge:** the cases rest on general product knowledge as of November 2026. Some
  products may be renamed, unavailable or priced differently in Lebanon. Pooled adjudication
  scores what actually exists; the designed outcomes are not answers.
- **Sample size:** thirty cases give wide intervals. Phones and laptops get most of the
  sample, so tablets, monitors and audio support only coarse conclusions.
- **Links:** no held-out case uses a specific Lebanese retailer product link. Writing one
  would have required looking up listings.
- **Language:** coverage is a small, representative sample, not a measure of general multilingual ability.
- **Visibility:** the held-out cases are visible in the repository to the people building the
  system. The rules, the leakage test and the hashes stop copying and silent edits, not
  reading; developers must not tune on these cases. Some held-out phone names also appear in
  the generic model list in `app/devices.py`.
- **Size classes:** monitor sizes use listed-diagonal minimums (26.5 and 31.5 inches), so
  panels sold as 27-inch and 32-inch class count.
- **External services:** LebTech, One Souq and the assistants may change or block access
  during the evaluation. Such sessions are recorded as access failures, not retried
  indefinitely.
- **Effort:** human verification and manual sessions need roughly a working day of teammate time on 1–2 December.
- **Scope:** this protocol adds no runner, scorer or result. Those belong to #79 and #82.
