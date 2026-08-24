"""Merge redacted seven-day batch reports into one reachability matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from core.backend.app.simulation.evidence import (  # noqa: E402
    gameplay_evidence_markdown,
    load_batch_reports,
    summarize_gameplay_evidence,
    summarize_preregistered_evidence,
)
from core.backend.app.simulation.manifest import (  # noqa: E402
    load_manifest,
    planned_attempts,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="*", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="preregistration manifest; enables the full planned denominator",
    )
    parser.add_argument("--minimum-seeds-per-route", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulation_reports") / "gameplay_evidence",
    )
    parser.add_argument(
        "--attempt-root",
        type=Path,
        default=None,
        help="atomic attempt-record directory; supports interrupted batches without a batch JSON",
    )
    parser.add_argument(
        "--attempt-report-root",
        type=Path,
        default=None,
        help="per-attempt checkpoint directory from an interrupted batch",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    if (
        not args.reports
        and args.attempt_root is None
        and args.attempt_report_root is None
    ):
        raise SystemExit(
            "provide at least one batch report, --attempt-root, or --attempt-report-root"
        )
    reports = load_batch_reports(args.reports) if args.reports else []
    manifest_path = args.manifest
    if manifest_path is not None:
        manifest, manifest_digest = load_manifest(manifest_path)
        ledger_records: dict[str, dict[str, object]] = {}
        if args.attempt_root is not None:
            for path in sorted(args.attempt_root.glob("*.json")):
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or not isinstance(
                    value.get("attemptId"), str
                ):
                    raise ValueError(f"invalid attempt record: {path.name}")
                ledger_records.setdefault(str(value["attemptId"]), value)
        if args.attempt_report_root is not None:
            checkpoint_paths = sorted(args.attempt_report_root.glob("*.json"))
            for path in checkpoint_paths:
                raw_bytes = path.read_bytes()
                value = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(value, dict) or not isinstance(
                    value.get("attemptId"), str
                ):
                    raise ValueError(f"invalid attempt checkpoint: {path.name}")
                report = dict(value)
                report["_evidenceSource"] = {
                    "sourceId": f"attempt-checkpoint:{value['attemptId']}",
                    "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "mode": value.get("mode"),
                    # Checkpoints intentionally do not claim batch cleanup or
                    # preflight metadata that was never finalized.
                    "backend": None,
                    "keepRuns": None,
                    "runsRequested": len(planned_attempts(manifest)),
                    "runsCompleted": len(checkpoint_paths),
                    "experimentId": manifest["experimentId"],
                    "manifestDigest": manifest_digest,
                    "attempts": list(ledger_records.values()),
                    "embeddingPreflightPassed": False,
                }
                record = ledger_records.get(str(value["attemptId"]))
                if record is not None:
                    report["_attemptRecord"] = dict(record)
                reports.append(report)
        for report in reports:
            source = report.get("_evidenceSource")
            if not isinstance(source, dict):
                continue
            raw_attempts = source.get("attempts", [])
            if not isinstance(raw_attempts, list):
                continue
            for raw_attempt in raw_attempts:
                if isinstance(raw_attempt, dict) and isinstance(
                    raw_attempt.get("attemptId"), str
                ):
                    ledger_records.setdefault(str(raw_attempt["attemptId"]), raw_attempt)
        summary = summarize_preregistered_evidence(
            manifest,
            reports,
            attempt_records=ledger_records.values(),
        )
    else:
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
