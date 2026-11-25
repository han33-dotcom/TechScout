# Buyer correctness acceptance

[Buyer-flow milestone #76](https://github.com/han33-dotcom/TechScout/issues/76) tracks whether a buyer can use the normal browser to find a domestic offer and act on it. The original [held-out evaluation](evaluation-report.md) is the baseline. This protocol defines a new assessment. The case manifest, independent reference observations, and scored results are subsequent deliverables of [#178](https://github.com/han33-dotcom/TechScout/issues/178).

## Freeze the cases before debugging

1. Write realistic buyer requests before looking up which retailers TechScout handles well. Use fresh requests across phones, laptops, tablets, monitors and audio. Include exact-model and needs-based entry, domestic delivery and selected-branch pickup. Keep the original study, its known outcomes, and the three development examples outside this cohort.
2. Independently check retailer evidence without running the buyer system. Keep a screening log of **every** request considered, including unclassifiable requests. Record the buyer text and inputs, hard constraints, variant, seller, listing and policy URLs, timestamps, known and missing facts, and the reason for including or excluding each request. A blocked case needs evidence for a specific blocking constraint. A fruitless search alone cannot establish one.
3. Freeze the 20 case IDs, exact inputs, reference evidence, expected classes and five comparison flags in a dated, versioned manifest before debugging. Select 10 independently verifiable requests, five useful partial requests and five known blocked requests. Flag five of the ten verifiable requests for which two independent sellers offer the same variant on comparable terms. If independent checks cannot fill a group, publish the screening counts and gaps. An unfilled cohort cannot pass.
4. Keep case prompts and expected answers out of fixtures, debugging and tuning. Use separate development requests for [one useful exact-model search](https://github.com/han33-dotcom/TechScout/issues/176), [one fully verified offer](https://github.com/han33-dotcom/TechScout/issues/75), and [one two-seller comparison](https://github.com/han33-dotcom/TechScout/issues/177). Each checkpoint needs real browser evidence on its linked issue.

## Score what the buyer sees

| Frozen cases | Evidence required in the browser | Pass |
| --- | --- | --- |
| 10 fully verifiable | A qualifying exact variant or suitable model, current price and stock, a working link to that offer, and an evidenced payable total for the requested fulfillment. Honor every hard constraint and the allowed condition. | At least 8 of 10 |
| 5 useful partial | A matching listing with a current listed price and working retailer link. Identify the specific missing cost or fulfillment fact, and label the offer uncertain. | At least 4 of 5 |
| 5 known blocked | Explain the relevant blocking constraint and how it prevents a qualifying result. An empty list or generic failure earns no credit. | 5 of 5 |
| 5 comparison flags within the first 10 | Show two independent sellers for the same exact variant. Check each seller link and verified total on equivalent fulfillment terms. Indicate the cheaper option or a tie. Score the single-offer and comparison criteria independently. | At least 4 of 5 |

A misleading verified claim in **any** case fails the entire gate. Examples include treating an unknown fee as zero, treating online stock as selected-branch stock, passing off the wrong variant as a match, or claiming a delivery guarantee from an estimate. The team can give a buyer a useful partial answer while keeping all unknowns visible.

## Run, timing and evidence

- After all three development checkpoints pass, pin the candidate revision, configuration, corpus version and run order. Submit each frozen request through the normal browser, API and worker with its intended inputs. Keep the independently found URLs out of the buyer inputs unless a frozen prompt itself supplies a retailer link.
- Use a configurable development-only active-execution cap of **300 seconds**. Record the effective cap before starting. Record time to first useful display and final elapsed time, then report attainment of the existing **120-second** product target separately. The five-minute cap bounds each attempt; changing the application deadline for development requires a separate implementation change. Preserve cancellation, source limits and shared spend controls.
- Recheck reference facts near the buyer run. For each case retain a sanitized browser capture, run ID, source evidence, displayed claims, timing, spend and scoring reason. Log reference drift, timeouts and failed attempts. Keep every original case in its denominator. If drift makes a result unassessable, report it explicitly with no pass credit. Record fixes or reruns separately.
- Report the four numerators and denominators, misleading claims, counts of all screened cases by class, and the 120-second timing results. The 20 cases measure correctness within specified classes. Report how often screening actually found verifiable offers separately.
- A focused fix PR links its checkpoint and records before/after evidence. The final acceptance PR can close #76 once all three live checkpoints and every threshold above pass. [Release #85](https://github.com/han33-dotcom/TechScout/issues/85) also needs the stakeholder walkthroughs and release rehearsals tracked there.
