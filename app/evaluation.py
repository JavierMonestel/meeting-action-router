"""Score an extractor against hand-labeled sample meetings.

Metrics
- action items: precision, recall, F1 (match = same owner + all keywords in title)
- destination accuracy and due-date accuracy on matched items
- decision and open-question recall
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from .extract_claude import extract_with_claude
from .extract_rules import extract_with_rules
from .models import MeetingAnalysis
from .transcript import load_sample, parse_transcript

GOLDEN = Path(__file__).parent / "eval_data" / "golden.json"


@dataclass
class SampleScore:
    slug: str
    split: str
    expected: int
    predicted: int
    matched: int
    destination_correct: int
    due_correct: int
    decisions_expected: int
    decisions_found: int
    questions_expected: int
    questions_found: int
    misses: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)


@dataclass
class Report:
    engine: str
    samples: list[SampleScore]

    def split(self, name: str) -> Report:
        return Report(engine=self.engine, samples=[s for s in self.samples if s.split == name])

    def _sum(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.samples)

    @property
    def precision(self) -> float:
        return self._sum("matched") / max(1, self._sum("predicted"))

    @property
    def recall(self) -> float:
        return self._sum("matched") / max(1, self._sum("expected"))

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    @property
    def destination_accuracy(self) -> float:
        return self._sum("destination_correct") / max(1, self._sum("matched"))

    @property
    def due_accuracy(self) -> float:
        return self._sum("due_correct") / max(1, self._sum("matched"))

    @property
    def decision_recall(self) -> float:
        return self._sum("decisions_found") / max(1, self._sum("decisions_expected"))

    @property
    def question_recall(self) -> float:
        return self._sum("questions_found") / max(1, self._sum("questions_expected"))


def _contains_all(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return all(k.lower() in low for k in keywords)


def score_sample(slug: str, spec: dict, analysis: MeetingAnalysis) -> SampleScore:
    predicted = list(analysis.action_items)
    unmatched = list(range(len(predicted)))
    matched = dest_ok = due_ok = 0
    misses: list[str] = []
    for gold in spec["action_items"]:
        hit = next(
            (i for i in unmatched if predicted[i].owner == gold["owner"] and _contains_all(predicted[i].title, gold["keywords"])),
            None,
        )
        if hit is None:
            misses.append(f"{gold['owner']}: {' + '.join(gold['keywords'])}")
            continue
        unmatched.remove(hit)
        matched += 1
        item = predicted[hit]
        dest_ok += item.destination == gold["destination"]
        expected_due = date.fromisoformat(gold["due"]) if gold["due"] else None
        due_ok += item.due_date == expected_due

    decisions_text = [d.text for d in analysis.decisions]
    decisions_found = sum(any(_contains_all(t, kw) for t in decisions_text) for kw in spec["decisions"])
    questions_found = sum(any(_contains_all(q, kw) for q in analysis.open_questions) for kw in spec["open_questions"])

    return SampleScore(
        slug=slug,
        split=spec.get("split", "dev"),
        expected=len(spec["action_items"]),
        predicted=len(predicted),
        matched=matched,
        destination_correct=dest_ok,
        due_correct=due_ok,
        decisions_expected=len(spec["decisions"]),
        decisions_found=decisions_found,
        questions_expected=len(spec["open_questions"]),
        questions_found=questions_found,
        misses=misses,
        extras=[f"{predicted[i].owner}: {predicted[i].title}" for i in unmatched],
    )


def run_eval(engine: Literal["rules", "claude"] = "rules") -> Report:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    meeting_date = date.fromisoformat(golden["meeting_date"])
    extract = extract_with_rules if engine == "rules" else extract_with_claude
    scores = []
    for slug, spec in golden["samples"].items():
        transcript = parse_transcript(load_sample(slug, meeting_date))
        scores.append(score_sample(slug, spec, extract(transcript)))
    return Report(engine=engine, samples=scores)
