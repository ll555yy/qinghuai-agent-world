from __future__ import annotations

import json

import pytest

from benchmark.common.ark_budget import AFPBudgetGuard, AFPUsage, BudgetExhausted
from benchmark.common.artifacts import ArtifactStore, sha256_file
from benchmark.common.models import BudgetPolicy, ExperimentManifest, TelemetryRecord
from benchmark.common.statistics import (
    cohen_kappa,
    paired_bootstrap,
    percentile,
    recommended_paired_seeds,
)


def manifest(experiment_id: str = "exp-001") -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id=experiment_id,
        suite="business",
        execution_mode="offline",
        seeds=(1, 2, 3),
        scenario_digest="a" * 64,
        prompt_digest="b" * 64,
    )


def test_manifest_rejects_duplicate_seeds() -> None:
    with pytest.raises(ValueError, match="unique"):
        ExperimentManifest(
            experiment_id="bad",
            suite="business",
            execution_mode="offline",
            seeds=(1, 1),
            scenario_digest="a",
            prompt_digest="b",
        )


def test_telemetry_separates_logical_and_physical_calls() -> None:
    telemetry = TelemetryRecord(
        candidate_calls=2,
        candidate_physical_requests=3,
        candidate_retries=1,
        prompt_tokens=10,
        completion_tokens=4,
        embedding_tokens=2,
        step_latencies_ms=[10, 20, 30],
    )
    value = telemetry.as_dict()
    assert value["totalTokens"] == 16
    assert value["candidate_calls"] == 2
    assert value["candidate_physical_requests"] == 3
    assert value["stepLatencyP50Ms"] == 20


def test_statistics_are_deterministic() -> None:
    first = paired_bootstrap([1, 1, 0], [0, 0, 0], samples=1000, seed=7)
    second = paired_bootstrap([1, 1, 0], [0, 0, 0], samples=1000, seed=7)
    assert first == second
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert cohen_kappa(["yes", "no"], ["yes", "no"]) == 1.0
    assert 10 <= recommended_paired_seeds([0.0, 1.0, -1.0]) <= 30


def test_artifact_manifest_is_immutable_and_hashed(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "exp-001")
    directory = store.initialize(manifest())
    assert sha256_file(directory / "manifest.json") == (directory / "manifest.sha256").read_text().strip()
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert payload["experiment_id"] == "exp-001"
    with pytest.raises(ValueError, match="immutable"):
        store.initialize(manifest("exp-001").__class__(
            experiment_id="exp-001",
            suite="business",
            execution_mode="offline",
            seeds=(9,),
            scenario_digest="a" * 64,
            prompt_digest="b" * 64,
        ))


def test_budget_guard_preserves_reserve() -> None:
    readings = iter(
        [
            AFPUsage(quota=2000, used=0, reset_time_ms=1),
            AFPUsage(quota=2000, used=1899, reset_time_ms=1),
            AFPUsage(quota=2000, used=1900, reset_time_ms=1),
        ]
    )
    guard = AFPBudgetGuard(BudgetPolicy(), lambda: next(readings))
    guard.start()
    guard.check()
    with pytest.raises(BudgetExhausted):
        guard.check()
