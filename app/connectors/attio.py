"""Attio: create a task linked to the partner's company record (REST v2)."""

from __future__ import annotations

import json
import os

import httpx

from ..models import ActionItem
from .base import Connector, DispatchResult, PlannedRequest

API = "https://api.attio.com/v2"


def _member_ids() -> dict[str, str]:
    """Optional ATTIO_MEMBER_IDS='{"Sam Rivera": "<workspace-member-uuid>", ...}' to assign tasks."""
    try:
        return json.loads(os.environ.get("ATTIO_MEMBER_IDS", "{}"))
    except json.JSONDecodeError:
        return {}


class AttioConnector(Connector):
    name = "attio"
    label = "Attio"
    env_vars = ("ATTIO_API_KEY",)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ.get('ATTIO_API_KEY', '<ATTIO_API_KEY>')}", "Content-Type": "application/json"}

    def build(self, item: ActionItem, meeting_title: str, company_record_id: str | None = None) -> PlannedRequest:
        linked = []
        if item.linked_org:
            linked.append(
                {"target_object": "companies", "target_record_id": company_record_id or f"<record id of company '{item.linked_org}'>"}
            )
        assignees = []
        if (member := _member_ids().get(item.owner)) or not item.owner_is_external:
            assignees.append(
                {"referenced_actor_type": "workspace-member", "referenced_actor_id": member or f"<workspace member id of {item.owner}>"}
            )
        content = f"{item.title} — from “{meeting_title}”"
        if item.owner_is_external:
            content = f"Waiting on {item.owner}: {item.title} — from “{meeting_title}”"
            assignees = []
        return PlannedRequest(
            method="POST",
            url=f"{API}/tasks",
            headers=self._headers(),
            body={
                "data": {
                    "content": content[:2000],
                    "format": "plaintext",
                    "deadline_at": f"{item.due_date.isoformat()}T17:00:00.000Z" if item.due_date else None,
                    "is_completed": False,
                    "linked_records": linked,
                    "assignees": assignees,
                }
            },
        )

    def _find_company(self, name: str) -> str | None:
        resp = httpx.post(
            f"{API}/objects/companies/records/query",
            headers=self._headers(),
            json={"filter": {"name": name}, "limit": 1},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        records = resp.json().get("data") or []
        return records[0]["id"]["record_id"] if records else None

    def send(self, item: ActionItem, meeting_title: str) -> DispatchResult:
        record_id = self._find_company(item.linked_org) if item.linked_org else None
        req = self.build(item, meeting_title, company_record_id=record_id)
        if item.linked_org and not record_id:
            req.body["data"]["linked_records"] = []  # company not in the CRM yet; still create the task
        if any(a["referenced_actor_id"].startswith("<") for a in req.body["data"]["assignees"]):
            req.body["data"]["assignees"] = []
        resp = httpx.post(req.url, headers=req.headers, json=req.body, timeout=15)
        if resp.status_code not in (200, 201):
            return DispatchResult(destination=self.name, title=item.title, status="error", detail=resp.text[:300])
        return DispatchResult(destination=self.name, title=item.title, status="sent", detail="Task created in Attio")
