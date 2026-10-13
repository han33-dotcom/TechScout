"""Deterministic PR metadata and independently approved exemption policy."""
import re
from urllib.parse import parse_qsl, unquote, urlsplit

PROJECTS = {
    "han33-dotcom/Sentra": ("Sentra", "SEN", "develop"),
    "han33-dotcom/TechScout": ("TechScout", "TECH", "main"),
}
APPROVERS = {
    "han33-dotcom/Sentra": {"han33-dotcom", "MohamadNasrallah", "mhmdaboulhoda"},
    "han33-dotcom/TechScout": {"han33-dotcom", "MohamadNasrallah", "Ahmad860187"},
}
EXEMPTIONS = {"workflow-maintenance", "release"}
WORKFLOW_FILES = {"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", ".gitignore"}
WORKFLOW_PREFIXES = ("docs/", ".github/", "scripts/team_workflow/", "tests/team_workflow/")
PAGE_ID = r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})"


def visible_text(body):
    """Ignore comments and fenced examples when reading declared metadata."""
    lines, fence = [], False
    for line in re.sub(r"<!--[\s\S]*?(?:-->|$)", "", body or "").splitlines():
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            fence = not fence
        elif not fence:
            lines.append(line)
    return "\n".join(lines)


def valid_task_url(value):
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname in {"notion.so", "www.notion.so", "notion.com", "www.notion.com", "app.notion.com"}
            and not url.username and not url.password and url.port is None
            and re.search(PAGE_ID + r"$", unquote(url.path)) is not None
            and all(key == "pvs" and val.isdigit() for key, val in parse_qsl(url.query, keep_blank_values=True))
            and not url.fragment
        )
    except ValueError:
        return False


def validate_pr_metadata(body: str, labels: set[str], repo: str, base: str) -> list[str]:
    """Labels must first pass approved_exemptions; this function does no network IO."""
    if repo not in PROJECTS:
        return ["Unsupported repository."]
    project, prefix, integration = PROJECTS[repo]
    text, errors = visible_text(body), []
    if base != integration and not (repo == "han33-dotcom/Sentra" and base == "main" and "release" in labels):
        errors.append("PR target does not match the project integration/release flow.")
    if "release" in labels and not (repo == "han33-dotcom/Sentra" and base == "main"):
        errors.append("Release exemption applies only to Sentra releases into main.")
    declared = re.findall(r"^Project:\s*(.*?)\s*$", text, flags=re.M)
    if declared != [project]:
        errors.append("Declare exactly one matching Project line.")
    ids = re.findall(r"^Task ID:\s*(.*?)\s*$", text, flags=re.M)
    if len(ids) > 1 or (ids and not re.fullmatch(re.escape(prefix) + r"-\d+", ids[0])):
        errors.append("Task ID must use the project's prefix and numeric ID.")
    urls = re.findall(r"^Task URL:\s*(.*?)\s*$", text, flags=re.M)
    exempt = bool(labels & EXEMPTIONS)
    if urls:
        if len(urls) != 1 or not valid_task_url(urls[0]):
            errors.append("Task URL must be one valid HTTPS Notion page link.")
    elif not exempt:
        errors.append("Provide a Task URL or an independently approved scoped exemption.")
    sections = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading[1]
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    for heading in ["What & why", "How tested", "Risks / limitations"]:
        value = "\n".join(sections.get(heading, [])).strip()
        if not value or value.lower() in {"tbd", "todo", "...", "fill in", "n/a"}:
            errors.append("Complete the " + heading + " section.")
    return errors


def approved_exemptions(pr, filenames, timeline, repo):
    """Require the latest label application by another configured human teammate."""
    errors, approved = [], set()
    active = {item["name"] for item in pr.get("labels", [])} & EXEMPTIONS
    for label in active:
        applications = [event for event in timeline
                        if event.get("event") in {"labeled", "unlabeled"}
                        and event.get("label", {}).get("name") == label]
        latest = applications[-1] if applications else {}
        actor = latest.get("actor", {}).get("login")
        if (latest.get("event") != "labeled" or actor not in APPROVERS.get(repo, set())
                or actor == pr["user"]["login"]):
            errors.append("Exemption requires a different configured teammate to apply its label.")
            continue
        if label == "workflow-maintenance":
            if not filenames or not all(
                path in WORKFLOW_FILES or path.startswith(WORKFLOW_PREFIXES)
                for path in filenames
            ):
                errors.append("Workflow exemption cannot cover application files or an empty diff.")
                continue
        if label == "release":
            if not (
                repo == "han33-dotcom/Sentra" and pr["base"]["ref"] == "main"
                and pr["head"]["ref"] == "develop"
                and pr["head"].get("repo", {}).get("full_name") == repo
            ):
                errors.append("Release exemption requires same-repository develop to main.")
                continue
        approved.add(label)
    return approved, errors
