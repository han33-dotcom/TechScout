# TechScout documentation

| To find | Start here |
| --- | --- |
| What buyers should experience | [Buyer product contract v2](buyer-product-contract.md) |
| How the scout works | [Scout agent](scout-agent.md) · [Scout evaluation](../acceptance/scout/README.md) |
| What to work on | [Project board](https://github.com/users/han33-dotcom/projects/3) · [Team workflow](team-workflow.md) |
| How to run or contribute | [Quick start](../README.md) · [Contributing](../CONTRIBUTING.md) · [Local development and recovery](local-development.md) |

## Infrastructure reference

This infrastructure is kept for integrating the scout later.

| Area | Guides |
| --- | --- |
| Buyer application | [Browser API and worker](buyer-api.md) · [Comparison UI](buyer-ui.md) · [History and export](buyer-history.md) · [Accounts](buyer-accounts.md) |
| Inference and spend | [OpenRouter](openrouter.md) · [Shared ledger](shared-accounting.md) · [Local accounting](local-accounting.md) |

## Earlier implementations

The deterministic control flow, the single shopping agent and the two-agent experiment are retired. Their designs, studies and results are at the [`baseline/pre-scout`](https://github.com/han33-dotcom/TechScout/tree/baseline/pre-scout/docs) and [`archive/test/two-agent`](https://github.com/han33-dotcom/TechScout/tree/archive/test/two-agent/docs) tags. The [scout agent design](scout-agent.md#why-what-was-stopping-useful-answers) summarises why they fell short.
