"""Aggregate redacted seven-day simulation reports into gameplay evidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

EXPECTED_ROUTES = ("observer", "pro_lin", "pro_zhao")
SUCCESS_BRANCHES = {"compromise_submitted", "consensus_submitted"}


def load_batch_reports(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Load report entries from one or more safe batch JSON files."""

    reports: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
        items = payload.get("reports")
        if not isinstance(items, list):
            raise ValueError(f"batch report has no reports list: {path}")
        embedding_preflight = payload.get("embeddingPreflight")
        source = {
            "path": str(path.resolve()),
            "sha256": sha256(raw_bytes).hexdigest(),
            "mode": payload.get("mode"),
            "backend": payload.get("backend"),
            "keepRuns": payload.get("keepRuns"),
            "runsRequested": payload.get("runsRequested"),
            "runsCompleted": payload.get("runsCompleted"),
            "embeddingPreflightPassed": (
                isinstance(embedding_preflight, dict)
                and _integer_or_zero(embedding_preflight.get("vectorCount")) >= 1
            ),
        }
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"batch report contains a non-object entry: {path}")
            report = dict(item)
            report["_evidenceSource"] = source
            reports.append(report)
    return reports


def summarize_gameplay_evidence(
    reports: Iterable[dict[str, Any]],
    *,
    minimum_seeds_per_route: int = 3,
) -> dict[str, Any]:
    """Build a deterministic, privacy-preserving route/seed acceptance matrix."""

    if minimum_seeds_per_route < 1:
        raise ValueError("minimum_seeds_per_route must be positive")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for report in reports:
        route = report.get("route")
        seed = report.get("seed")
        metrics = report.get("metrics")
        if route not in EXPECTED_ROUTES or not isinstance(seed, int):
            raise ValueError("report route/seed is invalid")
        if not isinstance(metrics, dict):
            raise ValueError("report metrics is invalid")
        identity = (route, seed)
        if identity in seen:
            raise ValueError(f"duplicate route/seed report: {route}/{seed}")
        seen.add(identity)

        speech = metrics.get("speech", {})
        cost = metrics.get("costEstimate")
        quality_failures = metrics.get("qualityGateFailures", [])
        if not isinstance(speech, dict):
            raise ValueError("report speech metrics are invalid")
        if not isinstance(quality_failures, list):
            raise ValueError("report quality gate metrics are invalid")
        player_speech = _integer(speech.get("player"))
        stance_changes = _integer(metrics.get("chapterStanceChangeCount"))
        goal_rate = _optional_number(metrics.get("goalCompletionRate"))
        cost_cny, cost_basis = _cost_estimate(metrics, cost)
        branch = _optional_string(metrics.get("day7Branch"))
        player_result = _optional_string(metrics.get("playerResult"))
        technical_failures = sorted(
            {
                *(str(item) for item in quality_failures),
                *_current_report_failures(report, metrics, cost, goal_rate),
            }
        )
        technical_pass = not technical_failures
        route_outcome_pass = _route_outcome_pass(
            route,
            branch=branch,
            player_result=player_result,
            player_speech=player_speech,
            stance_changes=stance_changes,
        )
        rows.append(
            {
                "route": route,
                "seed": seed,
                "playerSpeechCount": player_speech,
                "changedNpcStanceCount": stance_changes,
                "goalCompletionRate": goal_rate,
                "branch": branch,
                "playerResult": player_result,
                "estimatedCostCny": cost_cny,
                "costBasis": cost_basis,
                "technicalPass": technical_pass,
                "routeOutcomePass": route_outcome_pass,
                "acceptedEvidence": technical_pass and route_outcome_pass,
                "qualityGateFailures": technical_failures,
                "sourceSha256": _source_string(report, "sha256"),
                "sourcePath": _source_string(report, "path"),
                "runId": _optional_string(report.get("runId")),
            }
        )
    rows.sort(key=lambda item: (EXPECTED_ROUTES.index(item["route"]), item["seed"]))

    route_summaries: dict[str, dict[str, Any]] = {}
    requirement_failures: list[str] = []
    for route in EXPECTED_ROUTES:
        route_rows = [row for row in rows if row["route"] == route]
        distinct_seeds = {row["seed"] for row in route_rows}
        accepted = [row for row in route_rows if row["acceptedEvidence"]]
        required_outcomes = minimum_seeds_per_route
        branch_counts = Counter(row["branch"] or "n/a" for row in route_rows)
        result_counts = Counter(row["playerResult"] or "n/a" for row in route_rows)
        route_summary = {
            "runs": len(route_rows),
            "distinctSeeds": len(distinct_seeds),
            "acceptedEvidenceRuns": len(accepted),
            "requiredEvidenceRuns": required_outcomes,
            "playerSpeechTotal": sum(row["playerSpeechCount"] for row in route_rows),
            "changedNpcStanceAverage": _average(
                row["changedNpcStanceCount"] for row in route_rows
            ),
            "goalCompletionRateAverage": _average_optional(
                row["goalCompletionRate"] for row in route_rows
            ),
            "estimatedCostCnyTotal": round(
                sum(row["estimatedCostCny"] or 0.0 for row in route_rows), 6
            ),
            "branchCounts": dict(sorted(branch_counts.items())),
            "playerResultCounts": dict(sorted(result_counts.items())),
            "complete": (
                len(distinct_seeds) >= minimum_seeds_per_route
                and len(accepted) >= required_outcomes
            ),
        }
        route_summaries[route] = route_summary
        if len(distinct_seeds) < minimum_seeds_per_route:
            requirement_failures.append(f"{route}:insufficient_distinct_seeds")
        if len(accepted) < required_outcomes:
            requirement_failures.append(f"{route}:insufficient_accepted_evidence")

    sources = sorted(
        {
            (row["sourcePath"], row["sourceSha256"])
            for row in rows
            if row["sourcePath"] and row["sourceSha256"]
        }
    )
    return {
        "minimumSeedsPerRoute": minimum_seeds_per_route,
        "routes": route_summaries,
        "runs": rows,
        "sources": [
            {"path": path, "sha256": digest} for path, digest in sources
        ],
        "totalEstimatedCostCny": round(
            sum(row["estimatedCostCny"] or 0.0 for row in rows), 6
        ),
        "requirementFailures": requirement_failures,
        "complete": not requirement_failures,
    }


def gameplay_evidence_markdown(summary: dict[str, Any]) -> str:
    """Render the aggregate matrix without dialogue or private state."""

    lines = [
        "# Seven-day gameplay reachability evidence",
        "",
        f"- Complete: `{summary['complete']}`",
        f"- Minimum seeds per route: `{summary['minimumSeedsPerRoute']}`",
        f"- Total estimated cost (CNY): `{summary['totalEstimatedCostCny']}`",
        f"- Requirement failures: `{','.join(summary['requirementFailures']) or 'none'}`",
        "",
        "| Route | Seeds | Accepted evidence | Player lines | Changed stance avg | Goal completion avg | Cost CNY | Branches | Complete |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for route in EXPECTED_ROUTES:
        item = summary["routes"][route]
        lines.append(
            f"| `{route}` | {item['distinctSeeds']} "
            f"| {item['acceptedEvidenceRuns']}/{item['requiredEvidenceRuns']} "
            f"| {item['playerSpeechTotal']} "
            f"| {item['changedNpcStanceAverage']} "
            f"| {item['goalCompletionRateAverage']} "
            f"| {item['estimatedCostCnyTotal']} "
            f"| `{json.dumps(item['branchCounts'], ensure_ascii=False, sort_keys=True)}` "
            f"| `{item['complete']}` |"
        )
    lines.extend(
        [
            "",
            "| Route | Seed | Player lines | Changed NPC stances | Goal completion | Branch | Player result | Cost CNY | Cost basis | Technical | Outcome | Accepted |",
            "|---|---:|---:|---:|---:|---|---|---:|---|---|---|---|",
        ]
    )
    for row in summary["runs"]:
        lines.append(
            f"| `{row['route']}` | {row['seed']} | {row['playerSpeechCount']} "
            f"| {row['changedNpcStanceCount']} "
            f"| {row['goalCompletionRate'] if row['goalCompletionRate'] is not None else 'n/a'} "
            f"| `{row['branch'] or 'n/a'}` | `{row['playerResult'] or 'n/a'}` "
            f"| {row['estimatedCostCny'] if row['estimatedCostCny'] is not None else 'n/a'} "
            f"| `{row['costBasis']}` "
            f"| `{row['technicalPass']}` | `{row['routeOutcomePass']}` "
            f"| `{row['acceptedEvidence']}` |"
        )
    return "\n".join(lines) + "\n"


def _route_outcome_pass(
    route: str,
    *,
    branch: str | None,
    player_result: str | None,
    player_speech: int,
    stance_changes: int,
) -> bool:
    if route == "observer":
        return player_speech == 0 and branch == "no_submission"
    if route == "pro_lin":
        return (
            branch in SUCCESS_BRANCHES
            and player_result in {"completed", "partial"}
            and player_speech >= 5
            and stance_changes >= 3
        )
    return (
        branch == "no_submission"
        and player_result == "failed"
        and player_speech >= 2
    )


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer metric")
    return value


def _integer_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _source_string(report: dict[str, Any], key: str) -> str | None:
    source = report.get("_evidenceSource")
    if not isinstance(source, dict):
        return None
    return _optional_string(source.get(key))


def _current_report_failures(
    report: dict[str, Any],
    metrics: dict[str, Any],
    cost: Any,
    goal_rate: float | None,
) -> set[str]:
    failures: set[str] = set()
    source = report.get("_evidenceSource")
    if not isinstance(source, dict):
        return {"missing_evidence_source"}
    if source.get("mode") != "real" or report.get("mode") != "real":
        failures.add("not_real_mode")
    if source.get("backend") != "postgres":
        failures.add("not_postgres_backend")
    if source.get("keepRuns") is not False:
        failures.add("temporary_runs_kept")
    if source.get("runsCompleted") != source.get("runsRequested"):
        failures.add("batch_incomplete")
    if source.get("embeddingPreflightPassed") is not True:
        failures.add("embedding_preflight_not_proven")
    if metrics.get("finalWorldTime") != "Day7 18:00":
        failures.add("day7_not_reached")
    if metrics.get("abnormalTermination") is not None:
        failures.add("abnormal_termination")
    if metrics.get("budgetExhausted") is not False:
        failures.add("budget_exhausted_or_missing")
    if metrics.get("repositoryRecovered") is not True:
        failures.add("repository_not_recovered")
    if metrics.get("temporaryRunDeleted") is not True:
        failures.add("temporary_run_not_deleted")
    world_events = metrics.get("worldEvents")
    if (
        not isinstance(world_events, dict)
        or len(world_events.get("firedIds", [])) != 7
        or world_events.get("skippedIds") != []
    ):
        failures.add("world_events_incomplete")
    if goal_rate is None or not 0.0 <= goal_rate <= 1.0:
        failures.add("goal_completion_rate_missing_or_invalid")
    if not isinstance(cost, dict):
        failures.add("current_cost_estimate_missing")
    else:
        if cost.get("currency") != "CNY":
            failures.add("cost_currency_invalid")
        if _optional_number(cost.get("totalCny")) is None:
            failures.add("cost_total_missing")
        if _integer_or_zero(cost.get("embeddingProviderRequests")) < 1:
            failures.add("embedding_usage_missing")
    run_id = report.get("runId")
    if not isinstance(run_id, str) or not run_id:
        failures.add("run_id_missing")
    return failures


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a numeric metric")
    return float(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected a string metric")
    return value


def _cost_estimate(
    metrics: dict[str, Any], cost: Any
) -> tuple[float | None, str]:
    if isinstance(cost, dict):
        return _optional_number(cost.get("totalCny")), str(
            cost.get("basis", "reported")
        )
    tokens = metrics.get("tokens")
    if not isinstance(tokens, dict):
        return None, "unavailable"
    prompt = sum(
        value
        for key, value in tokens.items()
        if isinstance(key, str)
        and key.endswith(":prompt")
        and isinstance(value, int)
        and not isinstance(value, bool)
    )
    completion = sum(
        value
        for key, value in tokens.items()
        if isinstance(key, str)
        and key.endswith(":completion")
        and isinstance(value, int)
        and not isinstance(value, bool)
    )
    if prompt == 0 and completion == 0:
        return None, "unavailable"
    # Legacy reports predate explicit pricing/embedding accounting.  Preserve
    # comparability with the current configured 0-32k public token rates while
    # labeling the result as text-only rather than inventing embedding usage.
    value = (prompt * 0.6 + completion * 3.6) / 1_000_000
    return round(value, 6), "legacy_text_tokens_only"


def _average(values: Iterable[int]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else None


def _average_optional(values: Iterable[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    return round(sum(items) / len(items), 6) if items else None


__all__ = [
    "EXPECTED_ROUTES",
    "SUCCESS_BRANCHES",
    "gameplay_evidence_markdown",
    "load_batch_reports",
    "summarize_gameplay_evidence",
]
