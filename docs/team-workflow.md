# TechScout team workflow

## Current rollout

This change introduces shared instructions and contribution guidance. Automatic shared
AI review, Slack notifications, task synchronization and automated fixes are later
steps. Do not assume these services are active because their target flow is documented.

Workspace: `nsquared`. Project channel: `#eece503p-techscout`.
Work branches and implementation PRs target `main`. The backend PR may introduce
additional setup and CI; always inspect the current branch before starting.

## Ownership

| Participant/tool | Responsibility |
|---|---|
| Task owner and assistant | Implement the scope, validate it, open a PR and address accepted findings |
| Human reviewer and assistant | Inspect current code against task criteria; human decides approval |
| Notion | Tasks, acceptance criteria, assignments, deadlines and completion evidence |
| GitHub | Code, validation, actionable review findings and merge evidence |
| Slack | Team discussion and links to the shared task/PR |
| Automation, when enabled | Shared AI reviews, notifications and configured status updates |

All coding agents use the canonical `AGENTS.md`. Claude Code imports it through
`CLAUDE.md`. Teammates configure their own accounts and refresh their sessions when
instructions change; no private assistant conversation is assumed to be shared.

## Everyday handoff

1. Define a Notion task with a human owner/reviewer and observable acceptance criteria.
2. Start a focused branch from current `main`; load shared instructions and the task.
3. Implement and run `python -m unittest discover -s tests -v` with current requirements.
4. Open a PR into `main`, linking the task and describing the change, checks and limitations.
5. The reviewer/assistant reads that PR and leaves actionable findings on GitHub.
6. The author/assistant reads accepted findings directly, fixes them and reruns validation.
   One writer works on the branch at a time; reviews reference the current head.
7. The human reviewer approves when satisfied, and the PR merges through GitHub.
8. Verify acceptance and record completion. Before automation is enabled, share the PR
   link and update Notion manually. After activation, check synchronization results.

## Simple example: unknown delivery fee

Ahmad owns a task to preserve an unpublished delivery fee as unknown. Mohammad
Nasrallah is its human reviewer. Ahmad's assistant reads the policy, implements on
a feature branch, runs offline tests and opens a linked PR into `main`.

Mohammad asks Claude Code to review the PR against the evidence contracts. Claude
records findings on GitHub, and Mohammad decides which require correction. Ahmad's
assistant reads those findings directly and fixes them. Mohammad verifies the latest
head, approves and merges. A human verifies that unknown fees never become zero and
records Done. Shared AI review, Slack notification and status sync join this flow
only after they are activated and verified.

## Task status meanings

| Status | Evidence |
|---|---|
| Not started | Task scoped, work not begun |
| In progress | Implementation underway |
| In review | PR ready for review |
| Changes requested | Accepted review findings remain |
| Implemented | Required implementation PRs merged into main |
| Blocked | Human-recorded dependency/decision with reason |
| Done | Human verifies acceptance and records evidence |

The current tracker may still have only its original statuses. Use a clear note
until the schema upgrade. A single merged PR does not complete a task that needs
other PRs, deployment or acceptance checks. Manual Blocked/Done states are not
silently overwritten.

## Refreshing shared policy

After instructions merge, update clean local `main` with `git pull --ff-only`.
Bring `origin/main` into an ongoing feature branch deliberately, preserving local
changes. Restart/refresh the assistant and verify the active instruction sources,
branch target and checks. Ordinary web chats require the current files supplied
explicitly if repository instruction loading is unavailable.

Follow [collaborator-setup.md](collaborator-setup.md) for individual tool setup,
instruction-loading smoke checks and evidence records.
