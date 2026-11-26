# Buyer search experiments

**Current milestone:** compare one end-to-end shopping agent with the rule-based buyer. [Board](https://github.com/users/han33-dotcom/projects/2) · [Experiment 2 tasks](https://github.com/han33-dotcom/TechScout/milestone/2)

## Experiment 1 · Baseline established

Rules interpret the request, search supported retailers, match the selected variant and verify costs. GPT-6.1 Sol at low effort can choose a broader search, request a recheck and explain qualifying results.

| Evidence | Buyer outcome |
| --- | --- |
| [Frozen 20 with the earlier baseline](../acceptance/buyer-v1/results-2026-11-25/README.md) | 0/10 full answers, 0/5 useful partials, 0/5 explained blockers, 0/5 seller pairs. No misleading verified claims. Median final display 32.9 s, $0.022772 settled. |
| [Latest baseline, five fresh requests](../acceptance/sku-discovery/README.md) | Three correct pages found, two prices shown, one confirmed match, zero delivered totals. Final displays took 42.7–65.5 s, $0.006324 settled. |

The earlier [23-request study](evaluation-report.md) compared fixed rules, a sequential agent and the full controller. All 230 trials ended without a priced match. Subsequent [discovery](../acceptance/development/discovery-repair/final/README.md) and [variant repairs](../acceptance/development/variant-followup/README.md) improved individual cases. The final baseline still discarded a correct $9 headset after reaching its page. Keep the merged [#197 implementation](https://github.com/han33-dotcom/TechScout/pull/197) as the control. Product acceptance remains open under [#178](https://github.com/han33-dotcom/TechScout/issues/178).

## Experiment 2 · Single shopping agent

One agent will interpret the request, choose tools, revise its search and advise the buyer. Both implementations will have general web search, direct retailer reads, and browser interaction for variant selection, branch checks and delivery quotes. The control keeps its existing decision logic and gains the same tools through a shared adapter. The [amended freeze](../acceptance/architecture-v2/README.md) keeps the original 20 primary cases, six fresh requests, budgets and run order. Runtime integration and paired results are pending.

| Order | Deliverable |
| --- | --- |
| 1 | [Pin the control and reserve fresh confirmation cases](https://github.com/han33-dotcom/TechScout/issues/199) |
| 2 | [Build the agent behind the buyer UI](https://github.com/han33-dotcom/TechScout/issues/200) |
| 3 | [Verify shared search and browser access, then replay identical evidence](https://github.com/han33-dotcom/TechScout/issues/201) |
| 4 | [Score both and record the design decision](https://github.com/han33-dotcom/TechScout/issues/202) |

Use the same [20 frozen requests](../acceptance/buyer-v1/freeze-v1-2026-11-25.json), model, effort, tools, catalog and budgets. Run the finalists on the same fresh confirmation set. Score browser-visible correctness first, including misleading claims. Compare cost per useful answer, first useful display and final latency. Publish paired outcomes and dated reference changes. [Scoring protocol](buyer-correctness-acceptance.md)

**Later experiments:** compare parallel seller workers with the single agent, then vary model or reasoning effort on a fixed architecture. Record the measured tradeoff before adopting a change.
