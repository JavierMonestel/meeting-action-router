"""CLI: python -m evals.run [--engine rules|claude] [--min-f1 0.8]

Prints per-meeting results for the dev and held-out sets and exits non-zero if
overall F1 is below the threshold, so CI catches extraction regressions.
"""

from __future__ import annotations

import argparse
import sys

from app.evaluation import run_eval

SPLITS = (
    ("dev", "DEV set (used while building the rules)"),
    ("heldout", "HELD-OUT set (never used for tuning)"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["rules", "claude"], default="rules")
    parser.add_argument("--min-f1", type=float, default=0.0)
    args = parser.parse_args()

    full = run_eval(args.engine)
    print(f"\nEngine: {full.engine}")
    for split_name, label in SPLITS:
        report = full.split(split_name)
        print(f"\n{label}")
        print(f"{'meeting':28} {'expected':>8} {'predicted':>9} {'matched':>7} {'dest ok':>7} {'due ok':>6}")
        for s in report.samples:
            print(f"{s.slug:28} {s.expected:>8} {s.predicted:>9} {s.matched:>7} {s.destination_correct:>7} {s.due_correct:>6}")
            for miss in s.misses:
                print(f"    missed: {miss}")
            for extra in s.extras:
                print(f"    extra:  {extra}")
        print(
            f"precision {report.precision:.2f} | recall {report.recall:.2f} | F1 {report.f1:.2f} | "
            f"destination {report.destination_accuracy:.2f} | due date {report.due_accuracy:.2f} | "
            f"decisions {report.decision_recall:.2f} | open questions {report.question_recall:.2f}"
        )
    if full.f1 < args.min_f1:
        print(f"\nFAIL: overall F1 {full.f1:.2f} < {args.min_f1}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
