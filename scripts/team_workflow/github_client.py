"""Bounded GitHub REST/GraphQL client; no redirects or task-content logging."""
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from .check_pr import NoRedirect


class GitHubClient:
    def __init__(self, repo, token, project_token, transport=None):
        self.repo, self.token, self.project_token = repo, token, project_token
        self.transport = transport or self._network

    def _network(self, url, token, payload):
        request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)

    def _call(self, url, token, payload=None):
        for attempt in range(3):
            try:
                return self.transport(url, token, payload)
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def _rest(self, path):
        return self._call("https://api.github.com/repos/" + self.repo + path, self.token)

    def _pages(self, path):
        items = []
        for page in range(1, 31):
            batch = self._rest(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Unexpected paginated response.")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError("Repository metadata exceeds the pagination bound.")

    def _graph(self, query, variables):
        response = self._call("https://api.github.com/graphql", self.project_token,
                              {"query": query, "variables": variables})
        if response.get("errors") or not isinstance(response.get("data"), dict):
            raise ValueError("GraphQL request failed.")
        return response["data"]

    def list_pulls(self):
        return self._pages("/pulls?state=all")

    def reviews(self, number):
        return self._pages(f"/pulls/{number}/reviews")

    def issue(self, number):
        return self._rest(f"/issues/{number}")

    def project(self, project_id):
        data = self._graph("""query($id:ID!){node(id:$id){...on ProjectV2{
            owner{...on User{login} ...on Organization{login}}
            repositories(first:100){pageInfo{hasNextPage} nodes{nameWithOwner}}
            fields(first:100){pageInfo{hasNextPage} nodes{...on ProjectV2SingleSelectField{id name options{id name}}}}
        }}}""", {"id": project_id})["node"]
        if (not data or data["fields"]["pageInfo"]["hasNextPage"]
                or data["repositories"]["pageInfo"]["hasNextPage"]):
            raise ValueError("Project schema unavailable or incomplete.")
        fields = [f for f in data["fields"]["nodes"] if f.get("name") == "Status"]
        if len(fields) != 1:
            raise ValueError("Missing unique Status field.")
        return {"owner": data["owner"]["login"], "status_field": fields[0]["id"],
                "repositories": [r["nameWithOwner"] for r in data["repositories"]["nodes"]],
                "options": {o["name"]:o["id"] for o in fields[0]["options"]}}

    def item(self, issue_id, project_id):
        data = self._graph("""query($id:ID!){node(id:$id){...on Issue{
            projectItems(first:100){pageInfo{hasNextPage} nodes{id isArchived project{id}
                fieldValues(first:100){pageInfo{hasNextPage} nodes{
                    ...on ProjectV2ItemFieldSingleSelectValue{name field{...on ProjectV2SingleSelectField{name}}}
                }}}
            }
        }}}""", {"id": issue_id})["node"]
        if not data or data["projectItems"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Project item inventory unavailable or incomplete.")
        items = [i for i in data["projectItems"]["nodes"] if i["project"]["id"] == project_id]
        if not items:
            return None
        if len(items) != 1 or items[0]["isArchived"] or items[0]["fieldValues"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Project item is archived, ambiguous or incomplete; preserve it.")
        statuses = [v["name"] for v in items[0]["fieldValues"]["nodes"] if v.get("field", {}).get("name") == "Status"]
        if len(statuses) > 1:
            raise ValueError("Ambiguous Status values.")
        return {"id": items[0]["id"], "status": statuses[0] if statuses else "Not started"}

    def add_item(self, project_id, issue_id):
        data = self._graph("""mutation($project:ID!,$issue:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$issue}){item{id}}}""",
                           {"project": project_id, "issue": issue_id})
        return data["addProjectV2ItemById"]["item"]["id"]

    def set_status(self, project_id, item_id, field, option):
        self._graph("""mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){
            updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}
        }""", {"project": project_id, "item": item_id, "field": field, "option": option})
