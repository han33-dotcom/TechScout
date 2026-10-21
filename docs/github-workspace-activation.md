# Activate the TechScout GitHub workspace

## Current state

The reviewed workflow is on `main`. Project synchronization was enabled on
October 21, 2026 after successful credentialed no-write previews and the owner's
confirmation that the listed teammates can open their private boards from their
own accounts. Live status writes succeeded; the full human acceptance pilot
[#31](https://github.com/han33-dotcom/TechScout/issues/31) remains pending.
The rollout issue #5 stays open until that pilot and acceptance reconciliation
are verified. Local agent onboarding is a separate, still-unverified check.

The existing private Project board, public Wiki, approved Write grants and native
Slack PR/review subscriptions remain in use. `TEAM_SYNC_ENABLED=true` now allows
metadata-only status updates. No Notion token or synchronization is required.
Backend #3 and preview/CI consolidation #30 are merged. Required CI retains
SQLite/Postgres tests, migrations/fixtures and the Docker build.

## Project board

The board already exists; do not create a duplicate. It has all seven Status options:
Not started, In progress, In review, Changes requested, Implemented, Done and Blocked.
This is also the saved board column order, verified after reload on October 15.
The Status board and **All tasks** table are available. For your own work, filter
Assignees with `assignee:@me`; a dedicated saved My tasks view remains optional polish.

Use **Priority** (High/Medium/Low), **Due date** (date), **Human reviewer** (text GitHub
handle) and **Blocked reason** (text). Use native **Assignees** for the task owner.
GitHub's built-in Reviewers field refers to PR reviewers; it is not the task's Human
reviewer field. Acceptance criteria and completion evidence belong in the issue body.

Native auto-add is enabled only for `han33-dotcom/TechScout` with `is:issue`; new items
start at Not started. Automatic issue closure, PR-merge completion and sub-issue
addition are disabled. Keep Done under human acceptance control.

The board contains rollout issue #5 and 23 migrated tasks (#6–#28). Priorities,
source status and the one source deadline were restored. Four historical Done
statuses (#6, #7, #8 and #28) record the source state, not a new acceptance decision.
The imported GitHub issues remain open for team reconciliation. Original Notion
records and attachments remain intact; private attachment URLs and proposal contact
emails were excluded from the public import.

`MohamadNasrallah` retains board Write access. The owner-approved Write grant for
`Ahmad860187` was applied on October 15; Manage access shows both with Role: Write.
The owner confirmed on October 21 that listed teammates opened the board from their own accounts.
Repository permissions do not automatically confer Project access.

The authenticated Project API verified the owner, private visibility, required fields
and exactly one linked repository matching this project on October 15. The owner confirmed teammate access on October 21. Its private visibility does not make repository issues
private; these issues and the Wiki are public.

## Project synchronization credentials and activation

The repository `GITHUB_TOKEN` reads issues, PRs and reviews. GitHub Projects needs
additional authentication; the default repository token does not grant Project
access. Use a GitHub App with appropriate Project access where supported, or an
expiring personal token with Projects read/write permission for this account.
Store it as the Actions secret **PROJECTS_TOKEN**; never paste it into chat, tasks,
Wiki pages, logs or code. A personal Projects token can cover more than one board;
our code additionally validates the configured owner and exactly linked repository.

Set Actions variable **TEAM_PROJECT_ID** to this board's actual GraphQL node ID
(`PVT_...`), not its visible numeric URL suffix. Obtain it using GitHub's documented
Project API query or an authenticated CLI. Set **TEAM_SYNC_ENABLED** to `true` only
when the reviewed workflow code is on the repository's **default branch** (`main`),
the correct Project exists, and its fields, repository link and credentials are
verified. Do not bypass branch protections.

For activation or reactivation, run **Team project sync** manually with
dry_run checked while `TEAM_SYNC_ENABLED=false`. Inspect proposed transitions
before enabling sync; scheduled/event runs and manual writes remain disabled.
Then enable sync and run an approved fixture with dry_run unchecked. The worker consumes current
metadata rather than event bodies; it does not check out PR code or execute task text.
Events wake it through a separate signal workflow with no credentials or checkout.
Daily reconciliation repairs missed events; same-repository writes are serialized.
API requests have timeouts, bounded pagination and retries for transient HTTP errors.
Triage/API errors make the run fail and leave completion unclaimed.

Only PR authors in the configured team roster can influence task status; external
contributor PRs need manual triage until deliberately added to that roster.
The worker updates only Project Status, never issue content/closure, assignments or
acceptance evidence. It preserves closed issues and manual Done/Blocked. A remaining
race exists if a human edits Status after the final read and before the API write;
GitHub supplies no compare-and-swap here. If that happens, restore the intended manual
state and rerun. This is not a guarantee of atomic human/automation updates.

## Wiki publication

The Wiki has Home, project background, meeting-note index, meeting-note template,
references and a sidebar. General material is maintained in the Wiki; temporary
repository bootstrap copies have been removed. Technical documentation remains in
`docs/`, with canonical agent instructions at the repository root.

Verify that Wiki editing remains restricted to repository collaborators.
Do not publish participant data, private contacts, university attachments or original
private meeting records without explicit approval. The references page links agent instructions on the normal implementation branch
(Sentra `develop`, TechScout `main`), updated on October 21.

## Slack subscriptions

GitHub's official Slack app is installed in **nsquared** (`nsquared-corp`), with its
GitHub installation restricted to Sentra and TechScout. Optional code-channel
management was unchecked. AI review is optional; automated Codex comments were
observed on #32/#33, with no configuration change made by this rollout.

In `#eece503p-techscout`, GitHub [confirmed the subscription](https://nsquared-corp.slack.com/archives/C0C8SHN88CT/p1792048965356559)
for `pulls` and `reviews`; `/github subscribe list` shows only this repository.
No existing subscription was removed. The configured command is:

```text
/github subscribe han33-dotcom/TechScout pulls reviews
```

Inspect `/github subscribe list` before changing subscriptions. Native messages handle PR/review events; no custom bot duplicates
those messages. Task descriptions and review details remain on GitHub. Receiving a
notification does not automatically launch a teammate's personal AI assistant.

## October 21 live activation evidence

- The owner confirmed all listed teammates opened their boards from their own
  accounts. This confirms access, not their local assistant setup or task acceptance.
- [Credentialed preview](https://github.com/han33-dotcom/TechScout/actions/runs/39604507686)
  passed on reviewed default-branch code while writes were disabled, including the
  synthetic draft pilot. Proposed transitions were inspected before enabling sync.
- [First write run](https://github.com/han33-dotcom/TechScout/actions/runs/39604701247) passed
  with `DRY_RUN=false`; pilot #31 moved Not started to In progress.
- [Independent readback](https://github.com/han33-dotcom/TechScout/actions/runs/39604900665)
  passed and showed the pilot already In progress, matching its planned status.
- Rollout #5 moved to Implemented and active task #9 to In review. Historical Done
  task #8 was preserved in both the write run and subsequent readback.
- No human change request, approval-staleness, final merge/acceptance or closed/
  Done/Blocked replay is claimed. Those steps remain in the pilot issue.

## October 21 checkpoint before live activation (historical)

- Mohamad approved follow-up #29, which merged into `main` on October 15.
- Backend #3 has merged; actual `test` (SQLite/Postgres, migrations and fixtures)
  and `build` jobs passed on current `main`. Both jobs and `pr-policy` are now
  required from GitHub Actions. The redundant foundation CI was retired in #30.
- Main requires one CODEOWNER approval, stale-approval dismissal and resolved
  review threads, with no bypass actors and force pushes/deletion blocked.
- `TEAM_SYNC_ENABLED=false` and the Project node ID were read back. The existing
  `PROJECTS_TOKEN` secret remains present; its next live authentication check is
  the default-branch preview. No live Project write or pilot is claimed.
- Teammate board access checks, migration acceptance reconciliation, the full
  review/change/fix/acceptance pilot and replay checks remain pending.
- Live policy tests on #30 rejected a missing task link and a self-applied
  exemption. GitHub reported merging blocked while human approval was also missing;
  this did not isolate which requirement blocked it. Restoring the task link and removing
  the temporary label recovered a successful policy check. The
  [self-exemption run](https://github.com/han33-dotcom/TechScout/actions/runs/39586017974/job/119174988009)
  records the independent-label rejection. No human approval was supplied by an agent.
- All 23 migrated issues were inventoried and their GitHub task IDs distinguished
  from retained historical Notion identifiers. #8 records the reviewed backend
  merge while retaining the original handoff as history. All 23 remain open;
  imported Done states have not been converted into new human acceptance evidence.
- Dedicated synthetic pilot [#31](https://github.com/han33-dotcom/TechScout/issues/31)
  records the remaining human actions and evidence. Its criteria remain unchecked
  until observed; the rollout issue remains open.

## October 15 verification checkpoint (historical)

- `MohamadNasrallah` approved the current foundation head and merged #4 into `main`.
- The existing one-approval requirement and stale-review dismissal are preserved;
  branch protections were not weakened.
- Foundation CI and policy regression jobs passed at reviewed head `dae431ee1797`.
  Its metadata job logged `Bootstrap pending`. Follow-up PRs can now validate
  against the merged base; required-check enforcement remains pending.
- `Team project sync` is on TechScout `main`, but sync remains disabled until the
  reviewed abandoned-PR fix reaches `main` and the dry-run/write pilot is verified.
- `PROJECTS_TOKEN` was entered through a masked local prompt and encrypted into this
  repository's Actions secrets. Its `project` scope and authenticated read access
  were verified; it expires November 14, 2026. No plaintext token was logged or saved.
- `TEAM_PROJECT_ID=PVT_kwHOC_Gyqs4BnAcm` and `TEAM_SYNC_ENABLED=false`
  were read back from Actions variables. A workflow dry run and credentialed write
  test remain pending until reviewed follow-up code reaches `main`.
- GitHub approval and merge notifications were observed in this channel on October
  15. Draft/change-request/fix delivery, replay behavior and duplicate suppression
  remain part of the human pilot.
- `workflow-maintenance` now exists; TechScout does not support release exemptions.
- Main now requires CODEOWNER review. Existing approval count, stale-review
  dismissal, blocked force-push/deletion rules and empty bypass list are preserved.

## Pilot

Use the rollout tracking issue and a synthetic/test-only PR. Open a draft, ready it,
request a human review, request a change, push a fix and re-review the latest head.
Observe In progress → In review → Changes requested → In review. Merge only after an
actual teammate approval and required checks. Observe Implemented; the teammate then
records acceptance, sets Done and closes the issue. Replay/reconcile and verify no
incorrect transition or duplicate notification. Preserve the same branch flow and
Sentra collection pause throughout.

## References

- [GitHub Projects API](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)
- [Project automation authentication](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions)
- [GitHub Wiki editing](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)
- [GitHub Slack notifications](https://docs.github.com/en/integrations/how-tos/slack/customize-notifications)
