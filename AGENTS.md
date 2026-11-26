# TechScout agent instructions

This file is the single source of project policy for coding and review agents.
Read [CONTRIBUTING.md](CONTRIBUTING.md) and the applicable task and repository instructions.

## Project and architecture

TechScout compares electronics for buyers in Lebanon. The full-product scope covers phones,
laptops, tablets, monitors, and headphones and earbuds. Buyers can search by model or product
link or describe their needs. Establish that the actual merchant is based in Lebanon and serves the destination or pickup branch.
New condition is the default and other conditions require explicit opt-in. Buying takes place on the retailer's site.

The [full-product backlog](https://github.com/han33-dotcom/TechScout/issues/36) defines
implementation work and dependencies. The [v1 specification](docs/mvp-spec.md) documents
the existing contract. The [documentation index](docs/README.md) links source and team policy.

## Starting work

1. Read the issue, prerequisites, acceptance criteria and related PRs. Claim a ready,
   unassigned issue by assigning yourself. Each task has one active writer.
2. Check the repository, current branch and worktree. Preserve existing changes.
3. Branch from current main or the declared stacked base. Open a focused PR. Never push
   directly to main or overwrite a collaborator's branch.
4. Check shared interfaces and overlapping work before editing. Record blockers and
   integration needs on the issue.

## Product and evidence rules

- Preserve explicit buyer constraints. Approximate budgets are preferences. Treat an explicit
  maximum as a hard ceiling. Follow the [buyer product contract](docs/buyer-product-contract.md)
  and link buyer-facing changes to the [acceptance scenarios](docs/buyer-acceptance-matrix.md).
- Give advice across turns. Ask when the answer could change the recommendation and carry
  feedback forward. For a pasted deal, compare the linked variant across Lebanese sellers.
- Use applicable retailer fees or a scoped pre-order quote. Name sellers needing an address
  and ask once. The account flow must return a buyer who adds a saved location to the
  same chat and resume pending quotes automatically.
  The buyer orders and pays on the retailer site. Exclude reservations, seller messaging
  and retailer-account creation.
- Use Decimal money and the documented serialized contracts. Unknown mandatory
  charges stay unknown, never zero; conflicts cannot masquerade as known facts.
- Bind evidence to the actual field, product/variant and branch where required.
  Online stock does not prove selected-branch pickup stock; observation is not reservation.
- An arrival estimate does not satisfy a guaranteed deadline. Keep timestamps aware
  and preserve UTC normalization. Main recommendations require price and stock checks within
  the current run. Keep provisional candidates and uncertain alternatives visibly separate.
- Preserve source registry technical review, endpoint scope and positive workload
  limits. The existing project assumption permits public-data retrieval without
  retailer permission emails; do not introduce a permission-first gate.
- On the `test/two-agent` experiment and its topic branches, both agents may inspect
  a first-seen public retailer page directly through Firecrawl and use Playwright
  for an observed product option that requires interaction. Use a per-URL scope,
  honor explicit shop stops and workload limits, and assess the seller and item
  after inspection. Target experiment PRs at `test/two-agent`. Follow the
  [design and checkpoint plan](docs/two-agent-experiment.md).
- The archived outreach drafts are superseded and must not be sent.
- Tests use synthetic offers and offline fixtures. Do not invoke retailer endpoints,
  paid models or an operational service just to run the ordinary test suite.
- Shared contract changes require deliberate versioning/documentation and updates
  to affected consumers. Do not claim retrieval or recommendation success without evidence.

## Checks and evidence

Use Python 3.11 for the shared development/CI baseline. From the repository root:
```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```
Use the configured virtual-environment Python; Windows may use
`.venv/Scripts/python.exe`. Report exact results and unavailable validation.
CI (`ci.yml`) also runs the suite against Postgres and builds the Docker image;
preserve those database and build checks. A passing test suite alone does not prove shopping-flow completeness.

## Handoff and Definition of Done

- Link the GitHub issue in the PR and record the change, test results, demonstrated
  acceptance criteria and limitations. Record lasting design decisions in the repo or issue.
- Inspect the current diff and fix material defects. Review is optional. Put any actionable
  findings on the PR with evidence, impact and a file or line reference.
- The author can merge through GitHub when required CI checks pass on a current branch
  and the task's acceptance evidence is recorded. The author closes the issue after merge
  and verification. If work remains, record it on the issue.
- Report whether code is proposed or merged and whether required acceptance scenarios
  have been demonstrated.

## Code Review Rules

- Flag invented evidence, unknown costs treated as zero, float money, misleading stock
  scope or deadline guarantees, and errors reported as completed recommendations.
- Check contract compatibility, task scope and source workload bounds.
- Separate demonstrated bugs from suggestions; include evidence and practical impact.
- Keep mechanical checks in CI and review the latest commit.
