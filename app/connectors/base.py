"""Shared types for destination connectors.

Every connector does two things:
  1. build(item)  -> the exact HTTP request it would send (shown in the review UI)
  2. send(item)   -> performs it when credentials are configured, otherwise a dry run
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from ..models import ActionItem


class PlannedRequest(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: Any

    def redacted(self) -> PlannedRequest:
        safe = {k: ("<redacted>" if k.lower() in {"authorization", "x-api-key"} else v) for k, v in self.headers.items()}
        return self.model_copy(update={"headers": safe})


class DispatchResult(BaseModel):
    destination: str
    title: str
    status: Literal["sent", "dry_run", "error"]
    detail: str
    link: str | None = None


class Connector:
    name: str
    label: str
    env_vars: tuple[str, ...] = ()

    def configured(self) -> bool:
        import os

        return all(os.environ.get(v) for v in self.env_vars)

    def build(self, item: ActionItem, meeting_title: str) -> PlannedRequest:  # pragma: no cover - interface
        raise NotImplementedError

    def send(self, item: ActionItem, meeting_title: str) -> DispatchResult:  # pragma: no cover - interface
        raise NotImplementedError

    def dry_run(self, item: ActionItem, meeting_title: str) -> DispatchResult:
        req = self.build(item, meeting_title)
        missing = ", ".join(self.env_vars) or "nothing"
        return DispatchResult(
            destination=self.name,
            title=item.title,
            status="dry_run",
            detail=f"Would {req.method} {req.url} (set {missing} to send for real)",
        )


def description_for(item: ActionItem, meeting_title: str) -> str:
    lines = [
        f"From meeting: **{meeting_title}**",
        f"Owner: {item.owner}" + (" (external)" if item.owner_is_external else ""),
    ]
    if item.due_date:
        lines.append(f"Due: {item.due_date.isoformat()}")
    if item.linked_org:
        lines.append(f"Related to: {item.linked_org}")
    lines += ["", f"> [{item.evidence.timestamp}] {item.evidence.quote}", "", "_Created by Meeting Action Router_"]
    return "\n".join(lines)
