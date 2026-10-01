"""Notion: add a page to the team's tasks/docs data source."""

from __future__ import annotations

import os

import httpx

from ..models import ActionItem
from .base import Connector, DispatchResult, PlannedRequest

API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"


class NotionConnector(Connector):
    name = "notion"
    label = "Notion"
    env_vars = ("NOTION_API_KEY", "NOTION_DATA_SOURCE_ID")

    def build(self, item: ActionItem, meeting_title: str) -> PlannedRequest:
        properties: dict = {
            "Name": {"title": [{"text": {"content": item.title}}]},
            "Owner": {"rich_text": [{"text": {"content": item.owner}}]},
            "Status": {"select": {"name": "Not started"}},
            "Source": {"rich_text": [{"text": {"content": meeting_title}}]},
        }
        if item.due_date:
            properties["Due"] = {"date": {"start": item.due_date.isoformat()}}
        return PlannedRequest(
            method="POST",
            url=f"{API}/pages",
            headers={
                "Authorization": f"Bearer {os.environ.get('NOTION_API_KEY', '<NOTION_API_KEY>')}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            body={
                "parent": {"data_source_id": os.environ.get("NOTION_DATA_SOURCE_ID", "<NOTION_DATA_SOURCE_ID>")},
                "properties": properties,
                "children": [
                    {
                        "object": "block",
                        "type": "quote",
                        "quote": {"rich_text": [{"text": {"content": f"[{item.evidence.timestamp}] {item.evidence.quote}"}}]},
                    }
                ],
            },
        )

    def send(self, item: ActionItem, meeting_title: str) -> DispatchResult:
        req = self.build(item, meeting_title)
        resp = httpx.post(req.url, headers=req.headers, json=req.body, timeout=15)
        if resp.status_code != 200:
            return DispatchResult(destination=self.name, title=item.title, status="error", detail=resp.text[:300])
        return DispatchResult(destination=self.name, title=item.title, status="sent", detail="Page created", link=resp.json().get("url"))
