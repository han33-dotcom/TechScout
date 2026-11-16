# TechScout team workflow

Use the [full-product backlog](https://github.com/han33-dotcom/TechScout/issues/36).
Tasks are shared across the team. Each task has one active writer and lists its prerequisites
and acceptance criteria.

1. Check that prerequisites are complete and nobody has claimed the issue. Assign it to
   yourself when you start. Link your branch or PR on the issue.
2. Work in a focused branch from current main or the declared stacked base. If you need a
   change from another branch, coordinate it on GitHub and preserve its author's commits.
3. Open a PR and record checks, observable acceptance results and known limits. Optional
   review happens on the PR. Address material defects before merging.
4. The author can merge after GitHub's required checks pass and the acceptance criteria
   are demonstrated. Use the regular GitHub merge flow. The author records the merged
   result and closes the issue once the task is complete.

Track status through issues. An open issue with complete prerequisites and no assignee is
ready. An assigned open issue is in progress unless the issue records a blocker. A closed
issue with acceptance evidence is done. The existing Project can mirror those states manually.

## Main branch protection

Main requires PRs and up-to-date GitHub Actions checks named test and build. The test job
runs SQLite and PostgreSQL suites, then migrations and fixture loading. The build job
builds the Docker image. Force pushes and branch deletion are blocked. Check the active
[main ruleset](https://github.com/han33-dotcom/TechScout/rules/24940148) if GitHub reports
a merge blocker.

## Workflow history

[Task #37](https://github.com/han33-dotcom/TechScout/issues/37) replaces mandatory approvals
and the separate acceptance queue. The PR metadata check and automatic Project
reconciliation are retired. [Issue #31](https://github.com/han33-dotcom/TechScout/issues/31)
and [PR #34](https://github.com/han33-dotcom/TechScout/pull/34) preserve the superseded
human-review pilot. Its unfinished stages remain unverified.
