# Working on TechScout

Use [docs/collaborator-setup.md](docs/collaborator-setup.md) for Windows/macOS setup,
assistant instruction-loading checks and individual onboarding evidence.

Use [docs/pr-policy-activation.md](docs/pr-policy-activation.md) for PR metadata,
scoped exemptions and the owner checklist for required-check activation.

## Setup

Use Python 3.11 for the shared baseline. Create a virtual environment at the repository
root and install the current requirements; main currently needs Pydantic.

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
Recheck requirements/setup when the pending backend work merges.

## Branch and PR flow

1. Start with a clean worktree, fetch origin and update `main` using `git pull --ff-only`.
   Preserve uncommitted work before switching branches.
2. Create a focused `feat/`, `fix/`, `docs/`, `ci/` or task branch.
3. Read [AGENTS.md](AGENTS.md) and the linked task's acceptance criteria.
4. Implement, run the offline suite and open a PR into `main`. Use conventional
   commit messages such as `feat:`, `fix:` or `docs:`.
5. Link the GitHub issue and document what changed, why, checks and limitations.
6. A teammate reviews the latest PR; the author addresses accepted findings and
   reruns checks. The human reviewer supplies the approval.
7. Merge through GitHub after applicable checks and review requirements are satisfied.

The existing main ruleset requires one approving review and dismisses stale approvals.
Follow the actual configured requirements; do not bypass them. More CI/metadata
enforcement is part of the later team-workflow rollout.

## Shared instructions and handoff

[AGENTS.md](AGENTS.md) is the canonical agent policy.
[CLAUDE.md](CLAUDE.md) imports it for Claude Code.
[docs/team-workflow.md](docs/team-workflow.md) explains ownership, task states and
review handoffs. Preserve the contracts in [docs/mvp-spec.md](docs/mvp-spec.md) and
the existing public-source assumptions in [docs/source-admission.md](docs/source-admission.md).

After policy changes merge, update your local branch deliberately and restart or
refresh the assistant. Confirm it can identify loaded instructions, branch target
and test commands. In an ordinary web chat, supply the current instruction contents
and repository context explicitly.

Review findings belong on the PR. The author and their assistant read them there;
there is no requirement to copy private chat histories between teammates.
Record durable design decisions in project documents or the linked GitHub issue.

## Completion

Implemented means the required PRs have merged. Done means a human has verified the
task acceptance criteria and recorded evidence. Before status synchronization is
enabled, update the GitHub Project manually. Report failed or skipped checks honestly.
