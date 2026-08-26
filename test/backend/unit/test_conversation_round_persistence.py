from __future__ import annotations

import asyncio

from core.backend.app.domain.clock import WorldClock, WorldTime
from core.backend.app.domain.conversation import Conversation
from core.backend.app.domain.run import Run, new_conversation_round_state
from core.backend.app.persistence.codec import deserialize_run, serialize_run
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository


def _run() -> Run:
    run = Run(
        run_id="run_round_codec",
        player_agenda_id=None,
        clock=WorldClock(current=WorldTime(day=1, hour=10, minute=30)),
    )
    run.conversations["conv_1"] = Conversation(
        "conv_1", 1, ["npc_001", "player_001"]
    )
    run.conversation_round_states["conv_1"] = {
        "roundId": 7,
        "status": "publishing",
        "roundVersion": 4,
        "triggerMessageIds": ["msg_1"],
        "queuedMessageIds": ["msg_2", "msg_3"],
        "segmentId": "seg_2",
        "participantVersion": 3,
        "cooldownDueAt": None,
        "finalCheckUsed": False,
        "pendingPublications": [
            {"publicationId": "pub_1", "messageId": "msg_npc_1", "order": 1}
        ],
        "recovery": {
            "resumeStatus": "publishing",
            "attempt": 1,
            "publishedMessageIds": ["msg_npc_0"],
        },
    }
    return run


def test_round_state_codec_round_trip_and_runtime_registries_are_excluded() -> None:
    run = _run()
    run.conversation_round_locks["conv_1"] = asyncio.Lock()

    encoded = serialize_run(run)

    assert encoded["conversationRoundStates"] == run.conversation_round_states
    assert "conversationRoundLocks" not in encoded
    assert "conversationRoundTasks" not in encoded
    restored = deserialize_run(encoded)
    assert restored.conversation_round_states == run.conversation_round_states
    assert set(restored.conversation_round_locks) == {"conv_1"}
    assert restored.conversation_round_locks["conv_1"] is not run.conversation_round_locks[
        "conv_1"
    ]
    assert restored.conversation_round_tasks == {}
    assert restored.conversation_round_lock("conv_1") is restored.conversation_round_lock(
        "conv_1"
    )


def test_legacy_snapshot_defaults_round_state_lazily() -> None:
    run = _run()
    legacy = serialize_run(run)
    legacy.pop("conversationRoundStates")

    restored = deserialize_run(legacy)
    assert restored.conversation_round_states == {}
    state = restored.ensure_conversation_round_state("conv_1")
    assert state == new_conversation_round_state()
    assert state["status"] == "idle"
    assert state["triggerMessageIds"] == []
    assert state["queuedMessageIds"] == []
    assert state["pendingPublications"] == []


def test_sql_repository_projects_each_round_state_as_one_state_item() -> None:
    repository = SQLAlchemyRunRepository.__new__(SQLAlchemyRunRepository)
    run = _run()

    rows = repository._state_rows(run)
    round_rows = [row for row in rows if row["section"] == "conversationRoundStates"]
    assert len(round_rows) == 1
    assert round_rows[0]["run_id"] == run.run_id
    assert round_rows[0]["item_key"] == "conv_1"
    assert round_rows[0]["value"] == run.conversation_round_states["conv_1"]

    state: dict[str, object] = {}
    repository._apply_state_item(
        state,
        "conversationRoundStates",
        "conv_1",
        run.conversation_round_states["conv_1"],
    )
    assert state["conversationRoundStates"] == run.conversation_round_states
