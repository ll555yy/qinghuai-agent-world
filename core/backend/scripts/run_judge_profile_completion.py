"""Calibrate one frozen Judge profile, then conditionally re-judge saved summaries."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import DEFAULT_ARK_BASE_URL, ArkSettings  # noqa: E402
from core.backend.app.evaluation.judge import JudgeAdapter, JudgeCostConfig  # noqa: E402
from core.backend.app.evaluation.judge_profiles import load_judge_profile  # noqa: E402
from core.backend.app.evaluation.judge_protocols import DIMENSION_NAMES  # noqa: E402
from core.backend.scripts.run_agent_semantic_evaluation import (  # noqa: E402
    _live_calibration_report,
    _load_calibration_cases,
    _load_cases,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--profile", default="judge-v2")
    parser.add_argument("--compatibility", type=Path, required=True)
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=_ROOT / "core" / "evaluation" / "judge_calibration_cases.yaml",
    )
    parser.add_argument(
        "--case-file",
        type=Path,
        default=_ROOT / "core" / "evaluation" / "agent_semantic_cases.yaml",
    )
    parser.add_argument("--source-canonical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    return parser.parse_args()


def _plain(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
    elif isinstance(value, dict):
        result = dict(value)
    else:
        raise TypeError("Judge result is not serializable")
    if not isinstance(result, dict):
        raise TypeError("Judge result must be an object")
    return result


def _case_id(value: object) -> str:
    if isinstance(value, dict):
        result = value.get("case_id", value.get("caseId"))
    else:
        result = getattr(value, "case_id", None)
    if not isinstance(result, str) or not result:
        raise ValueError("evaluation case is missing case_id")
    return result


def _score_dict(evaluation: dict[str, Any]) -> dict[str, Any] | None:
    score = evaluation.get("score")
    return dict(score) if isinstance(score, dict) else None


async def _judge_saved_summaries(
    *,
    judge: JudgeAdapter,
    cases: list[Any],
    source: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    by_id = {_case_id(case): case for case in cases}
    source_cases = source.get("cases")
    if not isinstance(source_cases, list) or len(source_cases) != 47:
        raise ValueError("source canonical must contain exactly 47 cases")
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    calls = provider_retries = prompt_tokens = completion_tokens = total_tokens = 0
    for index, saved in enumerate(source_cases):
        if not isinstance(saved, dict):
            raise ValueError("source canonical case must be an object")
        case_id = saved.get("caseId")
        summary = saved.get("candidateSummary")
        if not isinstance(case_id, str) or case_id not in by_id:
            raise ValueError(f"source canonical has unknown case: {case_id}")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError(f"source canonical has no Candidate summary: {case_id}")
        remaining = timeout_seconds - (time.perf_counter() - started)
        if remaining <= 0:
            raise TimeoutError("Judge-only batch timeout")
        evaluation = _plain(
            await asyncio.wait_for(
                judge.score(
                    by_id[case_id],
                    {
                        "candidate_text": summary,
                        "protocol": saved.get("protocol"),
                    },
                    duplicate=False,
                    rule_score=saved.get("ruleScore"),
                ),
                timeout=remaining,
            )
        )
        metrics = evaluation.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        calls += int(metrics.get("calls", 0) or 0)
        provider_retries += int(metrics.get("provider_retries", 0) or 0)
        prompt_tokens += int(metrics.get("prompt_tokens", 0) or 0)
        completion_tokens += int(metrics.get("completion_tokens", 0) or 0)
        total_tokens += int(metrics.get("total_tokens", 0) or 0)
        v2_score = _score_dict(evaluation)
        v1_score = saved.get("judgeScore")
        deltas = None
        if isinstance(v1_score, dict) and v2_score is not None:
            deltas = {
                name: int(v2_score[name]) - int(v1_score[name])
                for name in DIMENSION_NAMES
            }
        results.append(
            {
                "ordinal": index + 1,
                "caseId": case_id,
                "protocol": saved.get("protocol"),
                "status": "scored" if v2_score is not None else "failed",
                "v1Score": v1_score,
                "v2Score": v2_score,
                "dimensionDeltas": deltas,
                "reviewReasons": evaluation.get("review_reasons", []),
                "errorCode": evaluation.get("error_code"),
                "candidateSummarySha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            }
        )
    dimension_means: dict[str, float | None] = {}
    scored = [item for item in results if isinstance(item["v2Score"], dict)]
    for name in DIMENSION_NAMES:
        dimension_means[name] = (
            round(sum(int(item["v2Score"][name]) for item in scored) / len(scored), 6)
            if scored
            else None
        )
    return {
        "status": "completed" if len(scored) == 47 else "incomplete",
        "sourceCases": 47,
        "scoredCases": len(scored),
        "candidateCalls": 0,
        "embeddingCalls": 0,
        "judgeCalls": calls,
        "physicalRequests": calls + provider_retries,
        "providerRetries": provider_retries,
        "tokenUsage": {
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
        },
        "costEstimateAvailable": False,
        "estimatedCostCny": None,
        "dimensionMeans": dimension_means,
        "cases": results,
        "humanValidated": False,
    }


def _markdown(report: dict[str, Any]) -> str:
    calibration = report["calibration"]
    judge_only = report.get("judgeOnly47")
    lines = [
        "# Judge profile completion",
        "",
        f"- Profile / model: `{report['profileId']} / {report['model']}`",
        f"- Compatibility: `{report['compatibility']['status']}`",
        f"- Calibration cases: `{calibration.get('scoredCases')}/{calibration.get('datasetCases')}`",
        f"- Calibration gate: `{calibration.get('qualityGateStatus')}`",
        f"- Critical boolean macro: `{calibration.get('criticalBooleanMacroAccuracy')}`",
        f"- Score-band match: `{calibration.get('qualityGateScoreBandMatch')}`",
        f"- Injection 3/3: `{calibration.get('injection3Of3')}`",
        f"- Provider/schema errors: `{calibration.get('providerSchemaErrors')}`",
        f"- Judge-only executed: `{judge_only is not None}`",
        f"- Human validated: `{report['humanValidated']}`",
    ]
    if isinstance(judge_only, dict):
        lines.extend(
            [
                f"- Judge-only cases: `{judge_only['scoredCases']}/{judge_only['sourceCases']}`",
                f"- Judge-only physical requests: `{judge_only['physicalRequests']}`",
                f"- Judge-only total tokens: `{judge_only['tokenUsage']['totalTokens']}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    if not args.live:
        raise SystemExit("--live is required; no request was sent")
    if args.output.exists():
        raise SystemExit("output already exists; refusing to repeat Judge completion")
    profile = load_judge_profile(args.profile)
    compatibility = json.loads(args.compatibility.read_text(encoding="utf-8"))
    if (
        compatibility.get("status") != "passed"
        or compatibility.get("selectedModel") != profile.model
        or compatibility.get("promptSha256") != profile.promptSha256
        or compatibility.get("schemaSha256") != profile.schemaSha256
    ):
        raise SystemExit("compatibility evidence does not match the frozen profile")
    key = os.environ.get("ARK_JUDGE_API_KEY", "").strip() or os.environ.get(
        "ARK_API_KEY", ""
    ).strip()
    if not key:
        raise SystemExit("ARK_JUDGE_API_KEY/ARK_API_KEY is required; no request was sent")
    settings = ArkSettings(
        api_key=key,
        model=profile.model,
        base_url=os.environ.get("ARK_JUDGE_BASE_URL", "").strip()
        or os.environ.get("ARK_BASE_URL", "").strip()
        or DEFAULT_ARK_BASE_URL,
        request_timeout_seconds=profile.timeoutSeconds,
    )
    judge = JudgeAdapter(
        settings=settings,
        profile_id=profile.profileId,
        cost=JudgeCostConfig(prompt_cny_per_1k=0.0, completion_cny_per_1k=0.0),
    )
    calibration_items = _load_calibration_cases(args.calibration_file)
    source = json.loads(args.source_canonical.read_text(encoding="utf-8"))
    started = time.perf_counter()
    try:
        calibration = await _live_calibration_report(
            calibration_items,
            judge,
            max_calls=len(calibration_items) * 2,
            max_cost_cny=None,
            existing_cost_cny=0.0,
            timeout_seconds=args.timeout_seconds,
        )
        calibration["profileId"] = profile.profileId
        calibration["model"] = profile.model
        calibration["costEstimateAvailable"] = False
        calibration["estimatedCostCny"] = None
        judge_only = None
        if calibration.get("qualityGateStatus") == "quality-gate":
            remaining = args.timeout_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise TimeoutError("no time remains for Judge-only evaluation")
            judge_only = await _judge_saved_summaries(
                judge=judge,
                cases=_load_cases(args.case_file),
                source=source,
                timeout_seconds=remaining,
            )
    finally:
        await judge.close()
    report = {
        "schemaVersion": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "profileId": profile.profileId,
        "profile": profile.model_dump(),
        "model": profile.model,
        "compatibility": {
            "status": compatibility["status"],
            "physicalRequests": compatibility["physicalRequests"],
            "tokenUsage": compatibility["tokenUsage"],
        },
        "calibration": calibration,
        "judgeOnly47": judge_only,
        "sourceCanonical": args.source_canonical.as_posix(),
        "candidateCalls": 0,
        "embeddingCalls": 0,
        "costEstimateAvailable": False,
        "estimatedCostCny": None,
        "humanValidated": False,
    }
    args.output.mkdir(parents=True)
    (args.output / "judge_profile_completion.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "judge_profile_completion.md").write_text(
        _markdown(report),
        encoding="utf-8",
    )
    print(json.dumps({
        "profileId": profile.profileId,
        "model": profile.model,
        "calibrationGate": calibration.get("qualityGateStatus"),
        "calibrationCalls": calibration.get("judgeCalls"),
        "judgeOnlyExecuted": judge_only is not None,
        "judgeOnlyCases": judge_only.get("scoredCases") if isinstance(judge_only, dict) else 0,
    }, ensure_ascii=False, sort_keys=True, indent=2))
    complete = (
        calibration.get("qualityGateStatus") == "quality-gate"
        and isinstance(judge_only, dict)
        and judge_only.get("status") == "completed"
    )
    return 0 if complete else 2


def main() -> int:
    return asyncio.run(_run(_parser()))


if __name__ == "__main__":
    raise SystemExit(main())
