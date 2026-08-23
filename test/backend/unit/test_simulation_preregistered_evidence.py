from __future__ import annotations

import copy
import json

from core.backend.app.simulation.evidence import (
    load_batch_reports,
    preregistered_evidence_markdown,
    summarize_preregistered_evidence,
)
from core.backend.app.simulation.manifest import load_manifest, planned_attempts


def _valid_report(planned: dict[str, object], digest: str) -> dict[str, object]:
    route = str(planned["route"])
    if route == "observer":
        branch, result, speech, stance = "no_submission", None, 0, 0
    elif route == "pro_lin":
        branch, result, speech, stance = "compromise_submitted", "completed", 7, 5
    else:
        branch, result, speech, stance = "no_submission", "failed", 2, 1
    return {
        "mode": "real",
        "route": route,
        "seed": planned["seed"],
        "runId": f"run-{planned['attemptId']}",
        "attemptId": planned["attemptId"],
        "attemptStatus": "completed",
        "manifestDigest": digest,
        "_evidenceSource": {
            "sourceId": "batch:test",
            "sha256": "a" * 64,
            "manifestDigest": digest,
            "mode": "real",
            "backend": "postgres",
            "keepRuns": False,
            "runsRequested": 15,
            "runsCompleted": 15,
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
            "speech": {"player": speech},
            "chapterStanceChangeCount": stance,
            "goalCompletionRate": 0.2,
            "day7Branch": branch,
            "playerResult": result,
            "costEstimate": {
                "totalCny": 1.0,
                "currency": "CNY",
                "embeddingProviderRequests": 1,
            },
            "qualityGateFailures": [],
        },
    }


def test_preregistered_summary_uses_manifest_denominator_and_reports_itt() -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    reports = [_valid_report(item, digest) for item in planned]

    summary = summarize_preregistered_evidence(manifest, reports)

    assert summary["planned"] == 15
    assert summary["attempted"] == 15
    assert summary["infraValid"] == 15
    assert summary["gameplayPass"] == 15
    assert summary["coverageRate"] == 1.0
    assert summary["ittSuccessRate"] == 1.0
    assert summary["validRunSuccessRate"] == 1.0
    assert summary["complete"] is True
    assert summary["routes"]["pro_lin"]["playerTaskCompleted"] == 5
    assert "C:\\Users" not in str(summary)
    assert "Attempt" in preregistered_evidence_markdown(summary)


def test_preregistered_summary_keeps_missing_and_unplanned_rows_visible() -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    reports = [_valid_report(item, digest) for item in planned[:1]]
    extra = copy.deepcopy(reports[0])
    extra["attemptId"] = "unplanned:observer:999"
    extra["seed"] = 999
    reports.append(extra)
    reports[0]["manifestDigest"] = "0" * 64

    summary = summarize_preregistered_evidence(manifest, reports)

    assert summary["planned"] == 15
    assert summary["attempted"] == 1
    assert summary["complete"] is False
    assert any("missing_report:" in failure for failure in summary["requirementFailures"])
    assert any(item["reason"] == "unplanned_attempt" for item in summary["unplannedReports"])
    first = summary["runs"][0]
    assert first["status"] == "completed"
    assert "manifest_digest_mismatch" in first["failureReasons"]


def test_preregistered_summary_rejects_started_attempt_as_incomplete() -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    report = _valid_report(planned[0], digest)
    report["attemptStatus"] = "started"
    summary = summarize_preregistered_evidence(manifest, [report])

    assert summary["complete"] is False
    assert any("non_terminal_attempt:" in failure for failure in summary["requirementFailures"])
    assert summary["runs"][0]["terminal"] is False


def test_preregistered_summary_rejects_duplicate_and_unplanned_ledger_rows() -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    first = _valid_report(planned[0], digest)
    first_record = {
        "attemptId": planned[0]["attemptId"],
        "experimentId": manifest["experimentId"],
        "route": planned[0]["route"],
        "seed": planned[0]["seed"],
        "strategyId": planned[0]["strategyId"],
        "status": "completed",
        "manifestDigest": digest,
    }
    extra_record = dict(first_record)
    extra_record["attemptId"] = "unplanned:observer:999"
    extra_record["seed"] = 999

    summary = summarize_preregistered_evidence(
        manifest,
        [first],
        attempt_records=[first_record, dict(first_record), extra_record],
    )

    assert summary["complete"] is False
    assert any("duplicate_attempt_record:" in item for item in summary["requirementFailures"])
    assert any("unplanned_attempt_record:" in item for item in summary["requirementFailures"])


def test_batch_loader_replaces_absolute_source_ids_with_stable_digest_id(tmp_path) -> None:
    path = tmp_path / "batch.json"
    path.write_text(
        json.dumps(
            {
                "sourceId": r"C:\Users\secret\batch.json",
                "mode": "real",
                "backend": "postgres",
                "keepRuns": False,
                "runsRequested": 0,
                "runsCompleted": 0,
                "embeddingPreflight": {"vectorCount": 0},
                "reports": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_batch_reports([path])
    # No reports means there is no per-report object to expose source metadata;
    # the loader's stable ID path is exercised through a minimal report below.
    path.write_text(
        json.dumps(
            {
                "sourceId": r"C:\Users\secret\batch.json",
                "mode": "real",
                "backend": "postgres",
                "keepRuns": False,
                "runsRequested": 1,
                "runsCompleted": 1,
                "embeddingPreflight": {"vectorCount": 1},
                "reports": [{"route": "observer", "seed": 1}],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_batch_reports([path])
    source = loaded[0]["_evidenceSource"]
    assert source["sourceId"].startswith("batch:")
    assert r"C:\Users" not in str(source)
