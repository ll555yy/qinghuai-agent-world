from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.conversation import Conversation
from core.backend.app.orchestration.event_hub import EventHub
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.codec import deserialize_run, serialize_run
from core.backend.app.persistence.in_memory import InMemoryRunRepository
from core.backend.app.persistence.speech_example_retriever import (
    SpeechExampleHit,
    SpeechExampleSearchResult,
)


class ParallelRoundModel:
    def __init__(
        self,
        *,
        decision_barrier: int = 0,
        speech_barrier: int = 0,
        speak_calls: int = 0,
    ) -> None:
        self.decision_barrier = decision_barrier
        self.speech_barrier = speech_barrier
        self.speak_calls = speak_calls
        self.chat_calls = 0
        self.speech_calls = 0
        self.speech_contexts: list[dict[str, Any]] = []
        self.chat_batches: list[tuple[str, ...]] = []
        self.decision_started = asyncio.Event()
        self.decision_release = asyncio.Event()
        self.speech_started = asyncio.Event()
        self.speech_release = asyncio.Event()
        if decision_barrier == 0:
            self.decision_release.set()
        if speech_barrier == 0:
            self.speech_release.set()

    async def generate(self, request: Any) -> TextGenerationResult:
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        payload = json.loads(request.messages[0].content)
        actor_id = str(payload.get("actor", {}).get("actorId", "npc_unknown"))
        if protocol == "ChatDecision":
            self.chat_calls += 1
            self.chat_batches.append(
                tuple(payload.get("context", {}).get("triggerMessageIds", []))
            )
            call_number = self.chat_calls
            if self.decision_barrier and call_number <= self.decision_barrier:
                if call_number == self.decision_barrier:
                    self.decision_started.set()
                await self.decision_release.wait()
            value = (
                {
                    "result": "decided",
                    "action": "speak",
                    "responseDesire": 3 if actor_id == "npc_002" else 1,
                    "intent": "回应玩家刚才的发言",
                }
                if call_number <= self.speak_calls
                else {"result": "decided", "action": "wait"}
            )
        elif protocol == "SpeechGeneration":
            self.speech_calls += 1
            self.speech_contexts.append(payload)
            if self.speech_barrier and self.speech_calls <= self.speech_barrier:
                if self.speech_calls == self.speech_barrier:
                    self.speech_started.set()
                await self.speech_release.wait()
            value = {
                "text": f"{actor_id} 的并行回复。",
                "addressedActorIds": [],
            }
        elif protocol == "SegmentSummary":
            value = {"claims": [], "actorIds": payload.get("participants", [])}
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
            model="parallel-round",
        )


def _install_conversation(
    service: RunService,
    run: Any,
    participants: list[str],
) -> Conversation:
    conversation_id, creation_seq = run.next_conversation_identity()
    conversation = Conversation(conversation_id, creation_seq, list(participants))
    run.conversations[conversation_id] = conversation
    run.messages[conversation_id] = []
    run.segments[conversation_id] = [
        {
            "segmentId": run.next_segment_identity(),
            "participants": list(participants),
            "startedAt": run.clock.as_dict()["label"],
            "summary": None,
            "summaryThroughMessageId": None,
        }
    ]
    run.conversation_drafts[conversation_id] = {}
    for actor_id in participants:
        run.actor_states[actor_id]["status"] = "chatting"
        actor = service.registry.actor(actor_id)
        if actor is not None and actor.kind == "npc":
            run.conversation_drafts[conversation_id][actor_id] = {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            }
        run.memory_cache[(conversation_id, actor_id)] = set()
    service._round_state_locked(run, conversation)
    return conversation


@pytest.mark.anyio
async def test_player_message_returns_before_parallel_decision_and_speech(
    registry,
) -> None:
    model = ParallelRoundModel(
        decision_barrier=2,
        speech_barrier=2,
        speak_calls=2,
    )
    service = RunService(
        registry,
        text_model=model,
        chat_cooldown_seconds=10,
        chat_publish_delay_min_seconds=0,
        chat_publish_delay_max_seconds=0,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        await service.repository.save(run)

    started = time.monotonic()
    result = await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "你们都说说自己的看法。",
    )
    assert time.monotonic() - started < 0.2
    assert result["acceptedMessageId"] == run.messages[conversation.conversation_id][0]["messageId"]
    await asyncio.wait_for(model.decision_started.wait(), timeout=1)
    assert [item["authorActorId"] for item in run.messages[conversation.conversation_id]] == [
        registry.player_actor_id
    ]

    model.decision_release.set()
    await asyncio.wait_for(model.speech_started.wait(), timeout=1)
    assert model.speech_calls == 2
    model.speech_release.set()
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    npc_messages = run.messages[conversation.conversation_id][1:]
    assert [item["authorActorId"] for item in npc_messages] == ["npc_002", "npc_001"]
    assert len({item["roundId"] for item in npc_messages}) == 1
    assert [item["roundSequence"] for item in npc_messages] == [1, 2]
    assert all(item["replyToMessageIds"] == [result["acceptedMessageId"]] for item in npc_messages)
    await service.close()


@pytest.mark.anyio
async def test_speak_retrieves_examples_outside_lock_and_injects_safe_payload(
    registry,
) -> None:
    model = ParallelRoundModel(speak_calls=1)

    class RecordingRetriever:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []
            self.lock_states: list[bool] = []
            self.run = None

        async def search(self, *, npc_id: str, intent: str, limit: int = 3):
            self.calls.append((npc_id, intent, limit))
            assert self.run is not None
            self.lock_states.append(self.run.lock.locked())
            example = registry.speech_examples["npc001_refuse_mediate_01"]
            return SpeechExampleSearchResult(
                hits=(SpeechExampleHit(example=example, similarity=0.93),)
            )

    retriever = RecordingRetriever()
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    retriever.run = run
    async with run.lock:
        conversation = _install_conversation(
            service, run, [registry.player_actor_id, "npc_001"]
        )
        await service.repository.save(run)

    await service.player_message(
        run.run_id, conversation.conversation_id, "请你替我向周慎之说情。"
    )
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    assert retriever.calls == [("npc_001", "回应玩家刚才的发言", 3)]
    assert retriever.lock_states == [False]
    speech_context = model.speech_contexts[0]["context"]
    assert speech_context["speechExamples"] == [
        {
            "situation": "别人请求她代为向第三方说情",
            "intendedMove": "委婉拒绝代为沟通，把行动责任交还给请求者",
            "reply": "这话我替你递不合适。你若真有诚意，自己同他说。",
        }
    ]
    serialized = json.dumps(speech_context["speechExamples"], ensure_ascii=False)
    assert "exampleId" not in serialized
    assert "similarity" not in serialized
    assert any(
        message["text"] == "请你替我向周慎之说情。"
        for message in speech_context["messages"]
    )
    await service.close()


@pytest.mark.anyio
async def test_wait_does_not_retrieve_speech_examples(registry) -> None:
    class UnexpectedRetriever:
        async def search(self, **_kwargs):
            raise AssertionError("wait must not retrieve examples")

    service = RunService(
        registry,
        text_model=ParallelRoundModel(speak_calls=0),
        speech_example_retriever=UnexpectedRetriever(),
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service, run, [registry.player_actor_id, "npc_001"]
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "知道了。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    await service.close()


@pytest.mark.anyio
async def test_speech_example_failure_does_not_block_publication(registry) -> None:
    class FailingRetriever:
        async def search(self, **_kwargs):
            raise RuntimeError("retrieval unavailable")

    model = ParallelRoundModel(speak_calls=1)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=FailingRetriever(),
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service, run, [registry.player_actor_id, "npc_001"]
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "请回答。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    assert model.speech_contexts[0]["context"]["speechExamples"] == []
    assert any(
        message["authorActorId"] == "npc_001"
        for message in run.messages[conversation.conversation_id]
    )
    await service.close()


@pytest.mark.anyio
async def test_two_conversations_reach_provider_barrier_together(registry) -> None:
    model = ParallelRoundModel(decision_barrier=2)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        first = _install_conversation(service, run, ["npc_001", "npc_002"])
        second = _install_conversation(service, run, ["npc_003", "npc_004"])
        first_message = service._write_message_locked(run, first, "npc_001", "第一场。")
        second_message = service._write_message_locked(run, second, "npc_003", "第二场。")
        service._queue_message_round_locked(run, first, [first_message["messageId"]])
        service._queue_message_round_locked(run, second, [second_message["messageId"]])
        await service.repository.save(run)
    service._ensure_chat_task(run, first.conversation_id)
    service._ensure_chat_task(run, second.conversation_id)

    await asyncio.wait_for(model.decision_started.wait(), timeout=1)
    assert model.chat_calls == 2
    model.decision_release.set()
    await service.wait_for_chat_idle(run.run_id, first.conversation_id)
    await service.wait_for_chat_idle(run.run_id, second.conversation_id)
    await service.close()


@pytest.mark.anyio
async def test_player_message_resets_cooldown_before_single_final_check(registry) -> None:
    model = ParallelRoundModel()
    service = RunService(registry, text_model=model, chat_cooldown_seconds=0.08)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        await service.repository.save(run)

    await service.player_message(run.run_id, conversation.conversation_id, "第一句话。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    first_due = run.conversation_round_states[conversation.conversation_id]["cooldownDueAt"]

    await asyncio.sleep(0.02)
    await service.player_message(run.run_id, conversation.conversation_id, "冷场时再补一句。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    second_due = run.conversation_round_states[conversation.conversation_id]["cooldownDueAt"]
    assert second_due > first_due
    assert conversation.is_open

    await service.wait_for_chat_idle(
        run.run_id,
        conversation.conversation_id,
        include_cooldown=True,
    )
    assert not conversation.is_open
    assert model.chat_calls == 3
    await service.close()


@pytest.mark.anyio
async def test_one_stalled_npc_times_out_without_blocking_other_speaker(registry) -> None:
    never = asyncio.Event()

    class PartialTimeoutModel(ParallelRoundModel):
        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            payload = json.loads(request.messages[0].content)
            actor_id = str(payload.get("actor", {}).get("actorId", ""))
            if protocol == "ChatDecision" and actor_id == "npc_001":
                await never.wait()
            if protocol == "ChatDecision":
                self.chat_calls += 1
                value = {
                    "result": "decided",
                    "action": "speak" if actor_id == "npc_002" else "wait",
                    "responseDesire": 2,
                    "intent": "及时回应",
                }
                return TextGenerationResult(
                    text=json.dumps(value, ensure_ascii=False),
                    provider="test",
                    model="partial-timeout",
                )
            return await super().generate(request)

    model = PartialTimeoutModel()
    service = RunService(
        registry,
        text_model=model,
        chat_model_call_timeout_seconds=0.2,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "请都回应。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert any(
        message["authorActorId"] == "npc_002"
        for message in run.messages[conversation.conversation_id]
    ), (run.messages[conversation.conversation_id], model.chat_calls)
    await service.close()


@pytest.mark.anyio
async def test_player_leave_during_publish_discards_old_round_tail(registry) -> None:
    model = ParallelRoundModel(speak_calls=2)
    service = RunService(
        registry,
        text_model=model,
        chat_cooldown_seconds=10,
        chat_publish_delay_min_seconds=0.15,
        chat_publish_delay_max_seconds=0.15,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "请依次回答。")
    deadline = time.monotonic() + 1
    while len(run.messages[conversation.conversation_id]) < 2:
        if time.monotonic() >= deadline:
            raise TimeoutError("first NPC publication did not arrive")
        await asyncio.sleep(0.005)

    await service.remove_participant(
        run.run_id,
        conversation.conversation_id,
        registry.player_actor_id,
    )
    await asyncio.sleep(0.2)
    assert len(run.messages[conversation.conversation_id]) == 2
    state = run.conversation_round_states[conversation.conversation_id]
    assert state["status"] == "idle"
    assert state["triggerMessageIds"] == []
    await service.close()


@pytest.mark.anyio
async def test_player_leave_returns_before_segment_summary_maintenance(registry) -> None:
    class SlowSummaryModel(ParallelRoundModel):
        def __init__(self) -> None:
            super().__init__()
            self.summary_started = asyncio.Event()
            self.summary_release = asyncio.Event()

        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            if protocol == "SegmentSummary":
                self.summary_started.set()
                await self.summary_release.wait()
            return await super().generate(request)

    model = SlowSummaryModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        service._write_message_locked(
            run,
            conversation,
            registry.player_actor_id,
            "我先离开，你们继续。",
        )
        await service.repository.save(run)

    started = time.monotonic()
    await service.remove_participant(
        run.run_id,
        conversation.conversation_id,
        registry.player_actor_id,
    )
    assert time.monotonic() - started < 0.2
    await asyncio.wait_for(model.summary_started.wait(), timeout=1)
    assert registry.player_actor_id not in conversation.participants
    model.summary_release.set()
    await service.close()


@pytest.mark.anyio
async def test_player_leave_maintenance_resumes_after_service_restart(registry) -> None:
    class BlockedSummaryModel(ParallelRoundModel):
        def __init__(self) -> None:
            super().__init__()
            self.summary_started = asyncio.Event()
            self.summary_release = asyncio.Event()

        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            if protocol == "SegmentSummary":
                self.summary_started.set()
                await self.summary_release.wait()
            return await super().generate(request)

    blocked_model = BlockedSummaryModel()
    first_service = RunService(registry, text_model=blocked_model)
    created = await first_service.create_run()
    original = await first_service.get_run_entity(created["runId"])
    async with original.lock:
        conversation = _install_conversation(
            first_service,
            original,
            [registry.player_actor_id, "npc_001"],
        )
        first_service._write_message_locked(
            original,
            conversation,
            registry.player_actor_id,
            "我先离开。",
        )
        await first_service.repository.save(original)

    await first_service.remove_participant(
        original.run_id,
        conversation.conversation_id,
        registry.player_actor_id,
    )
    await asyncio.wait_for(blocked_model.summary_started.wait(), timeout=1)
    await first_service.close()
    encoded = serialize_run(original)

    restored = deserialize_run(encoded)
    repository = InMemoryRunRepository()
    await repository.add(restored)
    resumed = RunService(registry, repository=repository, text_model=ParallelRoundModel())
    await resumed.get_run_entity(restored.run_id)

    deadline = time.monotonic() + 1
    while restored.conversation_round_states.get(conversation.conversation_id):
        if time.monotonic() >= deadline:
            raise TimeoutError("leave maintenance did not finish after restart")
        await asyncio.sleep(0.005)

    segment = restored.segments[conversation.conversation_id][0]
    assert segment["summary"] is not None
    assert restored.consolidation_status[(conversation.conversation_id, "npc_001")][
        "status"
    ] == "succeeded"
    await resumed.close()


@pytest.mark.anyio
async def test_event_hub_buffers_out_of_order_publishers_by_event_sequence() -> None:
    hub = EventHub()
    queue = await hub.subscribe("run_ordered")
    await hub.publish("run_ordered", {"eventSeq": 2, "eventType": "second"})
    assert queue.empty()
    await hub.publish("run_ordered", {"eventSeq": 1, "eventType": "first"})
    assert (await queue.get())["eventSeq"] == 1
    assert (await queue.get())["eventSeq"] == 2


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["deciding", "generating"])
async def test_player_message_preempts_unpublished_active_round(registry, phase: str) -> None:
    model = ParallelRoundModel(
        decision_barrier=1 if phase == "deciding" else 0,
        speech_barrier=1 if phase == "generating" else 0,
        speak_calls=2,
    )
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        await service.repository.save(run)
    first = await service.player_message(run.run_id, conversation.conversation_id, "第一条。")
    if phase == "deciding":
        await asyncio.wait_for(model.decision_started.wait(), timeout=1)
    else:
        await asyncio.wait_for(model.speech_started.wait(), timeout=1)
    second = await service.player_message(run.run_id, conversation.conversation_id, "插入的第二条。")
    assert second["acceptedMessageId"] in {
        item["messageId"] for item in run.messages[conversation.conversation_id]
    }
    model.decision_release.set()
    model.speech_release.set()
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert model.chat_batches[:2] == [
        (first["acceptedMessageId"],),
        (first["acceptedMessageId"], second["acceptedMessageId"]),
    ]
    npc_messages = [
        message
        for message in run.messages[conversation.conversation_id]
        if message["authorActorId"] == "npc_001"
    ]
    assert len(npc_messages) == 1
    assert npc_messages[0]["replyToMessageIds"] == [
        first["acceptedMessageId"],
        second["acceptedMessageId"],
    ]
    await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["deciding", "generating"])
async def test_player_message_preempts_inflight_final_check(registry, phase: str) -> None:
    model = ParallelRoundModel(
        decision_barrier=1 if phase == "deciding" else 0,
        speech_barrier=1 if phase == "generating" else 0,
        speak_calls=2,
    )
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        state = service._round_state_locked(run, conversation)
        state.update(
            {
                "status": "final_check",
                "roundVersion": 1,
                "finalCheckUsed": True,
                "syntheticTrigger": "final_check",
                "triggerMessageIds": [],
            }
        )
        await service.repository.save(run)
    service._ensure_chat_task(run, conversation.conversation_id)

    if phase == "deciding":
        await asyncio.wait_for(model.decision_started.wait(), timeout=1)
    else:
        await asyncio.wait_for(model.speech_started.wait(), timeout=1)

    player = await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "打断冷场复查的新问题。",
    )
    async with run.lock:
        state = service._round_state_locked(run, conversation)
        assert state["status"] == "queued"
        assert state["triggerMessageIds"] == [player["acceptedMessageId"]]
        assert state["finalCheckUsed"] is False

    model.decision_release.set()
    model.speech_release.set()
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    npc_messages = [
        message
        for message in run.messages[conversation.conversation_id]
        if message["authorActorId"] == "npc_001"
    ]
    assert len(npc_messages) == 1
    assert npc_messages[0]["replyToMessageIds"] == [player["acceptedMessageId"]]
    assert model.chat_batches[:2] == [(), (player["acceptedMessageId"],)]
    await service.close()


@pytest.mark.anyio
async def test_player_message_discards_unpublished_final_check_speech(registry) -> None:
    model = ParallelRoundModel(speak_calls=1)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        state = service._round_state_locked(run, conversation)
        state.update(
            {
                "status": "publishing",
                "roundId": 1,
                "roundVersion": 1,
                "finalCheckUsed": True,
                "syntheticTrigger": "final_check",
                "pendingPublications": [
                    {
                        "actorId": "npc_001",
                        "text": "这句已经生成但不应发布。",
                        "decision": {
                            "result": "decided",
                            "action": "speak",
                            "responseDesire": 1,
                            "intent": "冷场后补充",
                        },
                        "roundSequence": 1,
                        "replyToMessageIds": [],
                    }
                ],
            }
        )
        await service.repository.save(run)

    player = await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "发布前到达的玩家问题。",
    )
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    texts = [message["text"] for message in run.messages[conversation.conversation_id]]
    assert "这句已经生成但不应发布。" not in texts
    npc_messages = [
        message
        for message in run.messages[conversation.conversation_id]
        if message["authorActorId"] == "npc_001"
    ]
    assert len(npc_messages) == 1
    assert npc_messages[0]["replyToMessageIds"] == [player["acceptedMessageId"]]
    await service.close()


@pytest.mark.anyio
async def test_player_message_during_publish_gap_discards_unpublished_tail(registry) -> None:
    model = ParallelRoundModel(speak_calls=4)
    service = RunService(
        registry,
        text_model=model,
        chat_cooldown_seconds=10,
        chat_publish_delay_min_seconds=0.12,
        chat_publish_delay_max_seconds=0.12,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        await service.repository.save(run)
    queue = await service.event_hub.subscribe(run.run_id)
    await service.player_message(run.run_id, conversation.conversation_id, "第一条。")
    player_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert player_event["eventType"] == "message_created"
    first_npc_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert first_npc_event["payload"]["authorActorId"].startswith("npc_")
    inserted = await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "发布间隔中的玩家消息。",
    )
    inserted_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert inserted_event["payload"]["messageId"] == inserted["acceptedMessageId"]
    second_npc_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert second_npc_event["payload"]["authorActorId"].startswith("npc_")
    assert second_npc_event["payload"]["replyToMessageIds"] == [
        inserted["acceptedMessageId"]
    ]
    assert second_npc_event["payload"]["roundId"] != first_npc_event["payload"]["roundId"]
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    batches_with_inserted = [
        batch
        for batch in model.chat_batches
        if inserted["acceptedMessageId"] in batch
    ]
    assert len(set(batches_with_inserted)) == 1
    assert len(batches_with_inserted) == 2  # one frozen next-round decision per NPC
    await service.close()


@pytest.mark.anyio
async def test_chat_context_explicitly_lists_recent_own_messages(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        for index in range(5):
            service._write_message_locked(
                run,
                conversation,
                "npc_001",
                f"自己的第 {index + 1} 句。",
            )
        other_message = service._write_message_locked(
            run,
            conversation,
            "npc_002",
            "别人的一句。",
        )
        persisted_before = json.loads(
            json.dumps(run.messages[conversation.conversation_id], ensure_ascii=False)
        )
        context = service._chat_context(
            run,
            conversation,
            "npc_001",
            reply_to_message_ids=[other_message["messageId"]],
        )

    assert [item["text"] for item in context["recentOwnMessages"]] == [
        "自己的第 2 句。",
        "自己的第 3 句。",
        "自己的第 4 句。",
        "自己的第 5 句。",
    ]
    assert all(
        item["authorActorId"] == "npc_001"
        for item in context["recentOwnMessages"]
    )
    assert all(
        item["authorName"] == "林慧兰" for item in context["recentOwnMessages"]
    )
    assert [item["actorId"] for item in context["activeParticipants"]] == [
        registry.player_actor_id,
        "npc_001",
        "npc_002",
    ]
    assert all(
        item["actorId"] != "npc_005" for item in context["activeParticipants"]
    )
    assert context["replyTargets"] == [
        {
            "messageId": other_message["messageId"],
            "authorActorId": "npc_002",
            "authorName": "沈星遥",
        }
    ]
    assert all("authorName" in item for item in context["messages"])
    assert run.messages[conversation.conversation_id] == persisted_before
    await service.close()


@pytest.mark.anyio
async def test_invalid_speech_addressee_retries_once_and_publishes_correction(
    registry,
) -> None:
    class CorrectingAddresseeModel(ParallelRoundModel):
        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            payload = json.loads(request.messages[0].content)
            if protocol != "SpeechGeneration":
                return await super().generate(request)
            self.speech_calls += 1
            self.speech_contexts.append(payload)
            if self.speech_calls == 1:
                value = {
                    "text": "周老板，您放心。",
                    "addressedActorIds": ["npc_005"],
                }
            else:
                value = {
                    "text": "林老师，您放心。",
                    "addressedActorIds": ["npc_001"],
                }
            return TextGenerationResult(
                text=json.dumps(value, ensure_ascii=False),
                provider="test",
                model="correcting-addressee",
            )

    model = CorrectingAddresseeModel(speak_calls=1)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_003", "npc_001"],
        )
        await service.repository.save(run)

    await service.player_message(run.run_id, conversation.conversation_id, "请回答。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    assert model.speech_calls == 2
    assert model.speech_contexts[1]["context"]["identityCorrection"][
        "invalidAddressedActorIds"
    ] == ["npc_005"]
    npc_messages = [
        message
        for message in run.messages[conversation.conversation_id]
        if message["authorActorId"].startswith("npc_")
    ]
    assert [message["text"] for message in npc_messages] == ["林老师，您放心。"]
    await service.close()


@pytest.mark.anyio
async def test_twice_invalid_speech_addressee_is_not_published(registry) -> None:
    class InvalidAddresseeModel(ParallelRoundModel):
        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            if protocol != "SpeechGeneration":
                return await super().generate(request)
            self.speech_calls += 1
            return TextGenerationResult(
                text=json.dumps(
                    {
                        "text": "周老板，您放心。",
                        "addressedActorIds": ["npc_005"],
                    },
                    ensure_ascii=False,
                ),
                provider="test",
                model="invalid-addressee",
            )

    model = InvalidAddresseeModel(speak_calls=1)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_003", "npc_001"],
        )
        await service.repository.save(run)

    await service.player_message(run.run_id, conversation.conversation_id, "请回答。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    assert model.speech_calls == 2
    assert all(
        not message["authorActorId"].startswith("npc_")
        for message in run.messages[conversation.conversation_id]
    )
    await service.close()


@pytest.mark.anyio
async def test_persisted_queued_round_resumes_after_service_restart(registry) -> None:
    first_service = RunService(registry, text_model=None)
    created = await first_service.create_run()
    original = await first_service.get_run_entity(created["runId"])
    async with original.lock:
        conversation = _install_conversation(
            first_service,
            original,
            [registry.player_actor_id, "npc_001"],
        )
        message = first_service._write_message_locked(
            original,
            conversation,
            registry.player_actor_id,
            "重启后继续处理。",
        )
        first_service._queue_message_round_locked(
            original,
            conversation,
            [message["messageId"]],
        )
        encoded = serialize_run(original)
    await first_service.close()

    restored = deserialize_run(encoded)
    repository = InMemoryRunRepository()
    await repository.add(restored)
    model = ParallelRoundModel(speak_calls=1)
    resumed = RunService(
        registry,
        repository=repository,
        text_model=model,
        chat_cooldown_seconds=10,
    )
    await resumed.get_run_entity(restored.run_id)
    await resumed.wait_for_chat_idle(restored.run_id, conversation.conversation_id)
    assert any(
        item["authorActorId"] == "npc_001"
        and item.get("replyToMessageIds") == [message["messageId"]]
        for item in restored.messages[conversation.conversation_id]
    )
    await resumed.close()


@pytest.mark.anyio
async def test_one_speech_generation_failure_keeps_other_npc_message(registry) -> None:
    class OneBrokenSpeechModel(ParallelRoundModel):
        async def generate(self, request: Any) -> TextGenerationResult:
            protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
            payload = json.loads(request.messages[0].content)
            actor_id = str(payload.get("actor", {}).get("actorId", ""))
            if protocol == "SpeechGeneration" and actor_id == "npc_001":
                return TextGenerationResult(
                    text="not-json",
                    provider="test",
                    model="one-broken-speech",
                )
            return await super().generate(request)

    model = OneBrokenSpeechModel(speak_calls=2)
    service = RunService(registry, text_model=model, chat_cooldown_seconds=10)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001", "npc_002"],
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "请回答。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    npc_authors = [
        message["authorActorId"]
        for message in run.messages[conversation.conversation_id]
        if message["authorActorId"].startswith("npc_")
    ]
    assert npc_authors == ["npc_002"]
    await service.close()
