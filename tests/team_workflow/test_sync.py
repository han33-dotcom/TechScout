import unittest
from scripts.team_workflow.sync_project import synchronize


class FixtureClient:
    def __init__(self):
        self.status = "Not started"
        self.closed = False
        self.head_reads = 0
        self.changes = False
        self.writes = []
        self.project_owner = "han33-dotcom"
        self.pulls = [{"number": 2, "state": "open", "draft": False, "merged_at": None, "user": {"login": "han33-dotcom"}, "base": {"ref": "main"}, "body": "Project: TechScout\nTask URL: https://github.com/han33-dotcom/TechScout/issues/6\n"}]
    def project(self, project_id):
        return {"owner": self.project_owner, "status_field": "field", "repositories": ["han33-dotcom/TechScout"], "options": {s:s for s in ["Not started", "In progress", "In review", "Changes requested", "Implemented", "Blocked", "Done"]}}
    def list_pulls(self):
        self.head_reads += 1
        return self.pulls
    def reviews(self, number):
        return [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "MohamadNasrallah", "type": "User"}}] if self.changes else []
    def issue(self, number):
        return {"number": number, "node_id": "issue", "html_url": "https://github.com/han33-dotcom/TechScout/issues/6", "state": "closed" if self.closed else "open"}
    def item(self, issue_id, project_id):
        return {"id": "item", "status": self.status}
    def add_item(self, project_id, issue_id):
        self.writes.append("add")
        return "item"
    def set_status(self, project_id, item_id, field, option):
        self.writes.append(option)
        self.status = option


class SyncTests(unittest.TestCase):
    def test_dry_run_does_not_write_and_reports_planned_status(self):
        client = FixtureClient()
        output = synchronize("han33-dotcom/TechScout", "project", client, dry_run=True)
        self.assertEqual(output[0]["status"], "In review")
        self.assertEqual(client.writes, [])

    def test_duplicate_events_write_only_changed_values(self):
        client = FixtureClient()
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.writes, ["In review"])
        self.assertGreaterEqual(client.head_reads, 3)

    def test_manual_done_blocked_and_closed_issue_never_write(self):
        for status in ["Done", "Blocked"]:
            client = FixtureClient(); client.status = status
            synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
            self.assertEqual(client.writes, [])
        client = FixtureClient(); client.closed = True
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.writes, [])

    def test_changes_requested_and_merging_do_not_close_issue(self):
        client = FixtureClient(); client.changes = True
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.status, "Changes requested")
        client.changes = False; client.pulls[0].update(state="closed", merged_at="2026-10-14")
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.status, "Implemented")
        self.assertFalse(client.closed)

    def test_wrong_project_owner_and_wrong_repo_link_fail_closed(self):
        client = FixtureClient(); client.project_owner = "other"
        with self.assertRaises(ValueError):
            synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.writes, [])
        client = FixtureClient(); client.pulls[0]["body"] = "Task URL: https://github.com/han33-dotcom/Sentra/issues/6"
        self.assertEqual(synchronize("han33-dotcom/TechScout", "project", client, dry_run=False), [])
        self.assertEqual(client.writes, [])

    def test_manual_change_during_reconciliation_is_preserved(self):
        client = FixtureClient()
        original = client.list_pulls
        def changed():
            pulls = original()
            if client.head_reads >= 2:
                client.status = "Done"
            return pulls
        client.list_pulls = changed
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.writes, [])

    def test_bot_and_author_review_are_not_human_change_requests(self):
        client = FixtureClient()
        client.reviews = lambda n: [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "han33-dotcom", "type": "User"}}, {"id": 2, "state": "CHANGES_REQUESTED", "user": {"login": "bot[bot]", "type": "Bot"}}]
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.status, "In review")

    def test_same_owner_project_linked_to_other_repo_is_rejected(self):
        client = FixtureClient()
        original = client.project
        def foreign(project_id):
            config = original(project_id)
            config["repositories"] = ["han33-dotcom/Sentra"]
            return config
        client.project = foreign
        with self.assertRaises(ValueError):
            synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.writes, [])

    def test_comment_review_does_not_clear_outstanding_changes_request(self):
        client = FixtureClient()
        user = {"login": "MohamadNasrallah", "type": "User"}
        client.reviews = lambda n: [{"id": 1, "state": "CHANGES_REQUESTED", "user": user}, {"id": 2, "state": "COMMENTED", "user": user}]
        synchronize("han33-dotcom/TechScout", "project", client, dry_run=False)
        self.assertEqual(client.status, "Changes requested")

    def test_unconfigured_pr_author_cannot_change_task_status(self):
        client = FixtureClient()
        client.pulls[0]["user"]["login"] = "outside-contributor"
        self.assertEqual(synchronize("han33-dotcom/TechScout", "project", client, dry_run=False), [])
        self.assertEqual(client.writes, [])
