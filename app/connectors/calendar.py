"""Calendar: produce an .ics hold the EA can drop into Google Calendar or Outlook.

There is deliberately no auto-send here: booking time on other people's calendars
should stay a human click, so the router prepares the invite and links to it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

from ..models import ActionItem
from .base import Connector, DispatchResult, PlannedRequest


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(item: ActionItem, meeting_title: str, now: datetime | None = None) -> str:
    day = item.due_date or (date.today() + timedelta(days=7))
    # Default hold: 30 minutes at 11:00 ET (15:00 UTC); the organizer adjusts before sending.
    start = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    uid = hashlib.sha1(f"{meeting_title}|{item.title}".encode()).hexdigest()[:16]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Meeting Action Router//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}@meeting-action-router",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{_escape(item.title)}",
        f"DESCRIPTION:{_escape(f'Follow-up from {meeting_title}. Owner: {item.owner}. Evidence: {item.evidence.quote}')}",
        "STATUS:TENTATIVE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


class CalendarConnector(Connector):
    name = "calendar"
    label = "Calendar"
    env_vars = ()

    def build(self, item: ActionItem, meeting_title: str) -> PlannedRequest:
        return PlannedRequest(
            method="DOWNLOAD", url="invite.ics", headers={"Content-Type": "text/calendar"}, body=build_ics(item, meeting_title)
        )

    def configured(self) -> bool:
        return True

    def send(self, item: ActionItem, meeting_title: str) -> DispatchResult:
        return DispatchResult(
            destination=self.name,
            title=item.title,
            status="sent",
            detail="Tentative hold prepared — download the .ics and send it from your calendar",
        )
