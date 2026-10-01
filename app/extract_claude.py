"""Claude-powered extractor: one structured-output call per meeting."""

from __future__ import annotations

import logging
import os

import anthropic

from .models import MeetingAnalysis, Transcript
from .team import TEAM

log = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
# Route policy refusals to a fallback model server-side instead of failing the call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You are the operations assistant for an early-stage health-AI startup with two business lines:
Clinical (ECG-based software sold to health systems: pilots, regulatory, research sites) and
Performance (a membership for athletes and clubs: growth, devices, coaching).

You turn a meeting transcript into structured follow-ups that will be routed to the team's tools:
- linear: engineering, product, data or growth work (features, fixes, diagrams, experiments)
- attio: anything owed to or expected from an external partner, customer, site or investor (CRM follow-up)
- notion: documentation, playbooks, wiki pages, templates, hiring pages
- calendar: a meeting, call or session that needs to be scheduled

Rules:
- Extract only real commitments: someone agreed to do something, or was asked and accepted. Ignore ideas, maybes and chit-chat.
- One item per commitment. If a request is accepted by someone restating it, keep one item owned by the person who accepted.
- Owner: the full name of the responsible person. Internal team: {team}. External attendees own items they committed to (owner_is_external = true).
- Resolve relative dates against the meeting date ({meeting_date}, a {weekday}). "By Friday" is the next Friday after the meeting. Use null when no date was stated.
- Write titles as specific imperatives without filler ("Send Northfield the pilot proposal with both pricing options").
- evidence.quote must be copied verbatim from the transcript line at evidence.timestamp.
- Decisions: only things the group actually agreed on. Open questions: questions left unresolved.
- follow_up_email: a short, warm recap addressed to the external attendees (null for internal meetings), signed "Javier".
- Never invent people, dates, numbers or commitments that are not in the transcript."""


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def render_transcript(t: Transcript) -> str:
    people = "\n".join(
        f"- {p.name}"
        + (f" ({p.role}" + (f", {p.organization}" if p.organization else "") + ")" if p.role else "")
        + (" [external]" if p.is_external else "")
        for p in t.participants
    )
    lines = "\n".join(f"[{u.timestamp}] {u.speaker}: {u.text}" for u in t.utterances)
    return f"Meeting: {t.title}\nDate: {t.meeting_date.isoformat()}\nParticipants:\n{people}\n\nTranscript:\n{lines}"


def extract_with_claude(t: Transcript) -> MeetingAnalysis:
    """Raises on any failure; the caller decides whether to fall back to the rules engine."""
    team = ", ".join(f"{name} ({role})" for name, role in TEAM.items())
    system = SYSTEM.format(team=team, meeting_date=t.meeting_date.isoformat(), weekday=t.meeting_date.strftime("%A"))
    response = _get_client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=system,
        messages=[{"role": "user", "content": render_transcript(t)}],
        output_format=MeetingAnalysis,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"model returned no analysis (stop_reason={response.stop_reason})")
    return response.parsed_output


def describe_error(error: Exception) -> str:
    if isinstance(error, anthropic.AuthenticationError):
        return "invalid ANTHROPIC_API_KEY"
    if isinstance(error, anthropic.RateLimitError):
        return "rate limited"
    if isinstance(error, anthropic.APIStatusError):
        return f"API error {error.status_code}"
    if isinstance(error, anthropic.APIConnectionError):
        return "could not reach the Anthropic API"
    return str(error)
