# TechScout team workflow

## Rollout status

Issue-based policy and Project synchronization code are prepared on the workflow
foundation PR. They become active only after reviewed merges and owner activation.
The [private Project board](https://github.com/users/han33-dotcom/projects/2) and
[public Wiki](https://github.com/han33-dotcom/TechScout/wiki) were published on October 14, 2026.
Slack subscriptions, remaining board access, sync credentials and the human pilot
are pending. Shared cloud AI review remains optional and deferred.

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
8. Merge through the existing review rules. All required implementation PRs merged
   means **Implemented**. A teammate records acceptance evidence, sets **Done** and
   then closes the issue. Until activation, update the board and share links manually.

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
| Implemented | All linked implementation PRs merged into `main` |
| Blocked | Human-recorded dependency or decision; include reason |
| Done | Teammate verifies acceptance and records evidence |

Automation never sets Done, closes issues or overwrites manual Done/Blocked.
Closed-unmerged PRs require triage; reconcile removed scope/links explicitly.
A release PR does not mark every associated feature accepted.

## Activation and collaborator refresh

Follow [GitHub workspace activation](github-workspace-activation.md) and
[PR policy activation](pr-policy-activation.md). Each teammate still completes
[collaborator setup](collaborator-setup.md). After policy merges, pull `main`,
update ongoing feature branches deliberately and refresh/restart the assistant.
Normal web chats need current repository instructions supplied explicitly when
automatic loading is unavailable.
