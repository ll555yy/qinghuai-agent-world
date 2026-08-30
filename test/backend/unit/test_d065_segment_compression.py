from __future__ import annotations

import asyncio
import json

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.clock import WorldTime
from core.backend.app.orchestration.run_service import (
    SEGMENT_BOUNDARY_CARRYOVER_MESSAGES,
    SEGMENT_SUMMARY_RECENT_MESSAGES,
    RunService,
)
from core.backend.app.persistence.codec import deserialize_run, serialize_run


class WaitAndSummaryModel:
    """Small deterministic provider for lifecycle and summary tests."""

    def __init__(self, *, speak_on_chat_call: int | None = None) -> None:
        self.chat_calls = 0
        self.speak_on_chat_call = speak_on_chat_call
        self.segment_prompts: list[dict] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        if protocol == "ChatDecision":
            self.chat_calls += 1
            if self.speak_on_chat_call == self.chat_calls:
                value = {
                    "result": "decided",
                    "action": "speak",
                    "responseDesire": 1,
                    "intent": "回应一轮内部空闲调度",
                }
            else:
                value = {"result": "decided", "action": "wait"}
        elif protocol == "SpeechGeneration":
            value = {"text": "第二轮仍有人愿意回应。"}
        elif protocol == "SegmentSummary":
            self.segment_prompts.append(context)
            value = {
                "claims": ["滚动摘要已生成"],
                "actorIds": context.get("participants", []),
            }
        else:
            value = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        return TextGenerationResult(
            text=json.dumps(value, ensure_ascii=False),
            provider="test",
            model="offline",
        )


class AlwaysSpeakModel(WaitAndSummaryModel):
    """Keep choosing meaningful speech so cadence tests can cross bursts."""

    def __init__(self) -> None:
        super().__init__()
        self.speech_calls = 0

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        if protocol == "ChatDecision":
            self.chat_calls += 1
            value = {
                "result": "decided",
                "action": "speak",
                "responseDesire": 1,
                "intent": "继续推进尚未说完的话题",
            }
        elif protocol == "SpeechGeneration":
            self.speech_calls += 1
            value = {"text": f"继续交谈第{self.speech_calls}句。"}
        else:
            return await super().generate(request)
        return TextGenerationResult(
            text=json.dumps(value, ensure_ascii=False),
            provider="test",
            model="offline",
        )


async def _open(service: RunService, participants: list[str]):
    created = await service.create_run()
    opened = await service.create_conversation(created["runId"], participants)
    run = await service.get_run_entity(created["runId"])
    return run, run.conversations[opened["conversation"]["conversationId"]]


async def _write_messages(service: RunService, run, conversation, count: int) -> None:
    async with run.lock:
        for index in range(count):
            service._write_message_locked(
                run,
                conversation,
                "npc_001" if index % 2 == 0 else "npc_002",
                f"原文第{index + 1}条",
            )


@pytest.mark.anyio
async def test_pure_npc_silence_gets_one_final_check_then_closes(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model, chat_cooldown_seconds=0.01)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    await service.wait_for_chat_idle(
        run.run_id,
        conversation.conversation_id,
        include_cooldown=True,
    )

    assert not conversation.is_open
    idle_events = [event for event in run.events if event.event_type == "conversation_idle"]
    assert [event.payload["idleCount"] for event in idle_events] == [1]
    assert run.active_chat_pipelines == 0
    await service.close()


@pytest.mark.anyio
async def test_final_check_speech_starts_a_new_message_round(registry) -> None:
    model = WaitAndSummaryModel(speak_on_chat_call=2)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=0.03)
    run, conversation = await _open(service, [registry.player_actor_id, "npc_001"])
    await service.player_message(run.run_id, conversation.conversation_id, "还有一轮机会。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    await asyncio.sleep(0.05)
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert conversation.is_open
    assert run.idle_counts[conversation.conversation_id] == 1
    assert any(
        item["authorActorId"] == "npc_001"
        and item["text"] == "第二轮仍有人愿意回应。"
        for item in run.messages[conversation.conversation_id]
    )
    await service.close()


@pytest.mark.anyio
async def test_player_chat_uses_server_cooldown_and_only_one_final_check(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model, chat_cooldown_seconds=0.01)
    run, conversation = await _open(service, [registry.player_actor_id, "npc_001"])
    await service.player_message(run.run_id, conversation.conversation_id, "玩家还在聊天。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert conversation.is_open
    assert len([event for event in run.events if event.event_type == "conversation_idle"]) == 1

    await service.conversation_idle(run.run_id, conversation.conversation_id)
    await service.conversation_idle(run.run_id, conversation.conversation_id)
    await service.wait_for_chat_idle(
        run.run_id,
        conversation.conversation_id,
        include_cooldown=True,
    )
    assert not conversation.is_open
    assert len([event for event in run.events if event.event_type == "conversation_idle"]) == 1
    await service.close()


@pytest.mark.anyio
async def test_world_clock_does_not_replay_a_quiet_chat(registry) -> None:
    model = WaitAndSummaryModel(speak_on_chat_call=2)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    run, conversation = await _open(service, [registry.player_actor_id, "npc_001"])
    run.clock.current = WorldTime(day=1, hour=16, minute=0)
    for days in run.thought_days.values():
        days.add(1)

    await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "我先听听你怎么想。",
    )
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert len(run.messages[conversation.conversation_id]) == 1
    calls_before_step = model.chat_calls
    await service.world_step(run.run_id, 4)

    assert conversation.is_open
    assert len(run.messages[conversation.conversation_id]) == 1
    assert model.chat_calls == calls_before_step
    await service.close()


@pytest.mark.anyio
async def test_message_rounds_continue_without_world_clock_polling(registry) -> None:
    model = AlwaysSpeakModel()
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    run.clock.current = WorldTime(day=1, hour=16, minute=0)
    for days in run.thought_days.values():
        days.add(1)

    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert len(run.messages[conversation.conversation_id]) > 3
    count_before_step = len(run.messages[conversation.conversation_id])
    await service.world_step(run.run_id, 4)

    assert conversation.is_open
    assert len(run.messages[conversation.conversation_id]) == count_before_step
    await service.close()


@pytest.mark.anyio
async def test_18_boundary_closes_without_starting_an_idle_round(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    async with run.lock:
        run.clock.current = WorldTime(day=1, hour=17, minute=59)
        message = service._write_message_locked(run, conversation, "npc_001", "临近收束。")

    await service.world_step(run.run_id, 2)

    assert conversation.close_reason == "day_end"
    assert not any(event.event_type == "conversation_idle" for event in run.events)
    # A late continuation is a no-op at 18:00 and cannot create another idle
    # round after the unified day-end path has run.
    async with run.lock:
        await service._run_chat_pipeline_locked(run, conversation, message["messageId"])
    assert not any(event.event_type == "conversation_idle" for event in run.events)


@pytest.mark.anyio
async def test_closed_player_segment_records_public_experience(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    run, conversation = await _open(
        service,
        [registry.player_actor_id, "npc_002"],
    )

    async with run.lock:
        player_message = service._write_message_locked(
            run,
            conversation,
            registry.player_actor_id,
            "你对书店的未来有什么看法？",
        )
        npc_message = service._write_message_locked(
            run,
            conversation,
            "npc_002",
            "我想把青槐巷和旧书店的故事画下来。",
        )
        await service._close_conversation_locked(run, conversation, "test_closed")
        snapshot = run.to_public_snapshot(registry)

    assert snapshot["conversations"][0]["participantHistory"] == [
        "npc_002",
        registry.player_actor_id,
    ]
    assert snapshot["conversationExperiences"] == [
        {
            "experienceId": "experience_seg_000001",
            "conversationId": conversation.conversation_id,
            "segmentId": "seg_000001",
            "participantActorIds": [registry.player_actor_id, "npc_002"],
            "worldDay": 1,
            "at": "09:00",
            "summary": "滚动摘要已生成。",
            "evidenceMessageIds": [
                player_message["messageId"],
                npc_message["messageId"],
            ],
        }
    ]
    recorded = [
        event
        for event in run.events
        if event.event_type == "conversation_experience_recorded"
    ]
    assert len(recorded) == 1
    assert recorded[0].payload["experience"] == snapshot["conversationExperiences"][0]
    await service.close()


@pytest.mark.anyio
async def test_rolling_summary_keeps_eight_raw_messages_and_advances_cursor(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    await _write_messages(service, run, conversation, 21)

    async with run.lock:
        await service._maybe_roll_segment_summary_locked(run, conversation)
        segment = run.segments[conversation.conversation_id][-1]
        context = service._chat_context(run, conversation, "npc_001")

    assert len(run.messages[conversation.conversation_id]) == 21
    assert segment["summaryThroughMessageId"] == "msg_000013"
    assert len(context["messages"]) == SEGMENT_SUMMARY_RECENT_MESSAGES
    assert context["messages"][0]["messageId"] == "msg_000014"
    assert context["segmentSummaries"][0]["summary"]["claims"] == ["滚动摘要已生成"]
    assert model.segment_prompts[0]["mode"] == "rolling"
    assert [item["messageId"] for item in model.segment_prompts[0]["messages"]] == [
        f"msg_{index:06d}" for index in range(1, 14)
    ]


@pytest.mark.anyio
async def test_token_budget_can_roll_before_message_count_threshold(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(
        registry,
        text_model=model,
        segment_summary_trigger_tokens=300,
    )
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    async with run.lock:
        for index in range(9):
            service._write_message_locked(
                run,
                conversation,
                "npc_001" if index % 2 == 0 else "npc_002",
                "青槐巷" * 12,
            )
        await service._maybe_roll_segment_summary_locked(run, conversation)
        segment = run.segments[conversation.conversation_id][-1]
        context = service._chat_context(run, conversation, "npc_001")

    assert segment["summaryThroughMessageId"] == "msg_000001"
    assert len(context["messages"]) == SEGMENT_SUMMARY_RECENT_MESSAGES
    assert len(model.segment_prompts) == 1
    assert model.segment_prompts[0]["mode"] == "rolling"


@pytest.mark.anyio
async def test_final_summary_is_incremental_and_failure_does_not_advance_cursor(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    await _write_messages(service, run, conversation, 21)
    async with run.lock:
        await service._maybe_roll_segment_summary_locked(run, conversation)
        service._write_message_locked(run, conversation, "npc_001", "最后补充的一条。")
        await service._close_current_segment_locked(run, conversation)
        segment = run.segments[conversation.conversation_id][-1]

    assert segment["summaryThroughMessageId"] == "msg_000022"
    assert model.segment_prompts[-1]["mode"] == "final"
    assert [item["messageId"] for item in model.segment_prompts[-1]["messages"]] == [
        f"msg_{index:06d}" for index in range(14, 23)
    ]

    failed_service = RunService(registry, text_model=None)
    failed_run, failed_conversation = await _open(
        failed_service,
        ["npc_001", "npc_002"],
    )
    await _write_messages(failed_service, failed_run, failed_conversation, 21)
    async with failed_run.lock:
        await failed_service._maybe_roll_segment_summary_locked(
            failed_run,
            failed_conversation,
        )
        failed_segment = failed_run.segments[failed_conversation.conversation_id][-1]
        failed_context = failed_service._chat_context(
            failed_run,
            failed_conversation,
            "npc_001",
        )

    assert failed_segment["summaryThroughMessageId"] is None
    assert failed_segment["summary"] is None
    assert len(failed_run.messages[failed_conversation.conversation_id]) == 21
    assert len(failed_context["messages"]) == 21


@pytest.mark.anyio
async def test_joiner_cannot_read_previous_segment_summary(registry) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    await _write_messages(service, run, conversation, 21)
    async with run.lock:
        await service._maybe_roll_segment_summary_locked(run, conversation)
        await service._join_conversation_locked(run, conversation, "npc_003")
        continuing_context = service._chat_context(run, conversation, "npc_001")
        joiner_context = service._chat_context(run, conversation, "npc_003")

    assert service._visible_messages(run, conversation, "npc_003") == []
    assert [
        item["messageId"] for item in continuing_context["boundaryMessages"]
    ] == [
        f"msg_{index:06d}"
        for index in range(
            22 - SEGMENT_BOUNDARY_CARRYOVER_MESSAGES,
            22,
        )
    ]
    assert joiner_context["messages"] == []
    assert joiner_context["boundaryMessages"] == []
    assert joiner_context["segmentSummaries"] == []
    assert len(run.messages[conversation.conversation_id]) == 21


@pytest.mark.anyio
async def test_short_segment_join_summarizes_and_carries_tail_for_old_members_only(
    registry,
) -> None:
    model = WaitAndSummaryModel()
    service = RunService(registry, text_model=model)
    run, conversation = await _open(service, ["npc_001", "npc_002"])
    await _write_messages(service, run, conversation, 6)

    async with run.lock:
        await service._join_conversation_locked(run, conversation, "npc_003")
        continuing_context = service._chat_context(run, conversation, "npc_002")
        joiner_context = service._chat_context(run, conversation, "npc_003")

    assert model.segment_prompts[0]["mode"] == "final"
    assert [
        item["messageId"] for item in continuing_context["boundaryMessages"]
    ] == ["msg_000003", "msg_000004", "msg_000005", "msg_000006"]
    assert continuing_context["segmentSummaries"][0]["summary"]["claims"] == [
        "滚动摘要已生成"
    ]
    assert joiner_context == {
        "segmentSummaries": [],
        "activeParticipants": [
            {"actorId": "npc_001", "name": "林慧兰", "kind": "npc"},
            {"actorId": "npc_002", "name": "沈星遥", "kind": "npc"},
            {"actorId": "npc_003", "name": "赵磊", "kind": "npc"},
        ],
        "replyTargets": [],
        "boundaryMessages": [],
        "messages": [],
        "recentOwnMessages": [],
    }

    restored = deserialize_run(serialize_run(run))
    restored_conversation = restored.conversations[conversation.conversation_id]
    restored_continuing_context = service._chat_context(
        restored,
        restored_conversation,
        "npc_001",
    )
    restored_joiner_context = service._chat_context(
        restored,
        restored_conversation,
        "npc_003",
    )
    assert [
        item["messageId"]
        for item in restored_continuing_context["boundaryMessages"]
    ] == ["msg_000003", "msg_000004", "msg_000005", "msg_000006"]
    assert restored_joiner_context["boundaryMessages"] == []
