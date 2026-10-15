"""Pure lifecycle planning; completion remains a human decision."""


def plan_status(current, issue_closed, pulls):
    if current in {"Done", "Blocked"} or issue_closed or not pulls:
        return None, []
    # Open or merged PRs replace abandoned attempts for the same task.
    pulls = [p for p in pulls if p["state"] != "closed" or p["merged"]]
    if not pulls:
        return None, ["A linked PR closed without merging; reconcile task scope and links."]
    active = [p for p in pulls if p["state"] == "open"]
    if any(p.get("changes_requested") for p in active):
        return "Changes requested", []
    if any(not p.get("draft") for p in active):
        return "In review", []
    if active:
        return "In progress", []
    if all(p["merged"] for p in pulls):
        return "Implemented", []
    return None, ["Unexpected PR state; no completion is assumed."]
