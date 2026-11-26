# Verify a collaborator’s setup

From a clean checkout of current main, record `git rev-parse HEAD`. Preserve local edits before switching branches. Install Python 3.11 dependencies and run the [offline checks](../CONTRIBUTING.md) on the collaborator’s own machine. Use that environment’s Python to run `-m pip check`.

| Assistant | How to check the project rules |
| --- | --- |
| Codex | Open the checkout and confirm the active instruction sources include the repository’s [AGENTS.md](../AGENTS.md). |
| Claude Code | In the checkout, inspect `/memory` or `/context`. [CLAUDE.md](../CLAUDE.md) imports `@AGENTS.md`. |
| Chat with manually supplied files | Supply the current rules, contribution guide and relevant task files. Record the revision and which files the chat could read. |

Ask the assistant for the branch target, test command, evidence required to close an issue, and how it handles unknown delivery fees. Compare its answers with the project rules. If they differ, check the working directory and loaded instructions, then refresh the session.

Record the OS, tool and version, commit, loaded files, commands, results and date on the onboarding issue. Mark the person verified after both the instruction check and local checks pass. Repeat when shared rules or the assistant change.

| Person | GitHub | Tool confirmed | OS | Local verification |
|---|---|---|---|---|
| Hasan | han33-dotcom | ChatGPT | Windows | Pending |
| Mohammad Nasrallah | MohamadNasrallah | Claude Code | Windows | Pending |
| Ahmad Karnib | Ahmad860187 | ChatGPT | Windows | Pending |
