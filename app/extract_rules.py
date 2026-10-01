"""Deterministic meeting extractor.

Runs when no ANTHROPIC_API_KEY is configured (the public demo) and as the fallback
if the model call fails. It is intentionally simple and transparent: it looks for
explicit commitments ("I'll ...", "Javier, can you ...", "I'll get Dev to ..."),
decisions ("let's go with", "we agreed"), and questions that were left open.
The eval harness in /evals measures how far that gets us.
"""

from __future__ import annotations

import re
from datetime import timedelta

from .dates import resolve_due
from .models import ActionItem, Decision, Destination, Evidence, FollowUpEmail, MeetingAnalysis, Priority, Transcript, Utterance
from .team import BY_FIRST_NAME

# Split sentences, but not after common honorifics ("Dr. Kim").
SENTENCE = re.compile(r"(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<=[.!?])\s+")

# "I'll send over ...", "I will ...", "I can share ...", "we'll ..."
FIRST_PERSON = re.compile(r"\b(?:I'll|I will|I'm going to|I can|we'll|we will)\s+(?P<task>.+)", re.I)
# "I'll get Dev to produce ...", "I'll ask Dev to scope ..."
DELEGATE = re.compile(r"\bI'll (?:get|ask|have) (?P<who>[A-Z][a-z]+) to (?P<task>.+)", re.I)
# "Javier, can you send ...?"
ADDRESSED = re.compile(r"^(?P<who>[A-Z][a-z]+),\s+(?:can|could|would) you\s+(?P<task>.+?)\??$")
ACK = re.compile(r"^(?:yes|yep|sure|will do|on it|absolutely|of course|done|okay|ok)\b", re.I)
HEDGE = re.compile(r"\b(?:undecided|not sure|don't know|tbd|need to check|get back to you|honest answer|still deciding)\b", re.I)

# Commitments that aren't tasks ("I'll be honest", "I'll say this").
NOT_A_TASK = re.compile(r"^(?:be |say |admit |think |take that|pass|do that|try)", re.I)

DECISION = re.compile(
    r"\b(?:let's go with|we agreed|we've agreed|decision:|we decided|we are going with|we're going with|we are opening|we're opening)\b|\bdecision:",
    re.I,
)

# Naming the tool out loud always wins ("... and track it in Linear").
EXPLICIT_TOOL: list[tuple[Destination, re.Pattern[str]]] = [
    ("linear", re.compile(r"\blinear\b", re.I)),
    ("notion", re.compile(r"\bnotion\b", re.I)),
    ("attio", re.compile(r"\b(?:attio|crm)\b", re.I)),
]
KEYWORDS: list[tuple[Destination, re.Pattern[str]]] = [
    ("calendar", re.compile(r"\b(?:schedule|book|invite)\b.*\b(?:call|meeting|session|review|sync)\b|\binvites?\b", re.I)),
    ("notion", re.compile(r"\b(?:page|playbook|article|doc|docs|wiki|template|pre-read|write-up)\b", re.I)),
    (
        "linear",
        re.compile(
            r"\b(?:ship|fix|build|scope|dashboard|diagram|firmware|export|bug|deploy|a/b test|experiment|feature|workaround)\b", re.I
        ),
    ),
    (
        "attio",
        re.compile(r"\b(?:send|proposal|questionnaire|intro|reach out|chase|follow up|share|deck|list|relationship|contract)\b", re.I),
    ),
]


def classify_destination(title: str, sentence: str, has_external: bool) -> Destination:
    """Explicit tool mention in the sentence > keywords in the task title > keywords in the sentence."""
    for dest, pattern in EXPLICIT_TOOL:
        if pattern.search(sentence):
            return dest
    for text in (title, sentence):
        for dest, pattern in KEYWORDS:
            if pattern.search(text):
                return dest
    return "attio" if has_external else "linear"


_TRAILING_WHEN = re.compile(
    r"\s+(?:by|before|on|until)\s+(?:the\s+)?(?:end of next week|end of the week|next week|this week|tomorrow|today|"
    r"monday|tuesday|wednesday|thursday|friday|\d{1,2}(?:st|nd|rd|th))\b.*$",
    re.I,
)


def clean_task(task: str, transcript: Transcript, owner_external: bool) -> str:
    task = task.strip().rstrip(".!?")
    task = _TRAILING_WHEN.sub("", task)
    task = re.sub(r"\s+(?:tomorrow|today|this week|next week|within a week|two weeks from now)\b.*$", "", task, flags=re.I)
    task = re.sub(r"\s+(?:so it lands|as soon as|once)\b.*$", "", task, flags=re.I)
    task = re.sub(r"\s+and (?:have|get|send) it back to \w+$", "", task, flags=re.I)
    task = re.sub(r"\s+in (?:Linear|Notion|Attio)\b", "", task)
    task = re.sub(r"^(?:also|then|just)\s+", "", task, flags=re.I)
    task = re.sub(r"^own\s+", "Complete ", task, flags=re.I)
    task = re.sub(r"^have (.+) ready$", r"Prepare \1", task, flags=re.I)
    task = _resolve_pronouns(task, transcript, owner_external)
    task = re.sub(r"\s+(?:for|by|on|at|and|with)$", "", task.strip(), flags=re.I)
    return task[:1].upper() + task[1:] if task else task


def _resolve_pronouns(task: str, t: Transcript, owner_external: bool) -> str:
    """'Intro you to Dr. Kim' (said by an investor) -> 'Intro us to Dr. Kim';
    'Send you the deck' (said to the one external attendee) -> 'Send Nora the deck'."""
    if owner_external:
        return re.sub(r"\byou\b", "us", task)
    externals = [p for p in t.participants if p.is_external]
    if len(externals) == 1:
        ext = externals[0]
        task = re.sub(r"\byour\b", f"{ext.organization}'s", task)
        task = re.sub(r"\byou\b", ext.first_name, task)
    return task


class _Builder:
    def __init__(self, transcript: Transcript):
        self.t = transcript
        self.has_external = bool(transcript.external_orgs)
        self.items: list[ActionItem] = []

    def resolve_person(self, first_or_full: str, fallback: str) -> tuple[str, bool]:
        if p := self.t.participant(first_or_full):
            return p.name, p.is_external
        # A teammate who is mentioned but not in the meeting ("get Dev to ...").
        if name := BY_FIRST_NAME.get(first_or_full.lower()):
            return name, False
        return fallback, False

    def add(self, owner: str, external: bool, task: str, u: Utterance, sentence: str) -> None:
        title = clean_task(task, self.t, external)
        if len(title) < 6 or NOT_A_TASK.match(title):
            return
        due = resolve_due(sentence, self.t.meeting_date)
        self.items.append(
            ActionItem(
                title=title,
                owner=owner,
                owner_is_external=external,
                due_date=due,
                destination=classify_destination(title, sentence, self.has_external),
                linked_org=self.t.external_orgs[0] if self.has_external else None,
                priority=self.priority(due),
                evidence=Evidence(timestamp=u.timestamp, quote=sentence.strip()),
            )
        )

    def priority(self, due) -> Priority:
        if due is None:
            return "low"
        if due <= self.t.meeting_date + timedelta(days=2):
            return "high"
        return "medium"


def extract_with_rules(transcript: Transcript) -> MeetingAnalysis:
    b = _Builder(transcript)
    decisions: list[Decision] = []
    questions: list[tuple[int, str]] = []
    utterances = transcript.utterances

    for idx, u in enumerate(utterances):
        speaker = transcript.participant(u.speaker)
        speaker_external = bool(speaker and speaker.is_external)
        next_u = utterances[idx + 1] if idx + 1 < len(utterances) else None

        for sentence in SENTENCE.split(u.text):
            s = sentence.strip()
            if not s:
                continue

            if DECISION.search(s):
                text = re.sub(r"^(?:then|okay|so|decision:)\s*", "", s, flags=re.I).rstrip(".")
                decisions.append(Decision(text=text[:1].upper() + text[1:], evidence=Evidence(timestamp=u.timestamp, quote=s)))

            if m := DELEGATE.search(s):
                owner, ext = b.resolve_person(m["who"], u.speaker)
                b.add(owner, ext, m["task"].split(" and ")[0], u, s)
                continue

            if m := ADDRESSED.match(s):
                # Only a task if the addressee acknowledges it without restating it
                # (a restated "Yes, I'll ..." is captured from their own line instead).
                if next_u and ACK.match(next_u.text) and not FIRST_PERSON.search(next_u.text):
                    owner, ext = b.resolve_person(m["who"], u.speaker)
                    b.add(owner, ext, m["task"], u, s)
                continue

            if m := FIRST_PERSON.search(s):
                b.add(u.speaker, speaker_external, m["task"], u, s)
                continue

            if s.endswith("?") and speaker_external and len(s) > 30:
                questions.append((idx, s))

    open_questions = [
        _strip_lead_in(q)
        for idx, q in questions
        if "open question" in q.lower() or (idx + 1 < len(utterances) and HEDGE.search(utterances[idx + 1].text))
    ]

    action_items = _dedupe(b.items)
    return MeetingAnalysis(
        summary=_summary(transcript, action_items, decisions),
        key_points=[d.text for d in decisions] + [f"{len(action_items)} follow-ups captured with owners and dates"],
        decisions=decisions,
        action_items=action_items,
        open_questions=open_questions,
        follow_up_email=_follow_up_email(transcript, action_items, decisions),
    )


def _strip_lead_in(question: str) -> str:
    """'One open question I still have: does X?' -> 'Does X?'"""
    if ":" in question:
        tail = question.split(":", 1)[1].strip()
        if tail.endswith("?"):
            question = tail
    return question[:1].upper() + question[1:]


def _dedupe(items: list[ActionItem]) -> list[ActionItem]:
    seen: set[tuple[str, str]] = set()
    out: list[ActionItem] = []
    for item in items:
        key = (item.owner, " ".join(item.title.lower().split()[:4]))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _summary(t: Transcript, items: list[ActionItem], decisions: list[Decision]) -> str:
    people = ", ".join(p.name for p in t.participants)
    owners = sorted({i.owner.split()[0] for i in items})
    parts = [f"{t.title} with {people}."]
    if decisions:
        parts.append(f"{len(decisions)} decision{'s' if len(decisions) != 1 else ''} recorded, starting with: {decisions[0].text}.")
    if items:
        parts.append(f"{len(items)} follow-ups assigned to {', '.join(owners)}.")
    return " ".join(parts)


def _follow_up_email(t: Transcript, items: list[ActionItem], decisions: list[Decision]) -> FollowUpEmail | None:
    externals = [p for p in t.participants if p.is_external]
    if not externals:
        return None
    first_names = " and ".join(p.first_name for p in externals)
    lines = [f"Hi {first_names},", "", "Thanks for the time today. A quick recap so we're aligned:", ""]
    if decisions:
        lines.append("What we agreed:")
        lines += [f"- {d.text}" for d in decisions]
        lines.append("")
    ours = [i for i in items if not i.owner_is_external and i.destination != "notion"]
    theirs = [i for i in items if i.owner_is_external]
    if ours:
        lines.append("Next steps on our side:")
        for i in ours:
            when = f", {i.due_date.strftime('%b %d').replace(' 0', ' ')}" if i.due_date else ""
            lines.append(f"- {i.title} ({i.owner.split()[0]}{when})")
        lines.append("")
    if theirs:
        lines.append("On your side:")
        lines += [f"- {i.title}" for i in theirs]
        lines.append("")
    lines += ["Let me know if I missed anything.", "", "Best,", "Javier"]
    return FollowUpEmail(to=[p.name for p in externals], subject=f"Recap: {t.title}", body="\n".join(lines))
