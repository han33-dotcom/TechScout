# Activate the TechScout GitHub workspace

## Current state

Foundation [PR #4](https://github.com/han33-dotcom/TechScout/pull/4) was approved by
`MohamadNasrallah` and merged into `main` on October 15, 2026. The reviewed sync
fix and live activation checks are still required.
The private [TechScout board](https://github.com/users/han33-dotcom/projects/2) and public
[Wiki](https://github.com/han33-dotcom/TechScout/wiki) were published on October 14, 2026.
On October 15, approved board Write grants and Slack PR/review subscriptions were
applied and verified. Expiring Projects credentials and the actual Project node IDs
were securely saved and verified later that day. Default-branch activation and the
human acceptance pilot remain pending. Board status is updated manually until activation.
Both repositories explicitly have `TEAM_SYNC_ENABLED=false`.
No Notion API token or synchronization is needed for the new setup.

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
Teammates still need to verify opening the board from their own accounts.
Repository permissions do not automatically confer Project access.

The authenticated Project API verified the owner, private visibility, required fields
and exactly one linked repository matching this project on October 15. Before
activation, teammates must also verify opening the board from their own accounts. Its private visibility does not make repository issues
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

Run **Team project sync** manually with dry_run checked. Inspect proposed transitions,
then run an approved fixture with dry_run unchecked. The worker consumes current
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
private meeting records without explicit approval. The references page still needs
its foundation-branch agent-instruction link updated to the normal implementation
branch after rollout changes settle.

## Slack subscriptions

GitHub's official Slack app is installed in **nsquared** (`nsquared-corp`), with its
GitHub installation restricted to Sentra and TechScout. Optional code-channel
management was unchecked; shared cloud AI review remains deferred.

In `#eece503p-techscout`, GitHub [confirmed the subscription](https://nsquared-corp.slack.com/archives/C0C8SHN88CT/p1792048965356559)
for `pulls` and `reviews`; `/github subscribe list` shows only this repository.
No existing subscription was removed. The configured command is:

```text
/github subscribe han33-dotcom/TechScout pulls reviews
```

Inspect `/github subscribe list` before changing subscriptions. Native messages handle PR/review events; no custom bot duplicates
those messages. Task descriptions and review details remain on GitHub. Receiving a
notification does not automatically launch a teammate's personal AI assistant.

## October 15 verification checkpoint

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
