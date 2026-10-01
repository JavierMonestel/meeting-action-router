"""Domain models shared by the extractors, the router and the API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Destination = Literal["linear", "attio", "notion", "calendar"]
Priority = Literal["high", "medium", "low"]

DESTINATION_LABEL: dict[str, str] = {
    "linear": "Linear",
    "attio": "Attio",
    "notion": "Notion",
    "calendar": "Calendar",
}


class Participant(BaseModel):
    name: str
    role: str | None = None
    organization: str | None = None  # set for external attendees only

    @property
    def first_name(self) -> str:
        parts = self.name.replace("Dr. ", "").split()
        return parts[0] if parts else self.name

    @property
    def is_external(self) -> bool:
        return self.organization is not None


class Utterance(BaseModel):
    timestamp: str  # "00:04:05"
    speaker: str
    text: str


class Transcript(BaseModel):
    title: str
    meeting_date: date
    participants: list[Participant]
    utterances: list[Utterance]

    def participant(self, name_or_first: str) -> Participant | None:
        needle = name_or_first.lower().strip()
        for p in self.participants:
            if p.name.lower() == needle or p.first_name.lower() == needle:
                return p
        return None

    @property
    def external_orgs(self) -> list[str]:
        return sorted({p.organization for p in self.participants if p.organization})


class Evidence(BaseModel):
    timestamp: str = Field(description="Timestamp of the utterance that supports this item, e.g. 00:04:05")
    quote: str = Field(description="Short verbatim quote from the transcript")


class ActionItem(BaseModel):
    title: str = Field(description="Imperative, specific task title (max ~90 chars)")
    owner: str = Field(description="Full name of the person responsible, exactly as listed in participants or mentioned (e.g. 'Dev Patel')")
    owner_is_external: bool = Field(description="True when the owner is not on the internal team")
    due_date: date | None = Field(description="Resolved due date, or null if none was stated")
    destination: Destination = Field(
        description="linear = engineering/product/growth work; attio = partner, customer or investor follow-ups; "
        "notion = docs, wiki, playbooks, templates; calendar = meetings to schedule"
    )
    linked_org: str | None = Field(description="External organization this item relates to, if any")
    priority: Priority
    evidence: Evidence


class Decision(BaseModel):
    text: str
    evidence: Evidence


class FollowUpEmail(BaseModel):
    to: list[str] = Field(description="External attendees' names")
    subject: str
    body: str


class MeetingAnalysis(BaseModel):
    summary: str = Field(description="Two or three sentence summary of the meeting")
    key_points: list[str]
    decisions: list[Decision]
    action_items: list[ActionItem]
    open_questions: list[str]
    follow_up_email: FollowUpEmail | None = Field(description="Recap email for external attendees; null for internal meetings")


class AnalysisResult(BaseModel):
    """What the API returns: the analysis plus how it was produced."""

    engine: Literal["claude", "rules"]
    transcript: Transcript
    analysis: MeetingAnalysis
