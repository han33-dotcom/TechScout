# Buyer search experiments

The current milestone measures the single shopping agent on the original 20 buyer requests. The owner retired the paired control requirement on 26 November 2026. [Board](https://github.com/users/han33-dotcom/projects/2) · [Experiment 2 tasks](https://github.com/han33-dotcom/TechScout/milestone/2)

## Experiment 1 · Baseline established

Rules interpret the request, search supported retailers, match the selected variant and verify costs. GPT-6.1 Sol at low effort can choose a broader search, request a recheck and explain qualifying results.

| Evidence | Buyer outcome |
| --- | --- |
| [Frozen 20 with the earlier baseline](../acceptance/buyer-v1/results-2026-11-25/README.md) | 0/10 full answers, 0/5 useful partials, 0/5 explained blockers, 0/5 seller pairs. No misleading verified claims. Median final display 32.9 s, $0.022772 settled. |
| [Latest baseline, five fresh requests](../acceptance/sku-discovery/README.md) | Three correct pages found, two prices shown, one confirmed match, zero delivered totals. Final displays took 42.7–65.5 s, $0.006324 settled. |

The earlier [23-request study](evaluation-report.md) compared fixed rules, a sequential agent and the full controller. All 230 trials ended without a priced match. Subsequent [discovery](../acceptance/development/discovery-repair/final/README.md) and [variant repairs](../acceptance/development/variant-followup/README.md) improved individual cases. The final baseline still discarded a correct $9 headset after reaching its page. Keep the merged [#197 implementation](https://github.com/han33-dotcom/TechScout/pull/197) as the control. Product acceptance remains open under [#178](https://github.com/han33-dotcom/TechScout/issues/178).

## Experiment 2 · Single shopping agent

The normal UI can select a single shopping agent. Both historical implementations selected real product variants and displayed a USD 165 uncertain offer. After provider restoration, the agent dispatched general web searches. The control planned a search and its worker failed before dispatch. Both preflights subsequently demonstrated managed scraping. Branch-stock checks and pre-order quote submission lack a paired demonstration. The control stays available as historical evidence. [Paired receipts](../acceptance/development/paired-shopping/receipts/README.md) · [Final capability record](https://github.com/han33-dotcom/TechScout/issues/201)

| Order | Deliverable |
| --- | --- |
| 1 | [Pin the control and reserve fresh confirmation cases](https://github.com/han33-dotcom/TechScout/issues/199) |
| 2 | [Build the agent behind the buyer UI](https://github.com/han33-dotcom/TechScout/issues/200) |
| 3 | [Archive the shared integration evidence and defer the control dispatch repair](https://github.com/han33-dotcom/TechScout/issues/201) |
| 4 | [Verify the agent on independent development requests, then score its frozen 20](https://github.com/han33-dotcom/TechScout/issues/202) |

The agent runs live in listed primary order under the frozen scoring and budget rules. Report buyer-visible outcomes, misleading claims, cost per useful answer, latency and reference drift. Preserve the six reserved requests for a separate confirmation. [Scope and development cases](../acceptance/development/agent-first/README.md) · [Scoring protocol](buyer-correctness-acceptance.md)

**Later experiments:** compare parallel seller workers with the single agent, then vary model or reasoning effort on a fixed architecture. Record the measured tradeoff before adopting a change.
