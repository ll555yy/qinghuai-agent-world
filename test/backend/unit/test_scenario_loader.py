from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from core.backend.app.scenario.loader import ScenarioLoader, ScenarioValidationError


def test_eight_real_yaml_files_load(registry) -> None:
    assert len(registry.npcs) == 5
    assert len(registry.actors) == 6
    assert len(registry.goals) == 10
    assert len(registry.topics) == 8
    assert len(registry.agendas) == 5
    assert len(registry.events) == 7
    assert len(registry.npc_personas) == 5
    assert registry.npc_personas["npc_001"].core_secrets
    registry.validate_copy_isolated()


def test_registry_mapping_is_immutable(registry) -> None:
    with pytest.raises(TypeError):
        registry.actors["new"] = registry.player  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        registry.actors["npc_001"].name = "changed"  # type: ignore[misc]


def test_invalid_cross_reference_has_file_and_field(tmp_path: Path) -> None:
    from shutil import copyfile

    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    for filename in (
        "NPC_PERSONAS.yaml",
        "PLAYER_PROFILE.yaml",
        "INITIAL_TOPICS.yaml",
        "INITIAL_GOALS.yaml",
        "INITIAL_RELATIONSHIPS.yaml",
        "INITIAL_MEMORIES.yaml",
        "WORLD_EVENTS_DAY1_7.yaml",
        "CHAPTER_AGENDAS.yaml",
    ):
        copyfile(source / filename, tmp_path / filename)
    goals_path = tmp_path / "INITIAL_GOALS.yaml"
    goals_path.write_text(
        goals_path.read_text(encoding="utf-8").replace("topic_bookstore_survival", "topic_missing", 1),
        encoding="utf-8",
    )
    with pytest.raises(ScenarioValidationError, match=r"INITIAL_GOALS\.yaml\.goals\[0\]\.topicIds\[0\]"):
        ScenarioLoader(tmp_path).load()
