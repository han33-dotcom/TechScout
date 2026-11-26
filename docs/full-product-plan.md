# Product and submission plan

TechScout helps a buyer choose an electronics variant and compare offers from Lebanese sellers serving their destination or pickup branch. Coverage spans phones, laptops, tablets, monitors and headphones or earbuds. The [buyer contract](buyer-product-contract.md) defines advice across turns, near matches, seller quotes and retailer handoff. The [acceptance matrix](buyer-acceptance-matrix.md) gives each behavior an observable test.

## Delivery priorities

1. Measure buyer-visible correctness. The baseline failed the frozen 20-case evaluation. Compare it with a single shopping agent using the same evidence, tools and budgets. Record quality, latency and cost before choosing an architecture. [Experiment record](buyer-architecture-comparison.md) · [Scoring](buyer-correctness-acceptance.md)
2. Demonstrate the buyer flows, including useful partial results, comparable seller totals, clarification and the saved-address round trip. Track outstanding work on the [board](https://github.com/users/han33-dotcom/projects/2).
3. Rehearse from a clean local Docker Compose deployment on the demo and recovery machines. The submission deadline is December 5, 2026. Confirm the cutoff time and demo slot in [release task #85](https://github.com/han33-dotcom/TechScout/issues/85).

## System and operating limits

The browser talks to FastAPI. A separate worker uses a shared retailer directory, public evidence and PostgreSQL full-text and vector retrieval. Recheck finalist prices and stock during the run. Record the actual seller, destination coverage, outstanding charges and evidence for each comparison. [Architecture](architecture-api-report.md) · [Freshness](evidence-services.md) · [Contracts](full-product-contracts.md)

New sellers require technical review, scoped endpoints and positive workload limits before live use. Track failed reviews as exceptions. [Onboarding](retailer-onboarding.md) · [Source admission](source-admission.md)

The shared development and evaluation allowance is USD 500, including a USD 50 ceiling for initial collection and USD 3 per day for maintenance. Runtime caps depend on the protocol. The buyer correctness evaluation records time independently of answer quality. [Accounting](shared-accounting.md) · [Original study protocol](evaluation-protocol.md)

## Submission package

| Announcement requirement | Deliverable and acceptance |
|---|---|
| Live demo/presentation and poster | Show exact and needs-based searches, domestic pickup and delivery cost states, plus an unexpected input or failure; prepared fixture fallback is labelled |
| Full repository and reproducibility | Pinned environment, migrations, configuration examples, safe fixtures, documented startup and evaluation commands; verified from a clean environment |
| Evaluation suite and results | Frozen protocol, held-out cases, baseline outputs, scorer, sanitized results, and measured claims |
| Agent traces and operational metrics | Example complete/partial/failing traces plus latency, tokens, cost, failures, and coverage summaries |
| Failure and threat analysis | Failure Matrix, lightweight threat model, regression evidence, known limitations, and retention policy |
| Architecture/API documentation | Data flow, component boundaries, schemas, auth/session rules, endpoint/error behavior, and source/category coverage |
| Final technical report | User problem, agentic justification, choices/tradeoffs, implementation, evaluation, failure analysis, limitations, and reproducibility |

The [original approved plan](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/full-product-plan.md) retains the detailed design, schedule, course references and decision history. [Full backlog](https://github.com/han33-dotcom/TechScout/issues/36)
