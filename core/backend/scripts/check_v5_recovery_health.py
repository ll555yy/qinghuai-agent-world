"""Run the single bounded Candidate + Embedding health round for v5 recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.backend.scripts.check_ark_connection import run_check  # noqa: E402
from core.backend.scripts.check_ark_embedding import run_probe  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation_reports/v5_recovery_health.json"),
    )
    return parser


def _candidate_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    passed = sum(
        item.get("success") is True for item in checks if isinstance(item, dict)
    )
    return {
        "required": 6,
        "passed": passed,
        "gatePassed": passed == 6 and report.get("success") is True,
        "model": report.get("model"),
        "providerMetrics": report.get("providerMetrics"),
    }


def _embedding_summary(report: dict[str, Any]) -> dict[str, Any]:
    vector_count = report.get("vectorCount")
    actual_dimensions = report.get("actualDimensions")
    return {
        "required": 2,
        "passed": vector_count if isinstance(vector_count, int) else 0,
        "expectedDimensions": 2048,
        "actualDimensions": actual_dimensions,
        "gatePassed": bool(
            report.get("success") is True
            and vector_count == 2
            and actual_dimensions == 2048
        ),
        "configuredModel": report.get("configuredModel"),
        "actualModel": report.get("actualModel"),
    }


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if not args.live:
        report = {
            "schemaVersion": 1,
            "stage": "v5-recovery-health",
            "createdAt": datetime.now(UTC).isoformat(),
            "live": False,
            "requestSent": False,
            "status": "dry_run",
            "humanValidated": False,
        }
        return report, 0

    candidate_report, _candidate_code = await run_check(live=True)
    embedding_report, _embedding_code = await run_probe(live=True)
    candidate = _candidate_summary(candidate_report)
    embedding = _embedding_summary(embedding_report)
    gate_passed = bool(candidate["gatePassed"] and embedding["gatePassed"])
    report = {
        "schemaVersion": 1,
        "stage": "v5-recovery-health",
        "createdAt": datetime.now(UTC).isoformat(),
        "live": True,
        "requestSent": True,
        "status": "passed" if gate_passed else "failed",
        "gatePassed": gate_passed,
        "startV5Recovery": gate_passed,
        "candidate": candidate,
        "embedding": embedding,
        "candidateReport": candidate_report,
        "embeddingReport": embedding_report,
        "humanValidated": False,
    }
    return report, 0 if gate_passed else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, code = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
