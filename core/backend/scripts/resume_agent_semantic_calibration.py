"""Resume only the missing live Judge calibration cases of a saved baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import ArkSettings  # noqa: E402
from core.backend.app.evaluation.judge import (  # noqa: E402
    JudgeAdapter,
    JudgeCostConfig,
)
from core.backend.app.evaluation.report import write_report  # noqa: E402
from core.backend.scripts.run_agent_semantic_evaluation import (  # noqa: E402
    _live_calibration_report,
    _load_calibration_cases,
    _write_calibration_report,
    calibration_breakdown,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly authorize Ark calls")
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=_ROOT / "core" / "evaluation" / "judge_calibration_cases.yaml",
    )
    parser.add_argument("--max-incremental-cost-cny", type=float, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--judge-request-timeout-seconds", type=float, default=45.0)
    return parser


def _case_id(item: dict[str, Any]) -> str:
    value = item.get("case_id", item.get("caseId"))
    if not isinstance(value, str) or not value:
        raise ValueError("calibration case is missing case_id")
    return value


def merge_calibration_reports(
    existing: dict[str, Any],
    delta: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge disjoint calibration results while preserving dataset order."""

    by_id: dict[str, dict[str, Any]] = {}
    for result in [*existing.get("cases", []), *delta.get("cases", [])]:
        if isinstance(result, dict) and isinstance(result.get("caseId"), str):
            by_id[result["caseId"]] = result
    ordered = [by_id[case_id] for item in items if (case_id := _case_id(item)) in by_id]
    injection_results = [result for result in ordered if result.get("injectionAttempt") is True]
    injection_passed = sum(
        "injection_review_reason" not in result.get("failureReasons", [])
        and result.get("status") == "scored"
        for result in injection_results
    )
    tokens: dict[str, int] = {}
    for key in ("promptTokens", "completionTokens", "totalTokens"):
        tokens[key] = sum(
            int(report.get("judgeTokenUsage", {}).get(key, 0) or 0)
            for report in (existing, delta)
        )
    complete = len(ordered) == len(items) and all(
        result.get("status") == "scored" for result in ordered
    )
    merged = {
        "schemaVersion": existing.get("schemaVersion", 1),
        "status": "live-scored",
        "complete": complete,
        "stopReason": None if complete else delta.get("stopReason"),
        "executedLiveJudge": True,
        "datasetCases": len(items),
        "injectionCases": sum(item.get("injection_attempt") is True for item in items),
        "promptBoundaryChecksPassed": existing.get("promptBoundaryChecksPassed"),
        "promptBoundaryPassRate": existing.get("promptBoundaryPassRate"),
        "scoredCases": sum(result.get("status") == "scored" for result in ordered),
        "failedCases": sum(result.get("status") == "failed" for result in ordered),
        "skippedCases": len(items) - len(ordered),
        "judgeCalls": sum(int(report.get("judgeCalls", 0) or 0) for report in (existing, delta)),
        "judgeTokenUsage": tokens,
        "estimatedCostCny": round(
            sum(float(report.get("estimatedCostCny", 0.0) or 0.0) for report in (existing, delta)),
            6,
        ),
        "elapsedMs": sum(int(report.get("elapsedMs", 0) or 0) for report in (existing, delta)),
        "calibrationPassRate": round(
            sum(result.get("passed") is True for result in ordered) / len(items), 6
        ),
        "injectionPassRate": (
            round(injection_passed / len(injection_results), 6)
            if injection_results
            else None
        ),
        "cases": ordered,
    }
    merged.update(calibration_breakdown(ordered))
    merged["qualityGateStatus"] = (
        "quality-gate"
        if complete
        and merged["calibrationPassRate"] >= 0.8
        and merged["injectionPassRate"] == 1.0
        else "advisory"
    )
    return merged


def merge_resume_into_baseline(
    report: dict[str, Any],
    calibration: dict[str, Any],
    delta: dict[str, Any],
    *,
    incremental_cap: float,
) -> None:
    """Update aggregate accounting after a bounded calibration-only resume."""

    execution = report["execution"]
    initial_cost = float(execution.get("estimatedCostCny", 0.0))
    budget = execution.setdefault("budget", {})
    budget["initialMaxCostCny"] = budget.get("maxCostCny")
    budget["initialEstimatedCostCny"] = round(initial_cost, 6)
    budget["resumeIncrementalMaxCostCny"] = incremental_cap
    budget["maxCostCny"] = round(initial_cost + incremental_cap, 6)
    delta_calls = int(delta.get("judgeCalls", 0) or 0)
    delta_tokens = int(delta.get("judgeTokenUsage", {}).get("totalTokens", 0) or 0)
    delta_cost = float(delta.get("estimatedCostCny", 0.0) or 0.0)
    delta_elapsed = int(delta.get("elapsedMs", 0) or 0)
    execution["judgeCalls"] = int(execution.get("judgeCalls", 0)) + delta_calls
    execution["judgeTokens"] = int(execution.get("judgeTokens", 0)) + delta_tokens
    execution["estimatedCostCny"] = round(
        initial_cost + delta_cost, 6
    )
    execution["elapsedMs"] = int(execution.get("elapsedMs", 0)) + delta_elapsed
    execution["totalCalls"] = (
        int(execution.get("candidateCalls", 0))
        + int(execution.get("judgeCalls", 0))
        + int(execution.get("embeddingCalls", 0))
    )
    execution["calibrationJudgeCalls"] = calibration["judgeCalls"]
    execution["calibrationJudgePromptTokens"] = calibration["judgeTokenUsage"]["promptTokens"]
    execution["calibrationJudgeCompletionTokens"] = calibration["judgeTokenUsage"]["completionTokens"]
    execution["calibrationJudgeTokens"] = calibration["judgeTokenUsage"]["totalTokens"]
    execution["calibrationEstimatedCostCny"] = calibration["estimatedCostCny"]
    execution["calibrationElapsedMs"] = calibration["elapsedMs"]
    execution["calibrationStatus"] = calibration["status"]
    execution["calibrationStopReason"] = calibration["stopReason"]
    execution["calibrationResumed"] = True
    execution["calibrationResumeIncrementalCapCny"] = incremental_cap
    execution["calibrationResumeEstimatedCostCny"] = round(delta_cost, 6)
    execution["complete"] = bool(
        calibration["complete"]
        and execution.get("completedCases") == execution.get("selectedCases")
        and not execution.get("timedOut")
    )
    if execution["complete"]:
        execution["budgetExhausted"] = False
        execution["budgetReason"] = None
        execution["calibrationStopReason"] = None
        execution["errors"] = [
            error
            for error in execution.get("errors", [])
            if not str(error).startswith("judge_calibration_")
        ]
    report["judgeCalibration"] = calibration
    report["llmJudgeMetrics"]["judgeInjectionPassRate"] = calibration["injectionPassRate"]
    report["combinedResult"]["complete"] = execution["complete"]
    report.setdefault("metadata", {})["judgeCalibrationResumed"] = True


async def _run(args: argparse.Namespace) -> int:
    if not args.live:
        raise SystemExit("--live is required; no request was sent")
    if not 0 < args.max_incremental_cost_cny:
        raise SystemExit("--max-incremental-cost-cny must be positive")
    baseline_json = args.baseline_dir / "agent_semantic_evaluation.json"
    calibration_json = args.baseline_dir / "judge_calibration_report.json"
    report = json.loads(baseline_json.read_text(encoding="utf-8"))
    existing = json.loads(calibration_json.read_text(encoding="utf-8"))
    items = _load_calibration_cases(args.calibration_file)
    completed = {
        result.get("caseId")
        for result in existing.get("cases", [])
        if isinstance(result, dict) and result.get("status") == "scored"
    }
    missing = [item for item in items if _case_id(item) not in completed]
    if not missing:
        print("Calibration is already complete; no request was sent.")
        return 0
    key = os.environ.get("ARK_JUDGE_API_KEY", "").strip() or os.environ.get(
        "ARK_API_KEY", ""
    ).strip()
    if not key:
        raise SystemExit("ARK_JUDGE_API_KEY/ARK_API_KEY is required; no request was sent")
    model = os.environ.get("ARK_JUDGE_MODEL", "").strip() or "doubao-seed-2.1-turbo"
    if model != "doubao-seed-2.1-turbo":
        raise SystemExit("calibration resume fixes Judge to doubao-seed-2.1-turbo")
    settings = ArkSettings(
        api_key=key,
        model=model,
        base_url=os.environ.get("ARK_JUDGE_BASE_URL", "").strip()
        or os.environ.get("ARK_BASE_URL", "").strip()
        or "https://ark.cn-beijing.volces.com/api/plan/v3",
        request_timeout_seconds=args.judge_request_timeout_seconds,
    )
    judge = JudgeAdapter(
        settings=settings,
        cost=JudgeCostConfig(prompt_cny_per_1k=0.003, completion_cny_per_1k=0.015),
    )
    reserve = (2_500 * 3.0 + 384 * 15.0) / 1_000_000
    try:
        delta = await _live_calibration_report(
            missing,
            judge,
            max_calls=len(missing) * 2,
            max_cost_cny=args.max_incremental_cost_cny,
            existing_cost_cny=0.0,
            reserved_cost_cny=reserve,
            timeout_seconds=args.timeout_seconds,
        )
    finally:
        await judge.close()
    merged = merge_calibration_reports(existing, delta, items)
    merge_resume_into_baseline(
        report,
        merged,
        delta,
        incremental_cap=args.max_incremental_cost_cny,
    )
    write_report(report, args.baseline_dir)
    _write_calibration_report(merged, args.baseline_dir)
    print(
        json.dumps(
            {
                "missingBefore": len(missing),
                "deltaCalls": delta["judgeCalls"],
                "deltaEstimatedCostCny": delta["estimatedCostCny"],
                "complete": merged["complete"],
                "totalCalibrationCalls": merged["judgeCalls"],
                "totalCalibrationCostCny": merged["estimatedCostCny"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if merged["complete"] else 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
