"""Linear: create an issue through the GraphQL API (https://api.linear.app/graphql)."""

from __future__ import annotations

import os

import httpx

from ..models import ActionItem
from .base import Connector, DispatchResult, PlannedRequest, description_for

ENDPOINT = "https://api.linear.app/graphql"

ISSUE_CREATE = """mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url }
  }
}"""

# Linear priorities: 0 none, 1 urgent, 2 high, 3 medium, 4 low
PRIORITY = {"high": 2, "medium": 3, "low": 4}


class LinearConnector(Connector):
    name = "linear"
    label = "Linear"
    env_vars = ("LINEAR_API_KEY", "LINEAR_TEAM_ID")

    def build(self, item: ActionItem, meeting_title: str) -> PlannedRequest:
        variables: dict = {
            "input": {
                "teamId": os.environ.get("LINEAR_TEAM_ID", "<LINEAR_TEAM_ID>"),
                "title": item.title,
                "description": description_for(item, meeting_title),
                "priority": PRIORITY[item.priority],
            }
        }
        if item.due_date:
            variables["input"]["dueDate"] = item.due_date.isoformat()
        return PlannedRequest(
            method="POST",
            url=ENDPOINT,
            headers={"Authorization": os.environ.get("LINEAR_API_KEY", "<LINEAR_API_KEY>"), "Content-Type": "application/json"},
            body={"query": ISSUE_CREATE, "variables": variables},
        )

    def send(self, item: ActionItem, meeting_title: str) -> DispatchResult:
        req = self.build(item, meeting_title)
        resp = httpx.post(req.url, headers=req.headers, json=req.body, timeout=15)
        data = resp.json()
        result = (data.get("data") or {}).get("issueCreate") or {}
        if resp.status_code != 200 or not result.get("success"):
            return DispatchResult(destination=self.name, title=item.title, status="error", detail=str(data.get("errors", resp.text))[:300])
        issue = result["issue"]
        return DispatchResult(
            destination=self.name, title=item.title, status="sent", detail=f"Created {issue['identifier']}", link=issue["url"]
        )
