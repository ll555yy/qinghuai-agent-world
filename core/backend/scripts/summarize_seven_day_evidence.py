"""Merge redacted seven-day batch reports into one reachability matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from core.backend.app.simulation.evidence import (  # noqa: E402
    gameplay_evidence_markdown,
    load_batch_reports,
    summarize_gameplay_evidence,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--minimum-seeds-per-route", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulation_reports") / "gameplay_evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    reports = load_batch_reports(args.reports)
    summary = summarize_gameplay_evidence(
        reports,
        minimum_seeds_per_route=args.minimum_seeds_per_route,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "seven_day_gameplay_evidence.json"
    markdown_path = args.output / "seven_day_gameplay_evidence.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        gameplay_evidence_markdown(summary),
        encoding="utf-8",
    )
    print(f"JSON evidence: {json_path}")
    print(f"Markdown evidence: {markdown_path}")
    if not summary["complete"]:
        print("Evidence matrix is incomplete; inspect requirementFailures.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
