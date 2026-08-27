from __future__ import annotations

import asyncio
import json

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.clock import WorldTime
from core.backend.app.orchestration.run_service import RunService


class DelayedProtocolModel:
    def __init__(self, delayed_protocol: str) -> None:
        self.delayed_protocol = delayed_protocol
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.protocols: list[str] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        self.protocols.append(protocol)
        if protocol == self.delayed_protocol:
            self.started.set()
            await self.release.wait()
        if protocol == "ChatDecision":
            value = {
                "result": "decided",
                "action": "speak",
                "responseDesire": 3,
                "intent": "在今天结束前收尾",
            }
        elif protocol == "SpeechGeneration":
            value = {"text": "时间不早了，今天先说到这里。"}
        elif protocol == "SegmentSummary":
            value = {"claims": []}
        elif protocol == "ExitConsolidation":
            value = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        else:
            raise AssertionError(f"unexpected model protocol: {protocol}")
        return TextGenerationResult(
            text=json.dumps(value, ensure_ascii=False),
            provider="fake",
            model="delayed-offline",
        )


async def _open_player_chat_at(
    registry,
    *,
    day: int,
    model: DelayedProtocolModel,
) -> tuple[RunService, object, str]:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    run = await service.get_run_entity(created["runId"])
    service.decisions.model = model
    async with run.lock:
        for npc in registry.npcs:
            run.thought_days[npc.actor_id].add(day)
        run.clock.current = WorldTime(day=day, hour=17, minute=59)
    return service, run, opened["conversation"]["conversationId"]


@pytest.mark.anyio
async def test_pre_boundary_speech_may_land_once_then_day_end_closes(registry) -> None:
    model = DelayedProtocolModel("SpeechGeneration")
    service, run, conversation_id = await _open_player_chat_at(
        registry,
        day=1,
        model=model,
    )

    message_task = asyncio.create_task(
        service.player_message(run.run_id, conversation_id, "最后再确认一句。")
    )
    await asyncio.wait_for(model.started.wait(), timeout=2)

    stepped = await asyncio.wait_for(service.world_step(run.run_id, 2), timeout=2)
    assert stepped["worldTime"]["label"] == "Day1 18:00"
    assert run.pending_day_end == (1, "day_end")
    assert run.conversations[conversation_id].is_open

    model.release.set()
    await asyncio.wait_for(message_task, timeout=2)
    await service.wait_for_chat_idle(run.run_id, conversation_id, timeout=2)

    conversation = run.conversations[conversation_id]
    assert conversation.status == "closed"
    assert conversation.close_reason == "day_end"
    npc_lines = [
        message
        for message in run.messages[conversation_id]
        if message["authorActorId"] == "npc_001"
    ]
    assert [line["text"] for line in npc_lines] == ["时间不早了，今天先说到这里。"]
    assert npc_lines[0]["createdAt"] == "Day1 18:00"
    # The player message invalidates the conversation opener decision that
    # was already in flight, then one fresh decision handles the player's
    # actual message. Only the fresh round may publish speech.
    assert model.protocols.count("ChatDecision") == 2
    assert model.protocols.count("SpeechGeneration") == 1
    assert run.active_chat_pipelines == 0
    assert run.pending_day_end is None


@pytest.mark.anyio
async def test_day7_waits_for_inflight_decision_before_consolidation_and_resolution(
    registry,
) -> None:
    model = DelayedProtocolModel("ChatDecision")
    service, run, conversation_id = await _open_player_chat_at(
        registry,
        day=7,
        model=model,
    )

    message_task = asyncio.create_task(
        service.player_message(run.run_id, conversation_id, "截止前还有什么要说的吗？")
    )
    await asyncio.wait_for(model.started.wait(), timeout=2)

    stepped = await asyncio.wait_for(service.world_step(run.run_id, 2), timeout=2)
    assert stepped["worldTime"]["label"] == "Day7 18:00"
    assert run.chapter_resolution is None
    assert run.pending_chapter_event_id == "event_day7_proposal_deadline"
    assert run.conversations[conversation_id].is_open

    model.release.set()
    await asyncio.wait_for(message_task, timeout=2)
    await service.wait_for_chat_idle(run.run_id, conversation_id, timeout=2)

    conversation = run.conversations[conversation_id]
    assert conversation.close_reason == "chapter_deadline"
    assert run.chapter_resolution is not None
    assert "SpeechGeneration" not in model.protocols
    consolidated_seq = max(
        event.event_seq for event in run.events if event.event_type == "npc_consolidated"
    )
    resolved_seq = next(
        event.event_seq for event in run.events if event.event_type == "chapter_resolved"
    )
    assert consolidated_seq < resolved_seq
    assert run.pending_day_end is None
    assert run.pending_chapter_event_id is None
