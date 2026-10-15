import unittest
from scripts.team_workflow.policy import validate_pr_metadata
from scripts.team_workflow.check_pr import check_pr_event
try:
    from . import test_runner
except ImportError:
    import test_runner


def body(url, extra=""):
    return "Project: Sentra\nTask URL: " + url + "\n" + extra + "## What & why\nFix a filter.\n## How tested\nRegression checks passed.\n## Risks / limitations\nOffline validation only.\n"


class GitHubTaskTests(unittest.TestCase):
    def test_same_repository_issue_is_accepted(self):
        self.assertEqual(validate_pr_metadata(body("https://github.com/han33-dotcom/Sentra/issues/16"), set(), "han33-dotcom/Sentra", "develop"), [])

    def test_other_repository_pr_url_and_url_confusion_fail(self):
        for url in ["https://github.com/han33-dotcom/TechScout/issues/16", "https://github.com/han33-dotcom/Sentra/pull/16", "https://github.com.evil.test/han33-dotcom/Sentra/issues/16", "https://user@github.com/han33-dotcom/Sentra/issues/16", "https://github.com:443/han33-dotcom/Sentra/issues/16", "https://github.com/han33-dotcom/Sentra/issues/0", "https://github.com/han33-dotcom/Sentra/issues/16?q=x", "https://github.com/han33-dotcom/Sentra/issues/16#x", "https://github.com/han33-dotcom/Sentra/issues/16/", "http://github.com/han33-dotcom/Sentra/issues/16"]:
            with self.subTest(url=url):
                self.assertTrue(validate_pr_metadata(body(url), set(), "han33-dotcom/Sentra", "develop"))

    def test_optional_task_id_matches_native_issue_number(self):
        url = "https://github.com/han33-dotcom/Sentra/issues/16"
        self.assertEqual(validate_pr_metadata(body(url, "Task ID: SEN-16\n"), set(), "han33-dotcom/Sentra", "develop"), [])
        self.assertTrue(validate_pr_metadata(body(url, "Task ID: SEN-17\n"), set(), "han33-dotcom/Sentra", "develop"))

    def test_an_issue_endpoint_that_resolves_to_a_pr_is_rejected(self):
        event, pr, files, fetch = test_runner.RunnerTests().snapshot()
        def wrapped(path):
            if path == "/issues/16":
                return {"number": 16, "pull_request": {"url": "synthetic"}}
            return fetch(path)
        self.assertIn("Task URL must reference an issue, not a pull request.", check_pr_event(event, "han33-dotcom/Sentra", wrapped))

    def test_issue_identity_is_verified(self):
        event, pr, files, fetch = test_runner.RunnerTests().snapshot()
        def wrapped(path):
            if path == "/issues/16":
                return {"number": 17}
            return fetch(path)
        self.assertIn("Task issue identity could not be verified.", check_pr_event(event, "han33-dotcom/Sentra", wrapped))

    def test_pr_description_cannot_request_auto_closure(self):
        url = "https://github.com/han33-dotcom/Sentra/issues/16"
        for directive in ["Closes #16", "Closes: #16", "**Closes**: #16", "fixes han33-dotcom/Sentra#16", "Resolves " + url]:
            self.assertIn("Use Related to instead of automatic issue-closing keywords; human acceptance controls Done.", validate_pr_metadata(body(url) + directive, set(), "han33-dotcom/Sentra", "develop"))
