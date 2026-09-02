from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .models import ResumeMetrics
from .statistics import percentile


def _get(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def aggregate_cases(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in cases]
    success = [bool(_get(row, "gameplaySuccess", "gameplay_success", default=False)) for row in rows]
    infra = [bool(_get(row, "infraValid", "infra_valid", default=False)) for row in rows]
    failures = Counter(
        str(_get(row, "failureType", "failure_type"))
        for row in rows
        if _get(row, "failureType", "failure_type")
    )
    latencies: list[float] = []
    tokens = 0
    afp = 0.0
    cost = 0.0
    for row in rows:
        telemetry = _get(row, "telemetry", default={}) or {}
        if not isinstance(telemetry, Mapping):
            continue
        duration = _get(telemetry, "run_duration_ms", "durationMs", "runDurationMs")
        if duration is not None:
            latencies.append(float(duration))
        tokens += int(_get(telemetry, "totalTokens", "total_tokens", default=0) or 0)
        afp += float(_get(telemetry, "afp_used", "afpUsed", "afp", default=0) or 0)
        cost += float(
            _get(telemetry, "cost_cny_estimated", "costCnyEstimated", default=0) or 0
        )
    successes = sum(success)
    return {
        "sampleSize": len(rows),
        "infraValidCount": sum(infra),
        "infraValidRate": sum(infra) / len(rows) if rows else None,
        "gameplaySuccessCount": successes,
        "taskSuccessRate": successes / len(rows) if rows else None,
        "failureTaxonomy": dict(sorted(failures.items())),
        "totalTokens": tokens,
        "afpUsed": afp,
        "costCnyEstimated": cost,
        "afpPerSuccess": afp / successes if successes else None,
        "tokensPerSuccess": tokens / successes if successes else None,
        "costPerSuccessCnyEstimated": cost / successes if successes else None,
        "p50LatencyMs": percentile(latencies, 50),
        "p95LatencyMs": percentile(latencies, 95),
    }


def resume_metrics_from_aggregate(
    experiment_id: str,
    aggregate: Mapping[str, Any],
    *,
    primary_name: str = "taskSuccessRate",
    primary_unit: str = "ratio",
    limitations: Iterable[str] = (),
) -> ResumeMetrics:
    verified = aggregate.get("hypothesisVerified")
    return ResumeMetrics(
        experiment_id=experiment_id,
        sample_size=int(aggregate.get("sampleSize", 0)),
        primary_metric_name=primary_name,
        primary_metric_value=(
            float(aggregate[primary_name]) if aggregate.get(primary_name) is not None else None
        ),
        primary_metric_unit=primary_unit,
        baselines=tuple(aggregate.get("baselines", ())),
        effect_size=aggregate.get("effectSize"),
        confidence_interval_95=(
            tuple(aggregate["confidenceInterval95"])
            if aggregate.get("confidenceInterval95") is not None
            else None
        ),
        afp_per_success=aggregate.get("afpPerSuccess"),
        cost_per_success_cny_estimated=aggregate.get("costPerSuccessCnyEstimated"),
        p95_latency_ms=aggregate.get("p95LatencyMs"),
        limitations=tuple(limitations),
        hypothesis_verified=bool(verified) if verified is not None else None,
    )


def markdown_report(experiment_id: str, suite: str, aggregate: Mapping[str, Any]) -> str:
    verified = aggregate.get("hypothesisVerified")
    conclusion = (
        "已达到预注册阈值。"
        if verified is True
        else "未达到或尚不能验证预注册阈值。"
        if verified is False
        else "当前制品未包含可判定的研究假设。"
    )
    return (
        f"# Benchmark report: {experiment_id}\n\n"
        f"- Suite: `{suite}`\n"
        f"- Sample size: {aggregate.get('sampleSize', 0)}\n"
        f"- Infrastructure valid rate: {aggregate.get('infraValidRate')}\n"
        f"- Primary success rate: {aggregate.get('taskSuccessRate')}\n"
        f"- P95 latency: {aggregate.get('p95LatencyMs')} ms\n"
        f"- AFP per success: {aggregate.get('afpPerSuccess')}\n\n"
        f"## Conclusion\n\n{conclusion}\n"
    )


__all__ = ["aggregate_cases", "markdown_report", "resume_metrics_from_aggregate"]
