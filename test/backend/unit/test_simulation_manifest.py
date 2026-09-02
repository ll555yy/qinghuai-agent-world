from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from core.backend.app.simulation.manifest import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_MANIFEST_SHA256_PATH,
    AttemptLedger,
    ManifestValidationError,
    canonical_manifest_sha256,
    load_manifest,
    planned_attempts,
    validate_manifest,
)
from core.backend.app.simulation.runner import ROUTE_PLAYER_STEPS, player_strategy_steps

RECOVERY_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "core"
    / "backend"
    / "app"
    / "simulation"
    / "manifests"
    / "final_agent_validation_recovery_v2.json"
)
RECOVERY_V3_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "core"
    / "backend"
    / "app"
    / "simulation"
    / "manifests"
    / "final_agent_validation_recovery_v3.json"
)
STRATEGY_V2_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "core"
    / "backend"
    / "app"
    / "simulation"
    / "manifests"
    / "final_agent_validation_strategy_v2_v4.json"
)
STRATEGY_V3_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "core"
    / "backend"
    / "app"
    / "simulation"
    / "manifests"
    / "final_agent_validation_strategy_v3_v5.json"
)


def _canonical_json_digest(value: object) -> str:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_versioned_manifest_has_external_digest_and_full_continuous_matrix() -> None:
    manifest, digest = load_manifest()

    assert manifest["preregistrationBaseCommit"] == "738ef11"
    assert len(planned_attempts(manifest)) == 15
    assert digest == DEFAULT_MANIFEST_SHA256_PATH.read_text(encoding="utf-8").strip()
    assert all(
        strategy["privateInputsUsed"] is False for strategy in manifest["strategies"]
    )
    for route in ("observer", "pro_lin", "pro_zhao"):
        assert manifest["routes"][route]["seeds"] == list(range(20260840, 20260845))
        assert manifest["routes"][route]["plannedRuns"] == 5
    assert manifest["budget"]["maxCostCny"] is None
    assert manifest["pricing"]["currency"] == "CNY"


def test_recovery_manifest_is_distinct_preregistered_full_matrix() -> None:
    manifest, digest = load_manifest(RECOVERY_MANIFEST_PATH)
    original, _ = load_manifest()

    assert manifest["experimentId"] == "final-agent-validation-recovery-20260823"
    assert manifest["preregistrationBaseCommit"] == "235b36b"
    assert len(planned_attempts(manifest)) == 15
    assert digest == "0bc0a42bd71f1c98ea3229bea74db59021dfef028d43438e21cc07a662fcfcfe"
    assert manifest["artifacts"] == original["artifacts"]
    assert manifest["strategies"] == original["strategies"]
    assert {
        item["attemptId"] for item in planned_attempts(manifest)
    }.isdisjoint(item["attemptId"] for item in planned_attempts(original))
    for route in ("observer", "pro_lin", "pro_zhao"):
        assert manifest["routes"][route]["seeds"] == list(range(20260845, 20260850))
        assert manifest["routes"][route]["plannedRuns"] == 5


def test_recovery_v3_manifest_is_distinct_preregistered_full_matrix() -> None:
    manifest, digest = load_manifest(RECOVERY_V3_MANIFEST_PATH)
    original, _ = load_manifest()
    recovery_v2, _ = load_manifest(RECOVERY_MANIFEST_PATH)

    assert manifest["experimentId"] == "final-agent-validation-recovery-v3-20260824"
    assert manifest["preregistrationBaseCommit"] == "9f5cae1"
    assert len(planned_attempts(manifest)) == 15
    assert digest == "3efe93e36137510c8ed6d944a593135fea106e0e72eafed83c3d7a665d6c4f40"
    assert manifest["artifacts"] == original["artifacts"]
    assert manifest["strategies"] == original["strategies"]
    attempt_ids = {item["attemptId"] for item in planned_attempts(manifest)}
    assert attempt_ids.isdisjoint(
        item["attemptId"] for item in planned_attempts(original)
    )
    assert attempt_ids.isdisjoint(
        item["attemptId"] for item in planned_attempts(recovery_v2)
    )
    for route in ("observer", "pro_lin", "pro_zhao"):
        assert manifest["routes"][route]["seeds"] == list(range(20260850, 20260855))
        assert manifest["routes"][route]["plannedRuns"] == 5


def test_strategy_v2_manifest_freezes_new_holdout_without_relaxing_gates() -> None:
    manifest, digest = load_manifest(STRATEGY_V2_MANIFEST_PATH)
    prior_manifests = [
        load_manifest(path)[0]
        for path in (DEFAULT_MANIFEST_PATH, RECOVERY_MANIFEST_PATH, RECOVERY_V3_MANIFEST_PATH)
    ]

    assert manifest["experimentId"] == "final-agent-validation-strategy-v2-20260824"
    assert manifest["preregistrationBaseCommit"] == "c24eafd"
    assert len(planned_attempts(manifest)) == 15
    assert digest == "ebc8d913f2f5f74366ce26f865451b796d1e98e53b07bc7fc076626445a4bbd5"
    attempt_ids = {item["attemptId"] for item in planned_attempts(manifest)}
    for prior in prior_manifests:
        assert attempt_ids.isdisjoint(
            item["attemptId"] for item in planned_attempts(prior)
        )
    assert manifest["routes"]["pro_lin"]["strategyId"] == "strategy.pro_lin.v2"
    assert manifest["acceptance"]["pro_lin"] == {
        "minimumGameplayPasses": 4,
        "minimumPlayerCompletedRuns": 2,
    }
    for route in ("observer", "pro_lin", "pro_zhao"):
        assert manifest["routes"][route]["seeds"] == list(range(20260855, 20260860))
        assert manifest["routes"][route]["plannedRuns"] == 5

    selected = {
        route: [asdict(step) for step in player_strategy_steps(route, config["strategyId"])]
        for route, config in manifest["routes"].items()
    }
    assert manifest["artifacts"]["promptPolicy"]["sha256"] == _canonical_json_digest(
        selected
    )
    pro_lin_strategy = next(
        item for item in manifest["strategies"] if item["strategyId"] == "strategy.pro_lin.v2"
    )
    assert pro_lin_strategy["sha256"] == _canonical_json_digest(
        [asdict(step) for step in player_strategy_steps("pro_lin", "strategy.pro_lin.v2")]
    )


def test_strategy_v3_manifest_freezes_split_state_holdout_and_resume_base() -> None:
    manifest, digest = load_manifest(STRATEGY_V3_MANIFEST_PATH)
    prior_manifests = [
        load_manifest(path)[0]
        for path in (
            DEFAULT_MANIFEST_PATH,
            RECOVERY_MANIFEST_PATH,
            RECOVERY_V3_MANIFEST_PATH,
            STRATEGY_V2_MANIFEST_PATH,
        )
    ]

    assert manifest["experimentId"] == "final-agent-validation-strategy-v3-20260824"
    assert manifest["preregistrationBaseCommit"] == "e9a4a52"
    assert len(planned_attempts(manifest)) == 15
    assert digest == "97053b7a53b3c2d1803d8f090e29475bab13f5cad52c94decb8a0e2628a80aa1"
    attempt_ids = {item["attemptId"] for item in planned_attempts(manifest)}
    for prior in prior_manifests:
        assert attempt_ids.isdisjoint(
            item["attemptId"] for item in planned_attempts(prior)
        )
    assert manifest["routes"]["pro_lin"]["strategyId"] == "strategy.pro_lin.v3"
    assert manifest["acceptance"]["pro_lin"] == {
        "minimumGameplayPasses": 4,
        "minimumPlayerCompletedRuns": 2,
    }
    for route in ("observer", "pro_lin", "pro_zhao"):
        assert manifest["routes"][route]["seeds"] == list(range(20260860, 20260865))
        assert manifest["routes"][route]["plannedRuns"] == 5

    selected = {
        route: [asdict(step) for step in player_strategy_steps(route, config["strategyId"])]
        for route, config in manifest["routes"].items()
    }
    assert manifest["artifacts"]["promptPolicy"]["sha256"] == _canonical_json_digest(
        selected
    )
    pro_lin_strategy = next(
        item for item in manifest["strategies"] if item["strategyId"] == "strategy.pro_lin.v3"
    )
    assert pro_lin_strategy["sha256"] == _canonical_json_digest(selected["pro_lin"])
    assert all(item["privateInputsUsed"] is False for item in manifest["strategies"])


def test_manifest_artifact_and_strategy_digests_match_frozen_sources() -> None:
    manifest, _ = load_manifest()
    root = Path(__file__).resolve().parents[3]
    route_payload = {
        route: [asdict(step) for step in steps]
        for route, steps in ROUTE_PLAYER_STEPS.items()
    }

    assert manifest["artifacts"]["promptPolicy"]["sha256"] == _canonical_json_digest(
        route_payload
    )
    # A historical experiment manifest identifies the scenario snapshot used
    # for that run. The live scenario is allowed to evolve afterwards, so
    # comparing this value with today's ``core/scenario`` would make an old,
    # immutable experiment fail whenever world content changes.
    scenario_digest = manifest["artifacts"]["scenario"]["sha256"]
    assert scenario_digest == "b21ecca9b5283be198bd6875a175b86e33ed6e981712e5e253680fdbbb0e7e81"
    assert len(scenario_digest) == 64
    assert set(scenario_digest) <= set("0123456789abcdef")
    assert manifest["artifacts"]["case"]["sha256"] == hashlib.sha256(
        (root / "core" / "evaluation" / "agent_semantic_cases.yaml").read_bytes()
    ).hexdigest()
    strategy_by_id = {
        item["strategyId"]: item for item in manifest["strategies"]
    }
    for route, steps in ROUTE_PLAYER_STEPS.items():
        assert strategy_by_id[f"strategy.{route}.v1"]["sha256"] == (
            _canonical_json_digest([asdict(step) for step in steps])
        )


def test_manifest_digest_is_canonical_and_tampering_is_rejected(tmp_path) -> None:
    manifest = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    original_digest = canonical_manifest_sha256(manifest)
    manifest["routes"]["observer"]["seeds"][0] += 100
    with pytest.raises(ManifestValidationError, match="continuous seeds"):
        validate_manifest(manifest)

    manifest = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    sidecar = tmp_path / "manifest.sha256"
    sidecar.write_text(original_digest + "\n", encoding="utf-8")
    loaded, digest = load_manifest(path, external_sha256_path=sidecar)
    assert loaded["experimentId"] == manifest["experimentId"]
    assert digest == original_digest

    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="digest mismatch"):
        load_manifest(path, external_sha256_path=sidecar)


def test_manifest_rejects_private_strategy_and_duplicate_seed_plan() -> None:
    manifest, _ = load_manifest()
    private = copy.deepcopy(manifest)
    private["strategies"][0]["privateInputsUsed"] = True
    with pytest.raises(ManifestValidationError, match="privateInputsUsed=false"):
        validate_manifest(private)

    duplicate = copy.deepcopy(manifest)
    duplicate["plannedRuns"][1]["seed"] = duplicate["plannedRuns"][0]["seed"]
    with pytest.raises(ManifestValidationError, match="plannedRuns"):
        validate_manifest(duplicate)


def test_attempt_ledger_atomically_records_started_and_terminal_rows(tmp_path) -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    ledger = AttemptLedger(
        tmp_path / "attempts",
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=planned,
    )
    ledger.prepare()
    assert len(ledger.records()) == 15
    first = planned[0]
    assert ledger.get(first["attemptId"])["status"] == "not_started"
    assert ledger.start(first)["status"] == "started"
    attached = ledger.attach_run(first, "run_test_001")
    assert attached["runId"] == "run_test_001"
    assert ledger.attach_run(first, "run_test_001")["runId"] == "run_test_001"
    with pytest.raises(RuntimeError, match="already bound"):
        ledger.attach_run(first, "run_test_002")
    terminal = ledger.finish(
        first,
        "runner_failed",
        run_id="run_test_001",
        reason="create_run_failed",
        infra_valid=False,
    )
    assert terminal["status"] == "runner_failed"
    assert terminal["terminalAt"]
    assert ledger.get(first["attemptId"])["manifestDigest"] == digest
    with pytest.raises(RuntimeError, match="not started"):
        ledger.attach_run(first, "run_test_001")


def test_attempt_ledger_cannot_overwrite_terminal_state(tmp_path) -> None:
    manifest, digest = load_manifest()
    first = planned_attempts(manifest)[0]
    ledger = AttemptLedger(
        tmp_path,
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=[first],
    )
    ledger.start(first)
    ledger.finish(first, "completed", infra_valid=True)
    with pytest.raises(RuntimeError, match="already terminal"):
        ledger.finish(first, "provider_failed", infra_valid=False)
