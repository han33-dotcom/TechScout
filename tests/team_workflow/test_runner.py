import unittest
from scripts.team_workflow.check_pr import check_pr_event


class RunnerTests(unittest.TestCase):
    def snapshot(self):
        event = {"repository": {"full_name": "han33-dotcom/Sentra"},
                 "pull_request": {"number": 15, "head": {"sha": "current"},
                                  "base": {"sha": "base"}}}
        pr = {"number": 15, "state": "open", "body":
              "Project: Sentra\nTask URL: https://notion.so/0123456789abcdef0123456789abcdef\n"
              "## What & why\nFix the filter.\n## How tested\nTests passed.\n"
              "## Risks / limitations\nNo live checks.\n",
              "changed_files": 1, "labels": [], "user": {"login": "han33-dotcom"},
              "base": {"ref": "develop", "sha": "base"},
              "head": {"sha": "current", "ref": "feat/test",
                       "repo": {"full_name": "han33-dotcom/Sentra"}}}
        files = [{"filename": "src/sentra/viewer_app.py"}]
        def fetch(path):
            if path == "/pulls/15":
                return pr
            if "/files?" in path:
                return files
            if "/timeline?" in path:
                return []
            raise AssertionError("Unexpected API resource")
        return event, pr, files, fetch

    def test_current_api_metadata_is_used_instead_of_event_body(self):
        event, pr, files, fetch = self.snapshot()
        event["pull_request"]["body"] = "stale invalid body"
        self.assertEqual(check_pr_event(event, "han33-dotcom/Sentra", fetch), [])

    def test_stale_head_base_or_wrong_repository_fail_closed(self):
        for change in ["head", "base", "repo"]:
            event, pr, files, fetch = self.snapshot()
            if change in {"head", "base"}:
                event["pull_request"][change]["sha"] = "obsolete"
            else:
                event["repository"]["full_name"] = "other/Sentra"
            self.assertTrue(check_pr_event(event, "han33-dotcom/Sentra", fetch))

    def test_missing_files_and_api_failures_are_not_success(self):
        event, pr, files, fetch = self.snapshot()
        pr["changed_files"] = 2
        self.assertTrue(check_pr_event(event, "han33-dotcom/Sentra", fetch))
        def failing_fetch(path):
            raise RuntimeError("synthetic API failure")
        with self.assertRaises(RuntimeError):
            check_pr_event(event, "han33-dotcom/Sentra", failing_fetch)

    def test_renamed_application_file_denies_workflow_exemption(self):
        event, pr, files, fetch = self.snapshot()
        pr["labels"] = [{"name": "workflow-maintenance"}]
        files[0] = {"filename": "docs/new.md", "previous_filename": "src/sentra/auth.py"}
        def wrapped(path):
            if "/timeline?" in path:
                return [{"event": "labeled", "label": {"name": "workflow-maintenance"},
                         "actor": {"login": "MohamadNasrallah"}}]
            return fetch(path)
        self.assertTrue(check_pr_event(event, "han33-dotcom/Sentra", wrapped))


if __name__ == "__main__":
    unittest.main()
