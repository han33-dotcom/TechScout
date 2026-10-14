# Activate the TechScout GitHub workspace

## Current state

Repository changes are prepared in the workflow foundation PR. Project boards,
Wiki publishing, Slack app subscriptions and human acceptance pilot are pending.
No Notion API token or synchronization is needed for the new setup.

## Project board

1. In the `han33-dotcom` account, create a **private** GitHub Project named `TechScout`.
   Link only `han33-dotcom/TechScout` to it. Give this team's verified collaborators
   write access to the board; repository access does not automatically grant it.
2. Use a board grouped by **Status**, plus an All tasks table and an Assignee-me view.
   Set Status options to Not started, In progress, In review, Changes requested,
   Implemented, Blocked and Done. Rename defaults rather than maintaining synonyms.
3. Add Priority (High/Medium/Low), Due date (date), Reviewer (text GitHub handle),
   and Blocked reason (text). Use the native Assignees field for the task owner.
   Acceptance checklists and completion evidence belong in the issue body.
4. Configure native auto-add for `repo:han33-dotcom/TechScout is:issue` and set newly
   added items to Not started. Add existing issues deliberately and preserve their
   imported status, priority and deadline. Disable workflows that automatically set
   Done on issue closure or PR merge; completion remains a human acceptance decision.
5. Verify the new board's visibility, collaborator access and issue inventory before
   importing private historical content. The repository's issues remain public even
   when the Project board is private.

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
verified. Sentra therefore needs the normal reviewed develop-to-main release before
its default-branch automation can run. Do not bypass branch protections.

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

1. Verify Wiki is enabled. Public-repository Wikis are public; keep editing restricted
   to repository collaborators. Do not publish participant data, private contacts,
   university attachments or original private meeting records without explicit approval.
2. Create the initial Home page in GitHub's Wiki tab; clone the separate Wiki repository
   on your machine. Copy `.github/wiki-seed/*.md` into its root, inspect the content and
   commit/push it. These are reviewed bootstrap pages, not a second maintained copy.
3. Verify Home, sidebar, project background, meeting index, template and references.
   Once published, edit general material in the Wiki and remove bootstrap copies in a
   follow-up PR. Technical files stay authoritative in `docs/`; AGENTS.md stays at root.

## Slack subscriptions

In **nsquared**, install/authorize GitHub's Slack app and grant the intended repository
access. In this project's channel, run:

```text
/github subscribe han33-dotcom/TechScout pulls reviews
```

Inspect `/github subscribe list` and disable unwanted subscriptions only after checking
what already exists. Native messages handle PR/review events; no custom bot duplicates
those messages. Task descriptions and review details remain on GitHub. Receiving a
notification does not automatically launch a teammate's personal AI assistant.

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
