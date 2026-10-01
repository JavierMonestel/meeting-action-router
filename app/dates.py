"""Resolve spoken due dates ("by Friday", "end of next week", "before the 14th") against the meeting date."""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD = "|".join(WEEKDAYS)


def next_weekday(base: date, weekday: int) -> date:
    """Next occurrence of weekday strictly after base (Thursday 'by Thursday' -> next week)."""
    delta = (weekday - base.weekday()) % 7 or 7
    return base + timedelta(days=delta)


def end_of_week(base: date) -> date:
    """Friday of the current week (or the following Friday on weekends)."""
    delta = (4 - base.weekday()) % 7
    return base + timedelta(days=delta)


# Ordered from most to least specific: "send the invite today ... for next week" -> next week.
_PATTERNS: list[tuple[re.Pattern[str], object]] = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), lambda m, d: date.fromisoformat(m[1])),
    (re.compile(r"\b(?:before|by|on)\s+the\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I), lambda m, d: _day_of_month(d, int(m[1]))),
    (re.compile(r"\bend of next week\b", re.I), lambda m, d: end_of_week(d) + timedelta(days=7)),
    (re.compile(r"\b(?:two|2) weeks from now\b|\bin (?:two|2) weeks\b", re.I), lambda m, d: d + timedelta(days=14)),
    (re.compile(r"\b(?:within|in) a week\b", re.I), lambda m, d: d + timedelta(days=7)),
    (re.compile(r"\bnext week\b", re.I), lambda m, d: d + timedelta(days=7)),
    (re.compile(r"\b(?:end of (?:the|this) week|this week)\b", re.I), lambda m, d: end_of_week(d)),
    (re.compile(r"\btomorrow\b", re.I), lambda m, d: d + timedelta(days=1)),
    (re.compile(rf"\bnext ({_WD})\b", re.I), lambda m, d: next_weekday(d, WEEKDAYS.index(m[1].lower())) + timedelta(days=7)),
    (re.compile(rf"\b(?:by|on|until|before)?\s*({_WD})\b", re.I), lambda m, d: next_weekday(d, WEEKDAYS.index(m[1].lower()))),
    (re.compile(r"\b(?:today|end of day|eod)\b", re.I), lambda m, d: d),
]


def _day_of_month(base: date, day: int) -> date:
    candidate = base.replace(day=min(day, 28)) if day > 28 else base.replace(day=day)
    if candidate < base:  # "the 3rd" said on the 20th means next month
        month = base.month % 12 + 1
        year = base.year + (base.month == 12)
        candidate = date(year, month, min(day, 28))
    return candidate


def resolve_due(text: str, meeting_date: date) -> date | None:
    for pattern, resolve in _PATTERNS:
        if m := pattern.search(text):
            try:
                return resolve(m, meeting_date)  # type: ignore[operator]
            except ValueError:
                continue
    return None
