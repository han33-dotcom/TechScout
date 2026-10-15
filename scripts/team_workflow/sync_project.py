"""Trusted metadata-only GitHub Projects reconciliation."""
import json
import os
import re
import sys

from .lifecycle import plan_status
from .policy import PROJECTS, APPROVERS, task_issue_number, visible_text

STATUSES = {"Not started", "In progress", "In review", "Changes requested", "Implemented", "Blocked", "Done"}


def task_pulls(repo, client):
    grouped = {}
    integration = PROJECTS[repo][2]
    for pull in client.list_pulls():
        if (pull["base"]["ref"] != integration
                or pull["user"]["login"] not in APPROVERS[repo]):
            continue
        urls = re.findall(r"^Task URL:\s*(.*?)\s*$", visible_text(pull.get("body") or ""), flags=re.M)
        if len(urls) != 1:
            continue
        number = task_issue_number(urls[0], repo)
        if number is None:
            continue
        latest = {}
        if pull["state"] == "open":
            for review in sorted(client.reviews(pull["number"]), key=lambda r:r["id"]):
                user = review.get("user") or {}
                actor = user.get("login")
                if (actor in APPROVERS[repo] and actor != pull["user"]["login"]
                        and user.get("type") == "User"
                        and review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}):
                    latest[actor] = review["state"]
        grouped.setdefault(number, []).append({"state": pull["state"], "draft": pull.get("draft", False),
            "merged": bool(pull.get("merged_at")), "changes_requested": "CHANGES_REQUESTED" in latest.values()})
    return grouped


def synchronize(repo, project_id, client, dry_run=True):
    if repo not in PROJECTS or not project_id:
        raise ValueError("Missing or unsupported project configuration.")
    config = client.project(project_id)
    if (config["owner"].lower() != repo.split("/")[0].lower()
            or [r.lower() for r in config["repositories"]] != [repo.lower()]
            or not STATUSES.issubset(config["options"])):
        raise ValueError("Project owner or Status options do not match the team configuration.")
    output = []
    candidates = task_pulls(repo, client)
    for number in sorted(candidates):
        # Fresh PR/review state defeats duplicate and out-of-order event payloads.
        pulls = candidates[number] if dry_run else task_pulls(repo, client).get(number, [])
        issue = client.issue(number)
        if (issue.get("number") != number or "pull_request" in issue
                or task_issue_number(issue.get("html_url", ""), repo) != number):
            raise ValueError("Task identity could not be verified in the configured repository.")
        item = client.item(issue["node_id"], project_id)
        current = item["status"] if item else "Not started"
        if current not in STATUSES:
            raise ValueError("Unknown task Status; preserve it for manual triage.")
        status, errors = plan_status(current, issue["state"] == "closed", pulls)
        result = {"issue": number, "current": current, "status": status, "errors": errors, "dry_run": dry_run}
        if status and status != current and not dry_run:
            item_id = item["id"] if item else client.add_item(project_id, issue["node_id"])
            client.set_status(project_id, item_id, config["status_field"], config["options"][status])
        output.append(result)
    return output


def main():
    from .github_client import GitHubClient
    client = GitHubClient(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"], os.environ["PROJECTS_TOKEN"])
    result = synchronize(os.environ["GITHUB_REPOSITORY"], os.environ["TEAM_PROJECT_ID"], client,
                         dry_run=os.environ.get("DRY_RUN", "true").lower() != "false")
    print(json.dumps(result, indent=2))
    return 1 if any(r["errors"] for r in result) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Do not expose provider payloads, task bodies or credentials in logs.
        print("Project synchronization failed; check configuration, permissions and API availability.")
        sys.exit(1)
