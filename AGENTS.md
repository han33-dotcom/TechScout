# TechScout agent instructions

Policy version: 2. This is the canonical policy for coding and review agents.
Read [CONTRIBUTING.md](CONTRIBUTING.md) and the relevant project documents.
Follow applicable nested instructions if they are added later.

## Project and architecture

TechScout compares exact-device shopping offers for buyers in Lebanon. The current
main branch implements shopping/evidence contracts, the source registry and the
backend foundation (FastAPI app, Postgres storage, migrations); do not describe the
planned retrieval or shopping flow as implemented.
- MVP and shared contracts: [docs/mvp-spec.md](docs/mvp-spec.md).
- Source policy: [docs/source-admission.md](docs/source-admission.md).
- Contract implementation: `app/contracts.py`; source selection: `app/source_registry.py`.
- Persistence and app setup: `app/storage.py`, `app/main.py`, `migrations/`; startup in the README.
- Team workflow and rollout: [docs/team-workflow.md](docs/team-workflow.md).

Documentation index: [docs/README.md](docs/README.md). General references and meeting
notes live in the project Wiki after publication. Consult relevant Wiki decisions
when a task depends on them; summarize code-affecting decisions in reviewed docs.

## Starting work

1. Read the linked task, acceptance criteria, owner and human reviewer.
2. Check repository, branch, working-tree changes and current instructions.
3. Use a focused work branch from `main`; open implementation PRs into `main`.
   Do not push directly to `main` or overwrite another collaborator's branch.
4. Check open PRs before changing shared contracts or introducing infrastructure.
5. Identify missing context/access rather than inventing requirements or task IDs.
   Keep unrelated refactoring and dependencies out of a focused task.

## Product and evidence rules

- Preserve the explicit buyer constraints and current new-smartphone MVP scope.
  Do not silently expand to checkout, seller messaging, accounts or used devices.
- Use Decimal money and the documented serialized contracts. Unknown mandatory
  charges stay unknown, never zero; conflicts cannot masquerade as known facts.
- Bind evidence to the actual field, product/variant and branch where required.
  Online stock does not prove selected-branch pickup stock; observation is not reservation.
- An arrival estimate does not satisfy a guaranteed deadline. Keep timestamps aware
  and preserve UTC normalization and verification within the run.
- Preserve source registry technical review, endpoint scope and positive workload
  limits. The existing project assumption permits public-data retrieval without
  retailer permission emails; do not introduce a permission-first gate.
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

- Link the same-repository GitHub issue URL (Task ID is optional and uses its issue number), in the PR description.
  Explain the change, validation and limitations. Explain workflow-only changes
  explicitly when they have no implementation task.
- Use Related to #NUMBER, not automatic issue-closing keywords. Keep issues open
  until a teammate records acceptance; Project Status and issue closure are distinct.
- Record lasting decisions in repository documentation or the linked task, not only
  in a private assistant conversation.
- Reviews inspect the current PR head and applicable acceptance criteria. Put actionable
  findings on GitHub with file/line references, evidence and impact.
- The author addresses accepted findings and reruns checks. Keep one writer per branch.
- A human teammate decides approval. Agents do not approve on someone's behalf,
  merge automatically or dismiss unresolved findings.
- Implemented means required implementation PRs merged into `main`. Done requires
  a human to verify acceptance criteria, including deployment/demo work where applicable.
- Do not claim AI review, Slack notification or Project synchronization happened unless
  their actual results are available. See the rollout status in the team guide.

## Code Review Rules

- Flag invented evidence, unknown costs treated as zero, float money, misleading stock
  scope or deadline guarantees, and errors reported as completed recommendations.
- Check contract compatibility, task scope and source workload bounds.
- Separate demonstrated bugs from suggestions; include evidence and practical impact.
- Keep mechanical checks in CI and review the latest commit.
