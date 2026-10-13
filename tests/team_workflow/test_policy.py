import unittest

from scripts.team_workflow.policy import validate_pr_metadata, approved_exemptions


def body(project="Sentra", url="https://www.notion.so/0123456789abcdef0123456789abcdef"):
    return (
        f"Project: {project}\nTask URL: {url}\n"
        "## What & why\nAdd a date filter to the dashboard.\n"
        "## How tested\nOffline regression tests passed.\n"
        "## Risks / limitations\nNo live provider checks were run.\n"
    )


def pr(repo="han33-dotcom/Sentra", base="develop", head="feature/test"):
    return {"user": {"login": "han33-dotcom"}, "base": {"ref": base},
            "head": {"ref": head, "repo": {"full_name": repo}},
            "labels": [{"name": "workflow-maintenance"}]}


class PolicyTests(unittest.TestCase):
    def test_valid_task_links_and_project_metadata(self):
        for repo, project, base in [
            ("han33-dotcom/Sentra", "Sentra", "develop"),
            ("han33-dotcom/TechScout", "TechScout", "main"),
        ]:
            with self.subTest(repo=repo):
                self.assertEqual(validate_pr_metadata(body(project), set(), repo, base), [])

    def test_missing_link_fails(self):
        text = body().replace("Task URL: https://www.notion.so/0123456789abcdef0123456789abcdef\n", "")
        self.assertTrue(validate_pr_metadata(text, set(), "han33-dotcom/Sentra", "develop"))

    def test_bad_link_domains_credentials_queries_and_missing_page_id_fail(self):
        for url in [
            "https://notion.so.evil.test/0123456789abcdef0123456789abcdef",
            "https://user:password@notion.so/0123456789abcdef0123456789abcdef",
            "https://www.notion.so/", "http://www.notion.so/0123456789abcdef0123456789abcdef",
            "https://www.notion.so/0123456789abcdef0123456789abcdef?redirect=https://evil.test",
        ]:
            with self.subTest(url=url):
                self.assertTrue(validate_pr_metadata(body(url=url), set(), "han33-dotcom/Sentra", "develop"))

    def test_notion_page_urls_with_titles_dashes_and_pvs_are_supported(self):
        for url in [
            "https://www.notion.so/Task-0123456789abcdef0123456789abcdef?pvs=4",
            "https://app.notion.com/p/01234567-89ab-cdef-0123-456789abcdef",
        ]:
            self.assertEqual(validate_pr_metadata(body(url=url), set(), "han33-dotcom/Sentra", "develop"), [])

    def test_wrong_project_id_unknown_repository_and_wrong_base_fail(self):
        for text, repo, base in [
            (body("TechScout"), "han33-dotcom/Sentra", "develop"),
            (body()+"Task ID: TECH-3\n", "han33-dotcom/Sentra", "develop"),
            (body(), "other/Sentra", "develop"),
            (body(), "han33-dotcom/Sentra", "main"),
        ]:
            self.assertTrue(validate_pr_metadata(text, set(), repo, base))

    def test_required_sections_and_placeholders_fail(self):
        for heading in ["What & why", "How tested", "Risks / limitations"]:
            self.assertTrue(validate_pr_metadata(body().replace(f"## {heading}", "## Other"), set(), "han33-dotcom/Sentra", "develop"))
        self.assertTrue(validate_pr_metadata(body().replace("Add a date filter to the dashboard.", "TBD"), set(), "han33-dotcom/Sentra", "develop"))

    def test_comments_cannot_supply_task_or_required_sections(self):
        self.assertTrue(validate_pr_metadata("<!--"+body()+"-->", set(), "han33-dotcom/Sentra", "develop"))

    def test_unclosed_comment_cannot_supply_metadata(self):
        self.assertTrue(validate_pr_metadata("<!--\n"+body(), set(), "han33-dotcom/Sentra", "develop"))

    def test_duplicate_task_lines_fail(self):
        self.assertTrue(validate_pr_metadata(body()+"Task URL: https://notion.so/0123456789abcdef0123456789abcdef\n", set(), "han33-dotcom/Sentra", "develop"))

    def test_approved_maintenance_and_release_can_omit_task(self):
        text = body().replace("Task URL: https://www.notion.so/0123456789abcdef0123456789abcdef\n", "")
        self.assertEqual(validate_pr_metadata(text, {"workflow-maintenance"}, "han33-dotcom/Sentra", "develop"), [])
        self.assertEqual(validate_pr_metadata(text, {"release"}, "han33-dotcom/Sentra", "main"), [])
        self.assertTrue(validate_pr_metadata(text, {"release"}, "han33-dotcom/TechScout", "main"))

    def test_exemption_requires_another_verified_team_member(self):
        event = [{"event": "labeled", "label": {"name": "workflow-maintenance"},
                  "actor": {"login": "MohamadNasrallah"}}]
        labels, errors = approved_exemptions(pr(), ["docs/team-workflow.md"], event, "han33-dotcom/Sentra")
        self.assertEqual(labels, {"workflow-maintenance"})
        self.assertEqual(errors, [])
        for actor in ["han33-dotcom", "unknown", "some-bot[bot]"]:
            event[0]["actor"]["login"] = actor
            labels, errors = approved_exemptions(pr(), ["docs/team-workflow.md"], event, "han33-dotcom/Sentra")
            self.assertEqual(labels, set())
            self.assertTrue(errors)

    def test_maintenance_cannot_exempt_product_code_or_renamed_product_files(self):
        event = [{"event": "labeled", "label": {"name": "workflow-maintenance"},
                  "actor": {"login": "MohamadNasrallah"}}]
        for files in [["src/sentra/auth.py"], ["docs/workflow.md", "src/sentra/auth.py"], []]:
            labels, errors = approved_exemptions(pr(), files, event, "han33-dotcom/Sentra")
            self.assertEqual(labels, set())
            self.assertTrue(errors)

    def test_latest_label_application_controls_authorization(self):
        events = [
            {"event": "labeled", "label": {"name": "workflow-maintenance"}, "actor": {"login": "MohamadNasrallah"}},
            {"event": "unlabeled", "label": {"name": "workflow-maintenance"}, "actor": {"login": "han33-dotcom"}},
            {"event": "labeled", "label": {"name": "workflow-maintenance"}, "actor": {"login": "han33-dotcom"}},
        ]
        labels, errors = approved_exemptions(pr(), ["docs/a.md"], events, "han33-dotcom/Sentra")
        self.assertEqual(labels, set())
        self.assertTrue(errors)

    def test_release_requires_same_repo_develop_to_main(self):
        event = [{"event": "labeled", "label": {"name": "release"}, "actor": {"login": "MohamadNasrallah"}}]
        release = pr(base="main", head="develop")
        release["labels"] = [{"name": "release"}]
        self.assertEqual(approved_exemptions(release, ["src/sentra/api.py"], event, "han33-dotcom/Sentra"), ({"release"}, []))
        for key in ["branch", "fork"]:
            bad = pr(base="main", head="feature/test" if key=="branch" else "develop")
            bad["labels"] = [{"name": "release"}]
            if key=="fork":
                bad["head"]["repo"]["full_name"] = "other/Sentra"
            labels, errors = approved_exemptions(bad, ["src/sentra/api.py"], event, "han33-dotcom/Sentra")
            self.assertEqual(labels, set())
            self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
