# Working on TechScout

Use [docs/collaborator-setup.md](docs/collaborator-setup.md) for Windows/macOS setup,
assistant instruction-loading checks and individual onboarding evidence.

The [full-product backlog](https://github.com/han33-dotcom/TechScout/issues/36)
lists ready work and prerequisites. See the [team workflow](docs/team-workflow.md).

## Setup

Use Python 3.11 for the shared baseline. Create a virtual environment at the repository
root and install the current pinned backend requirements.

Windows PowerShell:
```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

macOS/Linux:
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Keep local environments, credentials and operational configuration out of commits.
Use the README for Postgres, migrations and fixture-loading checks.

## Branch and PR flow

1. Assign yourself a ready, unassigned issue and read its acceptance criteria.
2. Fetch origin, preserve local edits and create a focused branch from current main
   or the declared stacked base.
3. Read [AGENTS.md](AGENTS.md). Implement and run the relevant offline checks.
4. Open a PR, link the issue, inspect the diff and record acceptance evidence and limits.
5. The author merges through GitHub after the required checks pass on a current branch
   and the acceptance criteria are demonstrated. Feedback from teammates is optional.

Main requires a PR and the GitHub Actions test and build checks. Update the branch when
GitHub requires it and rerun checks. Main is protected against force pushes and deletion.

## Shared instructions and handoff

[AGENTS.md](AGENTS.md) is the canonical agent policy.
[CLAUDE.md](CLAUDE.md) imports it for Claude Code.
[docs/team-workflow.md](docs/team-workflow.md) explains task ownership and status.
For buyer-facing work, follow the [buyer product contract](docs/buyer-product-contract.md).
Link the relevant [scenario IDs](docs/buyer-acceptance-matrix.md) in the PR and attach
buyer-visible evidence with the tested revision and source mode. Preserve the legacy
record contracts in [docs/mvp-spec.md](docs/mvp-spec.md) and
the existing public-source assumptions in [docs/source-admission.md](docs/source-admission.md).

After policy changes merge, update your local branch deliberately and restart or
refresh the assistant. Confirm it can identify loaded instructions, branch target
and test commands. In an ordinary web chat, supply the current instruction contents
and repository context explicitly.

Review findings belong on the PR. The author and their assistant read them there;
there is no requirement to copy private chat histories between teammates.
Record durable design decisions in project documents or the linked GitHub issue.

## Completion

The author records the merged PR and actual acceptance evidence on the issue,
then closes it. Use issue state, assignee and a brief blocker note to track progress.
