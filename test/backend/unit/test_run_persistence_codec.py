from __future__ import annotations

import pytest

from core.backend.app.domain.clock import WorldClock, WorldTime
from core.backend.app.domain.conversation import Conversation
from core.backend.app.domain.run import CommandRecord, Run
from core.backend.app.persistence.codec import deserialize_run, serialize_run
from core.backend.app.persistence.in_memory import InMemoryRunRepository
from core.backend.app.persistence.run_repository import RepositoryConflictError


def _run() -> Run:
    run = Run(
        run_id="run_codec",
        player_agenda_id="agenda_001",
        clock=WorldClock(current=WorldTime(day=2, hour=10, minute=5)),
        seed=17,
        state_version=1,
        event_seq=1,
        actor_states={"npc_001": {"status": "chatting"}},
    )
    run.positions = {"npc_001": {"x": 4, "y": 5}}
    run.daily_think_minutes = {"npc_001": 600}
    run.daily_think_order = ["npc_001"]
    run.daily_think_schedule = {2: {"npc_001": 600}}
    run.thought_days = {"npc_001": {1, 2}}
    run.goals = {"goal_1": {"goalId": "goal_1", "ownerNpcId": "npc_001", "status": "active"}}
    run.relationships = {
        ("npc_001", "player_001"): {
            "fromActorId": "npc_001",
            "toActorId": "player_001",
            "trust": 1,
        }
    }
    run.memories = {"memory_1": {"memoryId": "memory_1", "ownerNpcId": "npc_001"}}
    run.memory_links = [{"memoryId": "memory_1", "targetId": "goal_1"}]
    run.memory_cache = {("conv_1", "npc_001"): {"memory_1"}}
    run.conversations["conv_1"] = Conversation("conv_1", 1, ["npc_001", "player_001"])
    run.conversations["conv_1"].remove_participant("npc_001")
    run.messages = {"conv_1": [{"messageId": "msg_1", "text": "hello"}]}
    run.segments = {"conv_1": [{"segmentId": "seg_1", "participants": ["npc_001"]}]}
    run.conversation_drafts = {"conv_1": {"npc_001": {"goalUpdates": {}}}}
    run.consolidation_status = {("conv_1", "npc_001"): {"status": "succeeded"}}
    run.chapter_agenda_stances = {("agenda_001", "npc_001"): "support"}
    run.fired_event_ids = {"event_1"}
    run.closed_days = {1}
    run.pending_day_end = (2, "day_end")
    run.pending_chapter_event_id = "event_deadline"
    run.command_records = {"cmd-1": CommandRecord("fp", {"runId": "run_codec"})}
    run.append_event("message_created", {"conversationId": "conv_1"})
    run.active_chat_pipelines = 2
    run.in_flight_speech_calls = 1
    return run


def test_run_codec_round_trips_durable_state_without_runtime_locks() -> None:
    original = _run()
    encoded = serialize_run(original)

    assert "lock" not in encoded
    assert "chat_pipeline_lock" not in encoded
    assert "active_chat_pipelines" not in encoded
    assert "in_flight_speech_calls" not in encoded

    restored = deserialize_run(encoded)
    assert restored.run_id == original.run_id
    assert restored.clock.as_dict() == original.clock.as_dict()
    assert restored.relationships == original.relationships
    assert restored.memory_cache == original.memory_cache
    assert restored.chapter_agenda_stances == original.chapter_agenda_stances
    assert restored.pending_day_end == original.pending_day_end
    assert restored.pending_chapter_event_id == original.pending_chapter_event_id
    assert restored.command_records["cmd-1"].fingerprint == "fp"
    assert restored.events[0].event_type == "message_created"
    assert restored.active_chat_pipelines == 0
    assert restored.in_flight_speech_calls == 0
    assert restored.conversations["conv_1"].status == "closed"
    assert restored.conversations["conv_1"].participants == ["player_001"]
    assert restored.conversations["conv_1"].participant_history() == frozenset(
        {"npc_001", "player_001"}
    )


@pytest.mark.anyio
async def test_in_memory_repository_keeps_identity_and_checks_revision() -> None:
    repository = InMemoryRunRepository()
    run = _run()
    await repository.add(run)
    assert await repository.get(run.run_id) is run
    assert await repository.revision(run.run_id) == 0

    revision = await repository.save(run, expected_revision=0)
    assert revision == 1
    assert await repository.revision(run.run_id) == 1
    with pytest.raises(RepositoryConflictError):
        await repository.save(run, expected_revision=0)
    events = await repository.events_after(run.run_id, 0)
    assert [event.event_seq for event in events] == [event.event_seq for event in run.events]
    assert await repository.healthcheck() is True
    await repository.close()


def test_codec_does_not_share_mutable_payloads() -> None:
    original = _run()
    restored = deserialize_run(serialize_run(original))
    restored.goals["goal_1"]["status"] = "achieved"
    assert original.goals["goal_1"]["status"] == "active"
