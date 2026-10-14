import unittest
from urllib.error import HTTPError
from scripts.team_workflow.github_client import GitHubClient


class ClientTests(unittest.TestCase):
    def test_project_status_schema_is_read_from_api(self):
        calls = []
        def transport(url, token, payload):
            calls.append((url, token, payload))
            return {"data": {"node": {"owner": {"login": "han33-dotcom"}, "repositories": {"pageInfo": {"hasNextPage": False}, "nodes": [{"nameWithOwner": "han33-dotcom/TechScout"}]}, "fields": {"pageInfo": {"hasNextPage": False}, "nodes": [{"id": "field", "name": "Status", "options": [{"id": "review", "name": "In review"}]}]}}}}
        result = GitHubClient("han33-dotcom/TechScout", "repo-token", "project-token", transport).project("project")
        self.assertEqual(result, {"owner": "han33-dotcom", "status_field": "field", "repositories": ["han33-dotcom/TechScout"], "options": {"In review": "review"}})
        self.assertEqual(calls[0][0:2], ("https://api.github.com/graphql", "project-token"))

    def test_graphql_errors_are_failure_not_empty_success(self):
        client = GitHubClient("han33-dotcom/TechScout", "r", "p", lambda *args: {"errors": [{"message": "synthetic"}]})
        with self.assertRaises(ValueError):
            client.project("project")

    def test_status_mutation_is_explicit_and_repo_token_not_used(self):
        calls = []
        def transport(url, token, payload):
            calls.append((token, payload["variables"]))
            return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}}}}
        GitHubClient("han33-dotcom/TechScout", "r", "p", transport).set_status("project", "item", "field", "review")
        self.assertEqual(calls, [("p", {"project": "project", "item": "item", "field": "field", "option": "review"})])

    def test_item_is_selected_only_from_the_configured_project(self):
        response = {"data": {"node": {"projectItems": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "foreign", "project": {"id": "other"}, "isArchived": False, "fieldValues": {"pageInfo": {"hasNextPage": False}, "nodes": []}},
            {"id": "item", "project": {"id": "project"}, "isArchived": False, "fieldValues": {"pageInfo": {"hasNextPage": False}, "nodes": [{"name": "Blocked", "field": {"name": "Status"}}]}}
        ]}}}}
        client = GitHubClient("han33-dotcom/TechScout", "r", "p", lambda *args:response)
        self.assertEqual(client.item("issue", "project"), {"id": "item", "status": "Blocked"})
        response["data"]["node"]["projectItems"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaises(ValueError):
            client.item("issue", "project")

    def test_permanent_api_failure_is_not_retried(self):
        calls = []
        def transport(url, token, payload):
            calls.append(url)
            raise HTTPError(url, 401, "synthetic", {}, None)
        with self.assertRaises(HTTPError):
            GitHubClient("han33-dotcom/TechScout", "r", "p", transport).issue(6)
        self.assertEqual(len(calls), 1)
