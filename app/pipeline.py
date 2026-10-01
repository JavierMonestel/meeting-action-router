"""Transcript in, analysis out: picks Claude when configured, the rules engine otherwise."""

from __future__ import annotations

import logging
from datetime import date

from .extract_claude import ai_enabled, describe_error, extract_with_claude
from .extract_rules import extract_with_rules
from .models import AnalysisResult
from .transcript import parse_transcript

log = logging.getLogger(__name__)


def analyze(text: str, meeting_date: date | None = None, *, force_rules: bool = False) -> AnalysisResult:
    transcript = parse_transcript(text, default_date=meeting_date)
    if ai_enabled() and not force_rules:
        try:
            return AnalysisResult(engine="claude", transcript=transcript, analysis=extract_with_claude(transcript))
        except Exception as error:  # noqa: BLE001 - any model failure falls back to rules
            log.warning("Claude extraction failed (%s); using rules engine", describe_error(error))
    return AnalysisResult(engine="rules", transcript=transcript, analysis=extract_with_rules(transcript))
