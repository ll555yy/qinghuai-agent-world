from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.backend.app.scenario.loader import (
    SCENARIO_FILES,
    ScenarioLoader,
    ScenarioValidationError,
)


def test_real_yaml_files_load(registry) -> None:
    assert len(registry.npcs) == 5
    assert len(registry.actors) == 6
    assert len(registry.goals) == 10
    assert len(registry.topics) == 8
    assert len(registry.agendas) == 5
    assert len(registry.events) == 7
    assert len(registry.npc_personas) == 5
    assert len(registry.relationships) == 25
    assert len(registry.speech_examples) == 44
    assert registry.virtual_hours_per_real_minute == 0.5
    assert registry.real_seconds_per_virtual_minute == 2
    covered_relationships = {
        (memory.owner_npc_id, target_actor_id)
        for memory in registry.memories.values()
        if memory.source == "scenario_seed"
        for target_actor_id in memory.actor_ids
    }
    assert set(registry.relationships) <= covered_relationships
    assert registry.npc_personas["npc_001"].core_secrets
    registry.validate_copy_isolated()


def test_registry_mapping_is_immutable(registry) -> None:
    with pytest.raises(TypeError):
        registry.actors["new"] = registry.player  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        registry.actors["npc_001"].name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        registry.speech_examples["new"] = registry.speech_examples[
            "npc001_greet_01"
        ]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        registry.speech_examples["npc001_greet_01"].reply = "changed"  # type: ignore[misc]


def test_invalid_cross_reference_has_file_and_field(tmp_path: Path) -> None:
    from shutil import copyfile

    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    for filename in SCENARIO_FILES:
        copyfile(source / filename, tmp_path / filename)
    goals_path = tmp_path / "INITIAL_GOALS.yaml"
    goals_path.write_text(
        goals_path.read_text(encoding="utf-8").replace("topic_bookstore_survival", "topic_missing", 1),
        encoding="utf-8",
    )
    with pytest.raises(ScenarioValidationError, match=r"INITIAL_GOALS\.yaml\.goals\[0\]\.topicIds\[0\]"):
        ScenarioLoader(tmp_path).load()


def test_speech_examples_include_extra_conversational_examples_for_lin_huilan(
    registry,
) -> None:
    assert {
        npc.actor_id: sum(
            example.npc_id == npc.actor_id
            for example in registry.speech_examples.values()
        )
        for npc in registry.npcs
    } == {
        "npc_001": 12,
        "npc_002": 8,
        "npc_003": 8,
        "npc_004": 8,
        "npc_005": 8,
    }
    example = registry.speech_examples["npc001_refuse_mediate_01"]
    assert example.npc_id == "npc_001"
    assert "代为" in example.situation
    assert "拒绝" in example.intended_move
    assert registry.speech_examples["npc001_join_simple_01"].reply == (
        "既然都说明白了，我也出一份力。日子定了，知会我一声。"
    )
    assert registry.speech_examples["npc001_narrow_topic_01"].reply == (
        "饭要一口口吃，事也得一件件办。先把眼前这件做稳吧。"
    )
    assert registry.speech_examples["npc001_return_choice_01"].reply == (
        "这事，还是听慎之自己怎么说吧。咱们不好替他拿主意。"
    )
    assert registry.speech_examples["npc001_pause_plan_01"].reply == (
        "这事我还得想想。最要紧的那一处，你先说清楚，好吗？"
    )


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("npc001_refuse_mediate_01", "npc001_greet_01", "duplicate example ID"),
        ("npcId: npc_001", "npcId: npc_missing", "existing NPC"),
        ('situation: "别人请求她代为向第三方说情"', 'situation: ""', "non-empty string"),
    ],
)
def test_invalid_speech_example_reports_file_and_field(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    from shutil import copyfile

    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    for filename in SCENARIO_FILES:
        copyfile(source / filename, tmp_path / filename)
    path = tmp_path / "NPC_SPEECH_EXAMPLES.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    with pytest.raises(
        ScenarioValidationError,
        match=rf"NPC_SPEECH_EXAMPLES\.yaml\.examples\[\d+\].*{match}",
    ):
        ScenarioLoader(tmp_path).load()


def test_each_npc_requires_eight_speech_examples(tmp_path: Path) -> None:
    from shutil import copyfile

    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    for filename in SCENARIO_FILES:
        copyfile(source / filename, tmp_path / filename)
    path = tmp_path / "NPC_SPEECH_EXAMPLES.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("  - exampleId: npc001_probe_commitment_01")
    end = text.index("  - exampleId: npc002_greet_01")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(
        ScenarioValidationError, match="npc_001 requires at least 8 examples"
    ):
        ScenarioLoader(tmp_path).load()
