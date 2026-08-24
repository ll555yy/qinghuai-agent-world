"""Preparation-only contract for the one-shot 47-Case semantic re-evaluation."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

FINAL_CASE_COUNT = 47
EXPECTED_CATEGORY_COUNTS = {
    "persona": 6,
    "boundary": 6,
    "memory": 11,
    "rules": 12,
    "relevance": 6,
    "coherence": 6,
}


def _value(case: object, *names: str, default: Any = "") -> Any:
    if isinstance(case, Mapping):
        for name in names:
            if name in case:
                return case[name]
    for name in names:
        if hasattr(case, name):
            return getattr(case, name)
    return default


def validate_final_case_set(cases: Iterable[object]) -> dict[str, Any]:
    """Validate the complete, unchanged 47-Case denominator."""

    values = list(cases)
    ids = [str(_value(case, "case_id", "caseId", default="")) for case in values]
    categories = Counter(str(_value(case, "category", default="")) for case in values)
    if len(values) != FINAL_CASE_COUNT:
        raise ValueError(f"final semantic re-evaluation requires {FINAL_CASE_COUNT} cases")
    if len(set(ids)) != FINAL_CASE_COUNT or any(not value for value in ids):
        raise ValueError("final semantic re-evaluation requires unique non-empty case IDs")
    if dict(categories) != EXPECTED_CATEGORY_COUNTS:
        raise ValueError(
            f"unexpected final category denominator: {dict(sorted(categories.items()))}"
        )
    return {
        "caseCount": len(values),
        "caseIds": sorted(ids),
        "categoryCounts": dict(sorted(categories.items())),
        "completeDenominator": True,
    }


def build_final_47_case_report_skeleton(cases: Iterable[object]) -> dict[str, Any]:
    """Return a report skeleton with all unrun metrics explicitly null."""

    denominator = validate_final_case_set(cases)
    return {
        "schemaVersion": 1,
        "status": "prepared_not_run",
        "denominator": denominator,
        "execution": {
            "selectedCases": FINAL_CASE_COUNT,
            "completedCases": None,
            "complete": False,
            "singleCanonicalRun": False,
        },
        "candidate": {
            "firstAttemptSchemaSuccessRate": None,
            "finalSchemaSuccessRate": None,
            "hardFailureCount": None,
            "directQuestionPassRate": None,
            "memorySingleCallRate": None,
        },
        "retrieval": {
            "fixture": None,
            "postgres": None,
            "live_embedding": None,
            "sourceMixing": "forbidden",
        },
        "judge": {"advisory": True, "calibration": None},
        "manualQueue": {"historicalItems": 17, "humanArbitration": "pending"},
        "p95LatencyMs": None,
        "tokens": None,
        "estimatedCostCny": None,
        "sha256": None,
    }


def write_final_47_case_report_skeleton(
    cases: Iterable[object],
    output: str | Path,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            build_final_47_case_report_skeleton(cases),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate the versioned Case file and write only a preparation skeleton."""

    args = _parser().parse_args(argv)
    from .case_loader import load_cases

    cases = load_cases(args.cases)
    path = write_final_47_case_report_skeleton(cases, args.output)
    print(json.dumps({"status": "prepared_not_run", "output": str(path)}, ensure_ascii=False))
    return 0


__all__ = [
    "EXPECTED_CATEGORY_COUNTS",
    "FINAL_CASE_COUNT",
    "build_final_47_case_report_skeleton",
    "validate_final_case_set",
    "write_final_47_case_report_skeleton",
]


if __name__ == "__main__":
    raise SystemExit(main())
