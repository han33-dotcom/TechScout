# TechScout collaborator setup

## What this verifies

Each collaborator checks their own checkout, dependencies and assistant instruction
loading. Shared rules are [AGENTS.md](../AGENTS.md), with the
[CLAUDE.md](../CLAUDE.md) import for Claude Code. Read
[CONTRIBUTING.md](../CONTRIBUTING.md) and [team-workflow.md](team-workflow.md).

The materials are prepared; no teammate's machine has been certified. Do not mark
a person verified until their own environment and instruction evidence is recorded.
Run this again after switching assistants or materially changing shared instructions.

| Person | GitHub | Tool confirmed | OS | Local verification |
|---|---|---|---|---|
| Hasan | han33-dotcom | ChatGPT | Windows | Pending |
| Mohammad Nasrallah | MohamadNasrallah | Claude Code | Windows | Pending |
| Ahmad Karnib | Ahmad860187 | ChatGPT | Windows | Pending |

## 1. Get the shared files without losing current work

Prerequisites: Git, Python 3.11 and access to the repository.
Installing dependencies may require internet; the ordinary tests below use offline
fixtures and do not need operational credentials.

For an existing checkout, first run `git status --short`. If it shows changes,
preserve them using your normal local process before switching branches. Do not
reset, clean, force-pull or automatically discard them.

When these workflow PRs are merged, run from a clean checkout:
```text
git fetch origin
git switch main
git pull --ff-only
git rev-parse HEAD
```

If fast-forward fails, inspect the divergence with your teammate; do not replace
the checkout. Bring the updated `origin/main` into an ongoing work branch
deliberately. Implementation PRs target `main`.

**While the workflow PR is still open:** inspect it without moving your active branch.
From the existing repository root, choose a sibling path that does not already exist:
```text
git fetch origin
git worktree add --detach ../TechScout-workflow-check origin/ci/team-workflow-foundation
cd ../TechScout-workflow-check
git rev-parse HEAD
```
This separate checkout is for setup verification. A pending policy commit is not
the team's merged policy. Record the exact commit you checked; repeat after merge.

For a first checkout:
```text
git clone https://github.com/han33-dotcom/TechScout.git
cd TechScout
```
Then follow the merged or pending-policy route above.

## 2. Configure dependencies in that checkout

Do not reuse a virtual environment from a different operating system.
If `.venv` already exists, verify its Python version before using it; do not delete
a collaborator's environment automatically.

Windows PowerShell:
```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe --version
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip check
```

macOS / POSIX:
```bash
python3.11 -m venv .venv
.venv/bin/python --version
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Expected: Python 3.11.x; installation succeeds; `pip check` reports no broken
requirements. Use the current requirements and README for the merged backend. CI also verifies Postgres, migrations/fixtures and the Docker build; local unit checks do not replace them.
A missing package/tool is a setup issue to record, not permission to report passing checks.

## 3. Verify the assistant loads this repository's policy

Start or refresh the session from this checkout's repository root. Confirm which
repository/branch the tool is actually accessing. Connecting GitHub or Slack alone
does not prove these instruction files were loaded.

### Claude Code

- Run `claude --version` if using the CLI and record the result. For a desktop/IDE
  surface, record its version and the Claude Code surface.
- Start Claude Code in this repository root.
- Run `/context` or inspect `/memory`; verify this checkout's `CLAUDE.md` appears.
- Inspect the actual `CLAUDE.md`: it must import `@AGENTS.md`.
- Run the read-only smoke prompt below and compare the answers with the files.
- If instructions are missing, check working directory, configured exclusions and
  competing parent/local instructions. Do not delete private/global policy to fix it.
  Reload the intended files or correct the session configuration, then repeat.

### Codex coding environment

If using Codex CLI, record `codex --version` and launch `codex` from the repository
root. In an app/cloud environment, record that surface and its selected repository,
branch and available version/build. Start a fresh session and ask it to list the
active instruction sources, including this repository's `AGENTS.md`.

Compare its reported instructions with the file. Check for overriding
`AGENTS.override.md` files or configuration that prevents discovery, without
silently removing personal/managed rules. Repeat the smoke check after correction.

### Ordinary ChatGPT web/app conversation

This is a **manual context-loading route**, not a claim of automatic file discovery.
Provide the current contents of `AGENTS.md`, `CONTRIBUTING.md` and the relevant
task/architecture files, or ask a repository-connected assistant to read the exact
files and confirm the branch. If it cannot access the repository, provide the
relevant diff/files for work and review; do not ask it to claim checkout/test access.

Record the policy commit with the supplied contents. Refresh them after policy
changes. A private chat's memory does not replace current shared instructions.

### Read-only smoke prompt

```text
Do not edit files, run operational jobs or send messages.
Read AGENTS.md, CONTRIBUTING.md and docs/team-workflow.md for this checkout.
Report the repository, current branch or provided-context limitations, policy commit
if accessible, instruction sources, implementation PR target, exact validation
commands, and two project-specific constraints. Explain who approves a PR and what
distinguishes Implemented from Done. Flag missing access rather than guessing.
```

Expected: PR target `main`; checks match `AGENTS.md`; humans decide approval
and verify task acceptance. Unknown mandatory costs stay unknown and online stock is distinct from selected-branch pickup stock.
Self-reported compliance alone does not prove all future behavior; verify actual
file access/source display where the tool supports it. A tool with no file access
passes only the manual-context route, not an automatic-loading check.

## 4. Run offline project checks locally

Run from the checked-out repository root, with ordinary live-test opt-ins unset.

Windows PowerShell:
```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

macOS / POSIX:
```bash
.venv/bin/python -m unittest discover -s tests -v
```

Expected: tests return exit code 0, with no failures.
Record the test count from the checked-out commit; do not treat an old onboarding count as the current baseline.
Record the actual command and summary. Any failing/unavailable check leaves local
verification pending. A plain web-chat assistant can help interpret output but
cannot certify commands it did not execute or observe.

## 5. Record evidence for each person

Each collaborator fills one entry in the linked onboarding task (or proposes a
reviewed update to the roster above). Keep secrets, private instruction contents
and sensitive screenshots out of the evidence.

```text
Person / GitHub:
Repository:
OS:
Assistant product + surface + version/build (or unavailable):
Policy commit:
Merged policy or pending PR checkout:
Instruction evidence (sources displayed/read, or manual contents supplied):
Smoke-check result and relevant limitations:
Commands run / test counts / skips / failures:
Verification date + timezone:
Status: Verified / Pending
```

Verified means the person's real setup passes both the applicable instruction check
and local project checks. An assistant-generated example entry is not real evidence.
If the person uses both projects, verify each repository separately.

## Troubleshooting and refresh

- Wrong rules: verify checkout/root and restart the session after updating.
- Missing dependencies: use this checkout's Python 3.11 environment and current
  requirements; record install errors without editing unrelated application code.
- Existing work: use the separate worktree route; no forced reset or cleanup.
- Tool change: repeat instruction check and record the new surface/version.
- Other AI tools: verify their official instruction-loading behavior before adding
  an adapter; do not assume `AGENTS.md` is universally automatic.
- After merge: recheck the integration branch's policy commit; pending-PR evidence
  does not establish that the teammate updated their normal checkout.

References: https://code.claude.com/docs/en/memory and
https://learn.chatgpt.com/docs/agent-configuration/agents-md
