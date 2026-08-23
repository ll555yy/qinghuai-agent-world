"""Aggregate redacted seven-day simulation reports into gameplay evidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from .manifest import (
    TERMINAL_ATTEMPT_STATUSES,
    ManifestValidationError,
    canonical_manifest_sha256,
    make_attempt_id,
    planned_attempts,
    validate_manifest,
)

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
        source_digest = sha256(raw_bytes).hexdigest()
        declared_source_id = payload.get("sourceId")
        source_id = (
            declared_source_id
            if isinstance(declared_source_id, str) and declared_source_id
            else f"batch:{source_digest[:16]}"
        )
        source = {
            # A source ID is deliberately stable and non-local.  The input
            # path is useful to the CLI caller but must never enter canonical
            # evidence JSON.
            "sourceId": source_id,
            "sha256": sha256(raw_bytes).hexdigest(),
            "mode": payload.get("mode"),
            "backend": payload.get("backend"),
            "keepRuns": payload.get("keepRuns"),
            "runsRequested": payload.get("runsRequested"),
            "runsCompleted": payload.get("runsCompleted"),
            "experimentId": payload.get("experimentId"),
            "manifestDigest": payload.get("manifestDigest"),
            "attempts": payload.get("attempts", []),
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
            # Batch-level attempt ledgers are copied onto the corresponding
            # report for the manifest-aware summarizer.  This remains redacted
            # and contains no path, prompt, or dialogue text.
            attempt_id = report.get("attemptId")
            for raw_attempt in source["attempts"]:
                if isinstance(raw_attempt, dict) and raw_attempt.get("attemptId") == attempt_id:
                    report["_attemptRecord"] = dict(raw_attempt)
                    break
            reports.append(report)
    return reports


def summarize_gameplay_evidence(
    reports: Iterable[dict[str, Any]],
    *,
    minimum_seeds_per_route: int = 3,
    manifest: dict[str, Any] | None = None,
    attempt_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, privacy-preserving route/seed acceptance matrix."""

    if manifest is not None:
        return summarize_preregistered_evidence(
            manifest,
            reports,
            attempt_records=attempt_records,
        )

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
                "sourceId": _source_string(report, "sourceId"),
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
            (row["sourceId"], row["sourceSha256"])
            for row in rows
            if row["sourceId"] and row["sourceSha256"]
        }
    )
    return {
        "minimumSeedsPerRoute": minimum_seeds_per_route,
        "routes": route_summaries,
        "runs": rows,
        "sources": [
            {"sourceId": source_id, "sha256": digest}
            for source_id, digest in sources
        ],
        "totalEstimatedCostCny": round(
            sum(row["estimatedCostCny"] or 0.0 for row in rows), 6
        ),
        "requirementFailures": requirement_failures,
        "complete": not requirement_failures,
    }


def summarize_preregistered_evidence(
    manifest: dict[str, Any],
    reports: Iterable[dict[str, Any]],
    *,
    attempt_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize the manifest's *entire* planned denominator.

    The caller cannot reduce the denominator by passing only successful JSON
    files.  Missing reports, duplicate/unplanned route-seeds, digest drift,
    and any non-terminal lifecycle row keep ``complete`` false while retaining
    a visible row for every planned attempt.
    """

    try:
        checked_manifest = validate_manifest(manifest)
    except ManifestValidationError:
        # Preserve the public ValueError contract used by the legacy
        # summarizer while making invalid preregistration an explicit matrix
        # failure for callers that choose to inspect the result.
        raise
    expected_digest = canonical_manifest_sha256(checked_manifest)
    expected = planned_attempts(checked_manifest)
    expected_by_id = {str(item["attemptId"]): item for item in expected}
    expected_by_pair = {(item["route"], item["seed"]): item for item in expected}

    all_reports = list(reports)
    ledger_by_id: dict[str, dict[str, Any]] = {}
    if attempt_records is not None:
        for raw_record in attempt_records:
            if not isinstance(raw_record, dict):
                continue
            attempt_id = raw_record.get("attemptId")
            if isinstance(attempt_id, str):
                ledger_by_id[attempt_id] = raw_record
    for report in all_reports:
        raw_record = report.get("_attemptRecord")
        if isinstance(raw_record, dict) and isinstance(raw_record.get("attemptId"), str):
            ledger_by_id.setdefault(str(raw_record["attemptId"]), raw_record)

    report_by_id: dict[str, list[dict[str, Any]]] = {}
    extra_reports: list[dict[str, Any]] = []
    for report in all_reports:
        route = report.get("route")
        seed = report.get("seed")
        attempt_id = report.get("attemptId")
        if not isinstance(attempt_id, str) and isinstance(route, str) and isinstance(seed, int):
            attempt_id = make_attempt_id(str(checked_manifest["experimentId"]), route, seed)
        if not isinstance(attempt_id, str):
            extra_reports.append(
                {
                    "attemptId": None,
                    "route": route,
                    "seed": seed,
                    "reason": "missing_attempt_id",
                }
            )
            continue
        report_by_id.setdefault(attempt_id, []).append(report)
        if attempt_id not in expected_by_id:
            extra_reports.append(
                {
                    "attemptId": attempt_id,
                    "route": route,
                    "seed": seed,
                    "reason": "unplanned_attempt",
                }
            )

    rows: list[dict[str, Any]] = []
    matrix_failures: list[str] = []
    seen_pairs: set[tuple[str, int]] = set()
    for planned in expected:
        attempt_id = str(planned["attemptId"])
        matches = report_by_id.get(attempt_id, [])
        record = ledger_by_id.get(attempt_id)
        reasons: set[str] = set()
        report: dict[str, Any] | None = None
        if len(matches) > 1:
            reasons.add("duplicate_attempt")
            matrix_failures.append(f"duplicate_attempt:{attempt_id}")
        if matches:
            report = matches[0]
        else:
            reasons.add("missing_report")
            matrix_failures.append(f"missing_report:{attempt_id}")

        route = str(planned["route"])
        seed = int(planned["seed"])
        pair = (route, seed)
        if pair in seen_pairs:
            reasons.add("duplicate_route_seed")
            matrix_failures.append(f"duplicate_route_seed:{route}/{seed}")
        seen_pairs.add(pair)

        metrics: dict[str, Any] = {}
        if report is not None and isinstance(report.get("metrics"), dict):
            metrics = report["metrics"]
        elif report is not None:
            reasons.add("invalid_metrics")
        status = _attempt_status(report, record)
        if status not in TERMINAL_ATTEMPT_STATUSES:
            reasons.add("non_terminal_attempt")
            matrix_failures.append(f"non_terminal_attempt:{attempt_id}")
        if report is None and record is not None:
            # The ledger row is retained for visibility, but a missing report
            # still invalidates the canonical matrix per preregistration.
            status = str(record.get("status", "missing"))

        digest_values = {
            value
            for value in (
                report.get("manifestDigest") if report is not None else None,
                record.get("manifestDigest") if record is not None else None,
                report.get("_evidenceSource", {}).get("manifestDigest")
                if report is not None and isinstance(report.get("_evidenceSource"), dict)
                else None,
            )
            if value is not None
        }
        if report is not None and expected_digest not in digest_values:
            reasons.add("manifest_digest_mismatch")
            matrix_failures.append(f"manifest_digest_mismatch:{attempt_id}")
        if record is not None and record.get("manifestDigest") != expected_digest:
            reasons.add("attempt_digest_mismatch")
            matrix_failures.append(f"attempt_digest_mismatch:{attempt_id}")
        report_status = report.get("attemptStatus") if report is not None else None
        record_status = record.get("status") if record is not None else None
        if (
            isinstance(report_status, str)
            and isinstance(record_status, str)
            and report_status != record_status
        ):
            reasons.add("attempt_status_mismatch")
            matrix_failures.append(f"attempt_status_mismatch:{attempt_id}")

        if report is not None:
            cost = metrics.get("costEstimate")
            goal_rate = _optional_number(metrics.get("goalCompletionRate"))
            technical = _current_report_failures(report, metrics, cost, goal_rate)
            declared_quality = metrics.get("qualityGateFailures", [])
            if isinstance(declared_quality, list):
                technical.update(str(value) for value in declared_quality)
            else:
                technical.add("quality_gate_metrics_invalid")
            reasons.update(technical)
        else:
            cost = None
            goal_rate = None
        speech = metrics.get("speech") if isinstance(metrics.get("speech"), dict) else {}
        player_speech = _integer_or_zero(speech.get("player"))
        stance_changes = _integer_or_zero(metrics.get("chapterStanceChangeCount"))
        branch = _optional_string_or_none(metrics.get("day7Branch"))
        player_result = _optional_string_or_none(metrics.get("playerResult"))
        route_outcome = _preregistered_route_outcome_pass(
            route,
            branch=branch,
            player_result=player_result,
            player_speech=player_speech,
            stance_changes=stance_changes,
        )
        technical_failures = sorted(reasons)
        terminal = status in TERMINAL_ATTEMPT_STATUSES
        attempted = status not in {"not_started", "missing", None}
        if record is not None and status == "not_started":
            attempted = False
        record_infra = record.get("infraValid") if record is not None else None
        infra_valid = (
            bool(record_infra)
            if isinstance(record_infra, bool)
            else status == "completed" and terminal and "missing_report" not in reasons
        )
        gameplay_pass = bool(
            status == "completed"
            and terminal
            and not reasons
            and route_outcome
        )
        row = {
            "attemptId": attempt_id,
            "experimentId": planned["experimentId"],
            "route": route,
            "seed": seed,
            "strategyId": planned["strategyId"],
            "planned": True,
            "reportPresent": report is not None,
            "status": status,
            "terminal": terminal,
            "attempted": attempted,
            "infraValid": infra_valid,
            "gameplayPass": gameplay_pass,
            "playerSpeechCount": player_speech,
            "playerTaskCompleted": player_result == "completed",
            "changedNpcStanceCount": stance_changes,
            "goalCompletionRate": goal_rate,
            "branch": branch,
            "playerResult": player_result,
            "estimatedCostCny": _cost_estimate(metrics, cost)[0] if report is not None else None,
            "costBasis": _cost_estimate(metrics, cost)[1] if report is not None else "unavailable",
            "failureReasons": technical_failures,
            "sourceId": _source_string(report or {}, "sourceId") if report else None,
            "sourceSha256": _source_string(report or {}, "sha256") if report else None,
            "runId": _optional_string_or_none(report.get("runId")) if report else None,
        }
        rows.append(row)

    if extra_reports:
        matrix_failures.extend(
            f"{item['reason']}:{item.get('attemptId') or 'unknown'}"
            for item in extra_reports
        )
    # Duplicate route/seed pairs that arrive under different attempt IDs are
    # also unplanned from the matrix's perspective.
    for attempt_id, matches in report_by_id.items():
        if attempt_id not in expected_by_id:
            continue
        for report in matches:
            pair = (report.get("route"), report.get("seed"))
            if pair not in expected_by_pair:
                matrix_failures.append(f"unplanned_seed:{pair[0]}/{pair[1]}")

    route_summaries: dict[str, dict[str, Any]] = {}
    for route in EXPECTED_ROUTES:
        route_rows = [row for row in rows if row["route"] == route]
        planned_count = len(route_rows)
        attempted_count = sum(bool(row["attempted"]) for row in route_rows)
        infra_count = sum(bool(row["infraValid"]) for row in route_rows)
        gameplay_count = sum(bool(row["gameplayPass"]) for row in route_rows)
        completed_count = sum(bool(row["playerTaskCompleted"]) for row in route_rows)
        route_failures: list[str] = []
        if route == "observer" and any(row["playerSpeechCount"] != 0 for row in route_rows):
            route_failures.append("observer_player_speech_nonzero")
        if route == "pro_lin":
            acceptance = checked_manifest.get("acceptance", {}).get(route, {})
            minimum_passes = int(acceptance.get("minimumGameplayPasses", 4))
            minimum_completed = int(acceptance.get("minimumPlayerCompletedRuns", 2))
            if gameplay_count < minimum_passes:
                route_failures.append("pro_lin_gameplay_pass_below_gate")
            if completed_count < minimum_completed:
                route_failures.append("pro_lin_player_completed_below_gate")
        if route == "pro_zhao":
            acceptance = checked_manifest.get("acceptance", {}).get(route, {})
            minimum_failure = int(acceptance.get("minimumFailureControlPasses", 4))
            if gameplay_count < minimum_failure:
                route_failures.append("pro_zhao_failure_control_below_gate")
        matrix_failures.extend(f"{route}:{failure}" for failure in route_failures)
        status_counts = Counter(str(row["status"]) for row in route_rows)
        route_summaries[route] = {
            "planned": planned_count,
            "attempted": attempted_count,
            "infraValid": infra_count,
            "gameplayPass": gameplay_count,
            "playerTaskCompleted": completed_count,
            "coverageRate": _rate(attempted_count, planned_count),
            "ittSuccessRate": _rate(gameplay_count, planned_count),
            "validRunSuccessRate": _rate(gameplay_count, infra_count),
            "statusCounts": dict(sorted(status_counts.items())),
            "failureCounts": dict(
                sorted(
                    Counter(
                        reason
                        for row in route_rows
                        for reason in row["failureReasons"]
                    ).items()
                )
            ),
            "complete": not route_failures
            and all(row["reportPresent"] for row in route_rows)
            and all(row["terminal"] for row in route_rows)
            and all(not row["failureReasons"] for row in route_rows),
        }

    # Preserve the declared seed matrix in the output, even when none of the
    # providers returned a report.  This makes missing/failed attempts visible
    # rather than allowing the caller to choose a success-only denominator.
    sources = sorted(
        {
            (row["sourceId"], row["sourceSha256"])
            for row in rows
            if row["sourceId"] and row["sourceSha256"]
        }
    )
    total_planned = len(rows)
    total_attempted = sum(bool(row["attempted"]) for row in rows)
    total_infra = sum(bool(row["infraValid"]) for row in rows)
    total_gameplay = sum(bool(row["gameplayPass"]) for row in rows)
    unique_failures = sorted(set(matrix_failures))
    return {
        "experimentId": checked_manifest["experimentId"],
        "manifestDigest": expected_digest,
        "planned": total_planned,
        "attempted": total_attempted,
        "infraValid": total_infra,
        "gameplayPass": total_gameplay,
        "coverageRate": _rate(total_attempted, total_planned),
        "ittSuccessRate": _rate(total_gameplay, total_planned),
        "validRunSuccessRate": _rate(total_gameplay, total_infra),
        "routes": route_summaries,
        "runs": rows,
        "unplannedReports": extra_reports,
        "sources": [
            {"sourceId": source_id, "sha256": digest}
            for source_id, digest in sources
        ],
        "failureSummary": dict(
            sorted(Counter(reason.split(":", 1)[0] for reason in unique_failures).items())
        ),
        "requirementFailures": unique_failures,
        "complete": not unique_failures,
    }


def _attempt_status(
    report: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> str | None:
    for value in (
        report.get("attemptStatus") if report is not None else None,
        record.get("status") if record is not None else None,
    ):
        if isinstance(value, str):
            return value
    return None


def _optional_string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _integer_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _preregistered_route_outcome_pass(
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
    return branch == "no_submission" and player_result == "failed" and player_speech >= 2


def gameplay_evidence_markdown(summary: dict[str, Any]) -> str:
    """Render the aggregate matrix without dialogue or private state."""

    if "planned" in summary:
        return preregistered_evidence_markdown(summary)

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


def preregistered_evidence_markdown(summary: dict[str, Any]) -> str:
    """Render full-denominator prereg evidence with every planned row visible."""

    lines = [
        "# Preregistered seven-day simulation evidence",
        "",
        f"- Experiment: `{summary['experimentId']}`",
        f"- Manifest digest: `{summary['manifestDigest']}`",
        f"- Complete: `{summary['complete']}`",
        f"- Planned / attempted / infra-valid / gameplay-pass: `{summary['planned']}/{summary['attempted']}/{summary['infraValid']}/{summary['gameplayPass']}`",
        f"- Coverage / ITT / valid-run success: `{summary['coverageRate']}` / `{summary['ittSuccessRate']}` / `{summary['validRunSuccessRate']}`",
        f"- Requirement failures: `{','.join(summary['requirementFailures']) or 'none'}`",
        "",
        "| Route | Planned | Attempted | Infra-valid | Gameplay pass | Completed player tasks | Coverage | ITT | Valid-run | Complete |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for route in EXPECTED_ROUTES:
        item = summary["routes"][route]
        lines.append(
            f"| `{route}` | {item['planned']} | {item['attempted']} | {item['infraValid']} "
            f"| {item['gameplayPass']} | {item['playerTaskCompleted']} "
            f"| {item['coverageRate']} | {item['ittSuccessRate']} "
            f"| {item['validRunSuccessRate']} | `{item['complete']}` |"
        )
    lines.extend(
        [
            "",
            "| Attempt | Route | Seed | Status | Terminal | Attempted | Infra-valid | Gameplay pass | Player result | Failure reasons | Source |",
            "|---|---|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in summary["runs"]:
        lines.append(
            f"| `{row['attemptId']}` | `{row['route']}` | {row['seed']} "
            f"| `{row['status']}` | `{row['terminal']}` | `{row['attempted']}` "
            f"| `{row['infraValid']}` | `{row['gameplayPass']}` "
            f"| `{row['playerResult'] or 'n/a'}` "
            f"| `{','.join(row['failureReasons']) or 'none'}` "
            f"| `{row['sourceId'] or 'n/a'}` |"
        )
    if summary.get("unplannedReports"):
        lines.extend(["", "## Unplanned reports", ""])
        lines.extend(
            f"- `{item.get('attemptId') or 'missing-id'}`: `{item['reason']}`"
            for item in summary["unplannedReports"]
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
    "preregistered_evidence_markdown",
    "summarize_gameplay_evidence",
    "summarize_preregistered_evidence",
]
