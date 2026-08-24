from __future__ import annotations

import json

import pytest
from core.backend.app.simulation.evidence import (
    gameplay_evidence_markdown,
    load_batch_reports,
    summarize_gameplay_evidence,
)


def _report(
    route: str,
    seed: int,
    *,
    branch: str,
    player_result: str | None,
    player_speech: int,
    stance_changes: int,
    gate_failures: list[str] | None = None,
) -> dict[str, object]:
    return {
        "mode": "real",
        "route": route,
        "runId": f"run_{route}_{seed}",
        "seed": seed,
        "_evidenceSource": {
            "path": f"report_{route}_{seed}.json",
            "sha256": f"sha256-{route}-{seed}",
            "mode": "real",
            "backend": "postgres",
            "keepRuns": False,
            "runsRequested": 1,
            "runsCompleted": 1,
            "embeddingPreflightPassed": True,
        },
        "metrics": {
            "finalWorldTime": "Day7 18:00",
            "abnormalTermination": None,
            "budgetExhausted": False,
            "repositoryRecovered": True,
            "temporaryRunDeleted": True,
            "worldEvents": {
                "firedIds": [f"event_day{day}" for day in range(1, 8)],
                "skippedIds": [],
            },
            "speech": {"player": player_speech},
            "chapterStanceChangeCount": stance_changes,
            "goalCompletionRate": 0.2,
            "day7Branch": branch,
            "playerResult": player_result,
            "costEstimate": {
                "totalCny": 1.25,
                "currency": "CNY",
                "embeddingProviderRequests": 1,
            },
            "qualityGateFailures": gate_failures or [],
        },
    }


def test_three_seed_route_matrix_proves_success_failure_and_observer() -> None:
    reports = []
    for offset in range(3):
        reports.extend(
            [
                _report(
                    "observer",
                    100 + offset,
                    branch="no_submission",
                    player_result=None,
                    player_speech=0,
                    stance_changes=0,
                ),
                _report(
                    "pro_lin",
                    200 + offset,
                    branch="compromise_submitted",
                    player_result="partial",
                    player_speech=7,
                    stance_changes=5,
                ),
                _report(
                    "pro_zhao",
                    300 + offset,
                    branch="no_submission",
                    player_result="failed",
                    player_speech=2,
                    stance_changes=1,
                ),
            ]
        )

    summary = summarize_gameplay_evidence(reports)

    assert summary["complete"] is True
    assert summary["requirementFailures"] == []
    assert summary["routes"]["pro_lin"]["acceptedEvidenceRuns"] == 3
    assert summary["routes"]["pro_zhao"]["branchCounts"] == {
        "no_submission": 3
    }
    assert summary["routes"]["observer"]["playerSpeechTotal"] == 0
    assert summary["totalEstimatedCostCny"] == 11.25
    markdown = gameplay_evidence_markdown(summary)
    assert "compromise_submitted" in markdown
    assert "dialogue text" not in markdown


def test_matrix_requires_distinct_seeds_and_enough_accepted_outcomes() -> None:
    reports = [
        _report(
            "pro_lin",
            1,
            branch="compromise_submitted",
            player_result="partial",
            player_speech=7,
            stance_changes=5,
        ),
        _report(
            "pro_lin",
            2,
            branch="no_submission",
            player_result="failed",
            player_speech=7,
            stance_changes=4,
            gate_failures=["success_branch_not_reached"],
        ),
        _report(
            "pro_lin",
            3,
            branch="no_submission",
            player_result="failed",
            player_speech=7,
            stance_changes=4,
            gate_failures=["success_branch_not_reached"],
        ),
    ]

    summary = summarize_gameplay_evidence(reports)

    assert summary["complete"] is False
    assert "pro_lin:insufficient_distinct_seeds" not in summary["requirementFailures"]
    assert "pro_lin:insufficient_accepted_evidence" in summary["requirementFailures"]
    assert "observer:insufficient_distinct_seeds" in summary["requirementFailures"]


def test_duplicate_route_seed_is_rejected() -> None:
    report = _report(
        "observer",
        7,
        branch="no_submission",
        player_result=None,
        player_speech=0,
        stance_changes=0,
    )

    with pytest.raises(ValueError, match="duplicate route/seed"):
        summarize_gameplay_evidence([report, report])


def test_batch_loader_accepts_only_report_lists(tmp_path) -> None:
    report = _report(
        "observer",
        7,
        branch="no_submission",
        player_result=None,
        player_speech=0,
        stance_changes=0,
    )
    path = tmp_path / "batch.json"
    batch = {
        "mode": "real",
        "backend": "postgres",
        "keepRuns": False,
        "runsRequested": 1,
        "runsCompleted": 1,
        "embeddingPreflight": {"vectorCount": 1},
        "reports": [report],
    }
    path.write_text(json.dumps(batch), encoding="utf-8")

    loaded = load_batch_reports([path])
    assert len(loaded) == 1
    assert loaded[0]["route"] == "observer"
    assert loaded[0]["_evidenceSource"]["mode"] == "real"
    assert len(loaded[0]["_evidenceSource"]["sha256"]) == 64

    path.write_text(json.dumps({"reports": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="no reports list"):
        load_batch_reports([path])


def test_legacy_report_cost_is_derived_from_text_tokens_only() -> None:
    report = _report(
        "observer",
        7,
        branch="no_submission",
        player_result=None,
        player_speech=0,
        stance_changes=0,
    )
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    metrics.pop("costEstimate")
    metrics["tokens"] = {
        "ChatDecision:prompt": 1_000_000,
        "ChatDecision:completion": 100_000,
        "ChatDecision:total": 1_100_000,
    }

    summary = summarize_gameplay_evidence([report])

    assert summary["runs"][0]["estimatedCostCny"] == 0.96
    assert summary["runs"][0]["costBasis"] == "legacy_text_tokens_only"
    assert summary["runs"][0]["acceptedEvidence"] is False
    assert "current_cost_estimate_missing" in summary["runs"][0]["qualityGateFailures"]
