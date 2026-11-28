# TechScout agent instructions

This file is the single source of project policy for coding and review agents.
Read [CONTRIBUTING.md](CONTRIBUTING.md), the [team workflow](docs/team-workflow.md) and the task's issue.

## Project and architecture

TechScout compares electronics offers for buyers in Lebanon: phones, laptops, tablets,
monitors, and headphones and earbuds, from Lebanese sellers. Buyers can name a model, paste a
product link or describe their needs. Buying takes place on the retailer's site.

The **scout agent** (`app/scout`) is the only implementation under development. Its
[design](docs/scout-agent.md) explains the agent loop, shop reads and the code verifier. The
[buyer product contract v2](docs/buyer-product-contract.md) defines the intended behavior.
The scout runs standalone. The FastAPI app, Postgres storage, React UI, accounts and run worker
are kept as infrastructure for integrating it later. Do not wire the scout into them until it
passes its evaluation and the team decides to integrate.

The deterministic control flow, the single shopping agent and the two-agent experiment are
retired. They are kept at the `baseline/pre-scout` and `archive/<branch>` tags for comparison and
inspection only. Do not restore or extend them on main.

## Starting work

1. Read the issue, its acceptance criteria and related PRs. Each issue has one active writer.
2. Check the repository, current branch and worktree. Preserve existing changes.
3. Branch from current main or the declared stacked base, and open a focused PR. Never push
   directly to main or overwrite a collaborator's branch.
4. Identify missing context or access rather than inventing requirements.

## Product and evidence rules

- **Scope.** Prices are compared before VAT and delivery. Do not require tax, delivery fees,
  delivery quotes, addresses or branch stock for a recommendation. Checkout, payment,
  reservations, retailer accounts and seller messaging are out of scope.
- **Buyer constraints.** Preserve them. "Under" and "up to" are hard ceilings; "around" is a
  preference. Ask a follow-up only when the answer would change the recommendation.
- **Condition.** New is the default. Used, refurbished or open-box offers qualify only with the
  buyer's opt-in. If the shop doesn't state condition, recommend the offer with that caveat.
  Never claim "new" without evidence from the current run.
- **Evidence.** Bind every price, stock, condition and identity claim to the exact variant and
  to page evidence read in the current run. The model proposes; code decides what is true.
- **Money and time.** Use Decimal money. Never present an unknown amount as zero or invent a
  price. Keep timestamps timezone-aware and normalized to UTC.
- **Retrieval.** Respect the shop directory, robots rules and workload limits. Never bypass bot
  challenges. Public website data may be retrieved without retailer permission.
- **Tests.** Use synthetic shops and offline fixtures. Do not invoke retailer endpoints, paid
  models or an operational service just to run the ordinary test suite.
- **Evaluation.** Develop on development requests only. Never tune on the evaluation requests or
  read their expectations before a scored run. Report results with their failures.

## Checks and evidence

Use Python 3.11 for the shared development and CI baseline. From the repository root:
```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```
Use the configured virtual-environment Python; Windows may use `.venv/Scripts/python.exe`.
Report exact results and any validation you could not run. CI (`ci.yml`) also runs the suite
against Postgres, checks the frontend and builds the Docker image; preserve those checks. A
passing test suite does not prove that the scout gives buyers useful answers.

## Handoff and Definition of Done

- Link the GitHub issue in the PR. Record the change, test results, evaluation evidence where
  relevant and limitations. Record lasting decisions in the repository or on the issue.
- The author reviews, approves and merges their own PR once the required checks pass. A PR into
  someone else's branch is merged by that branch's owner.
- Report whether code is proposed or merged, and whether acceptance criteria were demonstrated.
- Do not add AI-assistant attribution (co-author trailers, generated-by footers) to commits or
  PRs, and do not name branches after AI tools.

## Code review rules

- Flag invented evidence, unknown amounts treated as zero, float money, unsupported "new",
  "in stock" or "exact match" claims, and errors reported as completed recommendations.
- Check task scope, shop workload bounds and evaluation hygiene.
- Separate demonstrated bugs from suggestions; include evidence and practical impact.
