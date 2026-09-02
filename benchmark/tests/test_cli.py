from __future__ import annotations

import pytest

from benchmark import cli
from benchmark.memory import load_memory_dataset


def test_validate_freezes_all_p0_denominators() -> None:
    result = cli.validate()
    assert result["businessTasks"] == 12
    assert result["memoryQueries"] == 100
    assert result["memoryTuning"] == 30
    assert result["memoryHoldout"] == 70
    assert result["faultFamilies"] == 8
    assert result["faultAttempts"] == 80


def test_memory_dataset_uses_production_actor_goal_and_topic_ids() -> None:
    from benchmark.integrations.postgres_memory import validate_dataset_references
    from core.backend.app.scenario.loader import ScenarioLoader

    dataset = load_memory_dataset()
    actor_ids = {f"npc_{index:03d}" for index in range(1, 6)}
    for document in dataset.corpus:
        assert document.owner_npc_id in actor_ids
        assert all(actor_id in actor_ids for actor_id in document.actor_ids)
        assert all(goal_id.startswith("goal_00") for goal_id in document.goal_ids)
        assert all(topic_id.startswith("topic_") for topic_id in document.topic_ids)
    validate_dataset_references(dataset, ScenarioLoader("core/scenario").load())


def test_live_preflight_fails_closed_without_management_credentials(monkeypatch) -> None:
    for name in (
        "ARK_API_KEY",
        "ARK_MODEL",
        "ARK_AFP_ACCESS_KEY_ID",
        "ARK_AFP_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="ARK_AFP_ACCESS_KEY_ID"):
        cli._live_preflight("business")


def test_manual_afp_preflight_uses_candidate_credentials_only(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "candidate-key")
    monkeypatch.setenv("ARK_MODEL", "candidate-model")
    monkeypatch.delenv("ARK_AFP_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ARK_AFP_SECRET_ACCESS_KEY", raising=False)
    assert cli._live_preflight("business", manual_afp=True) is None


def test_expected_attempt_counts() -> None:
    assert cli._expected_attempts(cli._manifest("business", "pilot", (1, 2, 3), "b")) == 288
    assert cli._expected_attempts(cli._manifest("memory", "pilot", (1,), "m")) == 500
    assert cli._expected_attempts(cli._manifest("reliability", "pilot", tuple(range(10)), "r")) == 80
