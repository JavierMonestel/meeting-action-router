"""Parse Grain-style plain-text transcripts.

Expected shape (header lines are optional):

    Title: Weekly leadership sync
    Date: 2026-10-01
    Participants: Sam Rivera (Founder & CEO), Dr. Alan Brooks (CMIO, Northfield Health)

    [00:00:04] Sam Rivera: Thanks for making the time...
"""

from __future__ import annotations

import contextlib
import re
from datetime import date
from pathlib import Path

from .models import Participant, Transcript, Utterance

LINE = re.compile(r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^:]{2,60}):\s*(?P<text>.+)$")
SAMPLES_DIR = Path(__file__).parent / "samples"


class TranscriptError(ValueError):
    pass


def parse_participants(raw: str) -> list[Participant]:
    """'Sam Rivera (Founder & CEO), Dr. Alan Brooks (CMIO, Northfield Health)'."""
    people: list[Participant] = []
    for match in re.finditer(r"([^,(]+?)\s*(?:\(([^)]*)\))?\s*(?:,|$)", raw):
        name = match.group(1).strip()
        if not name:
            continue
        role, org = None, None
        if match.group(2):
            bits = [b.strip() for b in match.group(2).split(",")]
            role = bits[0] or None
            org = bits[1] if len(bits) > 1 else None
        people.append(Participant(name=name, role=role, organization=org))
    return people


def parse_transcript(text: str, default_date: date | None = None) -> Transcript:
    title = "Untitled meeting"
    meeting_date = default_date or date.today()
    participants: list[Participant] = []
    utterances: list[Utterance] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := LINE.match(line):
            utterances.append(Utterance(timestamp=m["ts"], speaker=m["speaker"].strip(), text=m["text"].strip()))
        elif line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip() or title
        elif line.lower().startswith("date:"):
            value = line.split(":", 1)[1].strip()
            # Keep the default on an unparseable date (e.g. an unfilled template placeholder).
            with contextlib.suppress(ValueError):
                meeting_date = date.fromisoformat(value)
        elif line.lower().startswith("participants:"):
            participants = parse_participants(line.split(":", 1)[1])
        elif utterances:
            # Continuation of the previous speaker's turn.
            utterances[-1].text += " " + line

    if not utterances:
        raise TranscriptError("No utterances found. Expected lines like '[00:01:02] Name: text'.")

    # Anyone who speaks but isn't listed is added as a participant (internal by default).
    known = {p.name for p in participants}
    for u in utterances:
        if u.speaker not in known:
            participants.append(Participant(name=u.speaker))
            known.add(u.speaker)

    return Transcript(title=title, meeting_date=meeting_date, participants=participants, utterances=utterances)


SAMPLE_TITLES: dict[str, str] = {
    "northfield-pilot-scoping": "Health-system pilot scoping (Clinical)",
    "summit-team-plan": "Endurance club team plan (Performance)",
    "leadership-sync": "Weekly leadership sync (both lines)",
    "investor-checkin": "Investor check-in (Company)",
    "heldout-cedar-valley-site": "Research site IRB check-in (held-out)",
    "heldout-growth-standup": "Growth standup (held-out)",
}


def load_sample(slug: str, meeting_date: date | None = None) -> str:
    if slug not in SAMPLE_TITLES:
        raise KeyError(slug)
    raw = (SAMPLES_DIR / f"{slug}.txt").read_text(encoding="utf-8")
    return raw.replace("{{DATE}}", (meeting_date or date.today()).isoformat())
