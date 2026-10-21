# TechScout team workflow

## Rollout status

The reviewed workflow is on `main`; metadata-only Project synchronization is enabled
and live writes were verified on October 21, 2026. The private board, Wiki, approved
access and native Slack subscriptions are configured. The owner confirmed the
listed teammates' own-account board access. The human pilot (issue #31),
acceptance reconciliation and local agent onboarding remain unverified; rollout
issue #5 stays open. A merge gives Implemented, never automatic Done.
Shared cloud AI review is optional. Automated Codex comments were observed on
#32/#33; this rollout did not change their configuration or require an AI approval.

Workspace: `nsquared` (`T0C8YHJMXAN`). Channel: `#eece503p-techscout`.
Implementation PRs target `main`.

## Where work lives

| Place | Responsibility |
|---|---|
| GitHub Issues + Project | Scope, assignee, reviewer, acceptance criteria, priority, due date and progress |
| Repository `docs/` | Technical documentation, architecture, setup and API contracts |
| Root AGENTS.md / CLAUDE.md | Shared coding/review policy and Claude Code import |
| GitHub Wiki | Meeting notes, background and general references |
| Slack | Discussion and project-specific task/PR links |

Start with the [documentation index](README.md). Keep one authoritative copy of a
document. If Wiki decisions affect code, summarize the technical consequences in
the relevant repository document through a PR. Agents consult Wiki context when
needed; ordinary code clones do not include the separate Wiki repository.
Existing Notion reports and original tasks remain available as historical records;
new software tasks belong in GitHub. Private attachments and participant material
must not be published in public Issues, Wiki pages or repository documentation.

## Everyday handoff

1. Create an issue using **Team task**; set its native Assignees field, **Human reviewer** (a teammate's GitHub handle),
   acceptance checklist, Priority and Due date. Add it to this project's board.
2. Pull current `main`, refresh the assistant's instructions and create a focused
   work branch. One writer works on the branch at a time.
3. Implement the issue scope and run the checks in AGENTS.md.
4. Open a PR into `main` with `Project: TechScout` and one full `Task URL:` pointing
   to this repository's issue. Optional task ID uses the issue number, not a legacy
   Notion ID. Include what/why, test evidence and risks.
5. Use **Related to #NUMBER**, not Closes/Fixes/Resolves. Automatic closing keywords
   would bypass the separate human acceptance step.
6. The teammate opens the PR from GitHub or Slack and asks their own assistant to
   review its current head and issue criteria. Findings stay on the PR.
7. The author's assistant reads the findings directly, fixes accepted issues and
   reruns checks. The human reviewer decides approval; agents do not approve or merge
   on someone's behalf.
8. Keep the branch up to date with `main` and rerun required checks after stacked
   prerequisites merge. Merge through the existing review rules. All required implementation PRs merged
   means **Implemented**. A teammate records acceptance evidence, sets **Done** and
   then closes the issue. Status synchronization is active; humans still own acceptance.

The tracking issue for this rollout is [#5](https://github.com/han33-dotcom/TechScout/issues/5).
Task ownership does not require buying a cloud AI reviewer. Personal assistants run
when their human starts them; a Slack notification does not start a teammate's chat.

## Status meanings

| Status | Evidence |
|---|---|
| Not started | Scoped task, implementation not begun |
| In progress | Work underway or only draft PRs open |
| In review | At least one implementation PR ready for review |
| Changes requested | Outstanding change request from an independent configured human reviewer |
| Implemented | All remaining implementation PRs merged into `main` |
| Done | Teammate verifies acceptance and records evidence |
| Blocked | Human-recorded dependency or decision; include reason |

Automation never sets Done, closes issues or overwrites manual Done/Blocked.
Closed-unmerged PRs are treated as abandoned attempts when open or merged PRs
remain for the same task. Only abandoned PRs still require triage and preserve the
current status. Humans must reconcile any removed scope before accepting the task.
A release PR does not mark every associated feature accepted.

## Activation and collaborator refresh

Follow [GitHub workspace activation](github-workspace-activation.md) and
[PR policy activation](pr-policy-activation.md). Each teammate still completes
[collaborator setup](collaborator-setup.md). After policy merges, pull `main`,
update ongoing feature branches deliberately and refresh/restart the assistant.
Normal web chats need current repository instructions supplied explicitly when
automatic loading is unavailable.
