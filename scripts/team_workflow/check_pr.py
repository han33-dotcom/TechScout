"""Read-only check of current PR metadata using the trusted base-branch policy."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

if __package__:
    from .policy import PROJECTS, approved_exemptions, validate_pr_metadata, visible_text, task_issue_number
else:
    from policy import PROJECTS, approved_exemptions, validate_pr_metadata, visible_text, task_issue_number


def pages(fetch, path):
    items = []
    for page in range(1, 31):
        batch = fetch(f"{path}?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Unexpected paginated API response.")
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise ValueError("Metadata exceeds the supported pagination bound.")


def check_pr_event(event, repo, fetch):
    if repo not in PROJECTS or event.get("repository", {}).get("full_name") != repo:
        return ["Event repository does not match configured repository."]
    event_pr = event.get("pull_request", {})
    number = event_pr.get("number", event.get("number"))
    if not isinstance(number, int) or number < 1:
        return ["No valid pull request number."]
    pr = fetch(f"/pulls/{number}")
    if pr.get("state") != "open":
        return ["PR is no longer open."]
    if (pr["head"]["sha"] != event_pr.get("head", {}).get("sha")
            or pr["base"]["sha"] != event_pr.get("base", {}).get("sha")):
        return ["Stale event: rerun using a fresh PR event."]
    files = pages(fetch, f"/pulls/{number}/files")
    if len(files) != pr.get("changed_files"):
        return ["Incomplete file inventory; cannot validate exemptions."]
    paths = [path for item in files for path in
             [item["filename"], item.get("previous_filename")] if path]
    timeline = pages(fetch, f"/issues/{number}/timeline")
    labels, errors = approved_exemptions(pr, paths, timeline, repo)
    errors.extend(validate_pr_metadata(pr.get("body") or "", labels, repo, pr["base"]["ref"]))
    urls = re.findall(r"^Task URL:\s*(.*?)\s*$", visible_text(pr.get("body") or ""), flags=re.M)
    if len(urls) == 1:
        task_number = task_issue_number(urls[0], repo)
        if task_number is not None:
            issue = fetch(f"/issues/{task_number}")
            if issue.get("number") != task_number:
                errors.append("Task issue identity could not be verified.")
            if "pull_request" in issue:
                errors.append("Task URL must reference an issue, not a pull request.")
    latest = fetch(f"/pulls/{number}")
    if (latest["head"]["sha"] != pr["head"]["sha"]
            or latest["base"]["sha"] != pr["base"]["sha"]
            or latest.get("body") != pr.get("body")
            or latest.get("labels") != pr.get("labels")
            or latest.get("state") != "open"):
        errors.append("PR changed during validation; use a fresh event.")
    return errors


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if repo not in PROJECTS:
        print("PR policy: unsupported repository.")
        return 1
    opener = build_opener(NoRedirect())
    def fetch(path):
        request = Request("https://api.github.com/repos/" + repo + path,
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"})
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    try:
        errors = check_pr_event(event, repo, fetch)
    except HTTPError as error:
        errors = ["GitHub metadata request failed (HTTP " + str(error.code) + ")."]
    except (URLError, ValueError, KeyError, TypeError):
        errors = ["GitHub metadata could not be validated; no success is assumed."]
    for error in errors:
        print("PR policy: " + error)
    if not errors:
        print("PR policy: metadata and exemption checks passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
