from __future__ import annotations

import json

import pytest

from core.backend.app.ai.decision_service import DecisionService, StructuredCallFailed
from core.backend.app.ai.errors import AIError, AIErrorCode
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.ai.protocols import ChatDecision
from core.backend.app.domain.errors import (
    ActorAlreadyInConversationError,
    ChapterAlreadyEndedError,
    InvalidConversationParticipantsError,
    InvalidInvitationError,
)
from core.backend.app.orchestration.run_service import RunService


class InvalidModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return TextGenerationResult(text="not-json", provider="fake", model="fake")


class UnconfiguredModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        raise AIError(AIErrorCode.NOT_CONFIGURED, "not configured")


class RecallModel:
    def __init__(self) -> None:
        self.chat_prompts: list[dict] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        if protocol == "ChatDecision":
            self.chat_prompts.append(context)
            if len(self.chat_prompts) == 1:
                value = {
                    "result": "need_memory",
                    "memoryQuery": {"actorIds": ["player_001"]},
                }
            else:
                value = {"result": "decided", "action": "wait"}
        elif protocol == "SegmentSummary":
            value = {"claims": []}
        else:
            value = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        return TextGenerationResult(
            text=json.dumps(value), provider="fake", model="fake"
        )


class JoinRecordingModel:
    def __init__(self) -> None:
        self.chat_contexts: list[dict] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        if protocol == "SegmentSummary":
            value = {"claims": []}
        elif protocol == "InvitationDecision":
            value = {"decision": "accept"}
        elif protocol == "ChatDecision":
            self.chat_contexts.append(context)
            value = {"result": "decided", "action": "wait"}
        else:
            value = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        return TextGenerationResult(
            text=json.dumps(value), provider="fake", model="fake"
        )


class ConsolidationModel:
    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        if protocol == "SegmentSummary":
            value = {"claims": ["林慧兰接受了讨论结果"], "actorIds": ["npc_001", "npc_002"]}
        elif protocol == "ExitConsolidation":
            own_message = next(
                item for item in context["context"]["messages"]
                if item["authorActorId"] == "npc_001"
            )
            value = {
                "memories": [
                    {
                        "ref": "m1",
                        "type": "commitment",
                        "content": "我在讨论中明确支持先保住书店。",
                        "actorIds": ["npc_002"],
                        "topicHints": ["书店存续"],
                        "importance": 4,
                        "confidence": "high",
                        "evidenceMessageIds": [own_message["messageId"]],
                        "goalIds": ["goal_001_public", "goal_002_public"],
                    }
                ],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [
                    {
                        "ref": "g1",
                        "description": "继续确认沈星遥是否愿意参与文社",
                        "parentGoalId": "goal_001_public",
                        "targetActorIds": ["npc_002"],
                        "topicHints": ["青槐文社"],
                        "importance": 3,
                        "triggerMemoryRefs": ["m1"],
                    }
                ],
                "chapterEffects": [],
            }
        else:
            value = {"result": "decided", "action": "wait"}
        return TextGenerationResult(
            text=json.dumps(value), provider="fake", model="fake"
        )


@pytest.mark.anyio
async def test_each_npc_thinks_once_and_after_day1_event(registry) -> None:
    service = RunService(registry, text_model=None, seed=7)
    created = await service.create_run()
    await service.world_step(created["runId"], 540)
    run = await service.get_run_entity(created["runId"])
    event_seq = next(
        event.event_seq
        for event in run.events
        if event.event_type == "world_event_occurred"
        and event.payload["event"]["eventId"] == "event_day1_recovery_notice"
    )
    thoughts = [event for event in run.events if event.event_type == "npc_thought_started"]
    assert len(thoughts) == 5
    assert len({event.payload["actorId"] for event in thoughts}) == 5
    assert all(event.event_seq > event_seq for event in thoughts)


@pytest.mark.anyio
async def test_player_invitation_refusal_keeps_visible_event_order_and_no_private_intent(
    registry,
) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    before = created["eventSeq"]
    result = await service.player_invite(created["runId"], "npc_001")
    events = (await service.get_events(created["runId"], before))["events"]
    assert [event["eventType"] for event in events] == [
        "actor_movement_started",
        "actor_movement_completed",
        "invitation_requested",
        "invitation_request_cleared",
        "invitation_refused",
    ]
    assert result["invitation"]["status"] == "refused"
    assert all(not key.startswith("_") for key in result["invitation"])


@pytest.mark.anyio
async def test_observed_event_only_enters_witness_private_memory(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        for actor_id in run.positions:
            run.positions[actor_id] = {"x": 100, "y": 100}
        run.positions["npc_001"] = {"x": 0, "y": 0}
    await service.world_step(created["runId"], 541)
    public = await service.get_run(created["runId"])
    assert "event_day2_bookstore_leak" not in {
        event["eventId"] for event in public["worldEvents"]
    }
    assert "bookstoreLeak" not in public["currentWorldState"]
    witnesses = {
        memory["ownerNpcId"]
        for memory in run.memories.values()
        if memory.get("eventId") == "event_day2_bookstore_leak"
    }
    assert witnesses == {"npc_001"}


@pytest.mark.anyio
async def test_new_npc_cannot_see_old_segment_but_player_join_receives_full_history(
    registry,
) -> None:
    join_model = JoinRecordingModel()
    service = RunService(registry, text_model=join_model)
    first = await service.create_run()
    created = await service.create_conversation(
        first["runId"], ["npc_001", "npc_002"]
    )
    conversation_id = created["conversation"]["conversationId"]
    run = await service.get_run_entity(first["runId"])
    async with run.lock:
        conversation = run.conversations[conversation_id]
        service._write_message_locked(run, conversation, "npc_001", "加入前的旧话")
    await service.add_participant(first["runId"], conversation_id, "npc_003")
    assert service._visible_messages(run, conversation, "npc_003") == []
    assert service._visible_messages(run, conversation, "npc_001")[0]["text"] == "加入前的旧话"
    assert {item["actor"]["actorId"] for item in join_model.chat_contexts} == {
        "npc_001",
        "npc_002",
    }
    assert {
        item["context"]["trigger"] for item in join_model.chat_contexts
    } == {"actor_joined:npc_003"}
    background_event = next(
        event for event in reversed(run.events) if event.event_type == "conversation_activity"
    )
    assert "text" not in background_event.payload

    second = await service.create_run()
    created = await service.create_conversation(
        second["runId"], ["npc_001", "npc_002"]
    )
    conversation_id = created["conversation"]["conversationId"]
    run = await service.get_run_entity(second["runId"])
    async with run.lock:
        conversation = run.conversations[conversation_id]
        service._write_message_locked(run, conversation, "npc_001", "玩家加入前的记录")
    joined = await service.player_join(second["runId"], conversation_id)
    assert joined["messages"][0]["text"] == "玩家加入前的记录"
    assert "visibleToNpcIds" not in joined["messages"][0]


@pytest.mark.anyio
async def test_memory_recall_happens_at_most_once_and_is_owner_scoped(registry) -> None:
    model = RecallModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    conversation = await service.create_conversation(
        created["runId"], ["npc_001", "player_001"]
    )
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        run.memories["foreign_memory"] = {
            "memoryId": "foreign_memory",
            "ownerNpcId": "npc_002",
            "content": "不应出现在林慧兰提示词里的标记",
            "actorIds": ["player_001"],
            "topicIds": [],
            "importance": 5,
        }
    await service.player_message(
        created["runId"], conversation["conversation"]["conversationId"], "你还记得我吗？"
    )
    assert len(model.chat_prompts) == 2
    assert all(
        "不应出现在林慧兰提示词里的标记"
        not in json.dumps(prompt, ensure_ascii=False)
        for prompt in model.chat_prompts
    )


@pytest.mark.anyio
async def test_chat_drafts_commit_once_and_create_atomic_memory_goal_links(registry) -> None:
    service = RunService(registry, text_model=ConsolidationModel())
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"], ["npc_001", "npc_002"]
    )
    conversation_id = opened["conversation"]["conversationId"]
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = run.conversations[conversation_id]
        other_line = service._write_message_locked(
            run, conversation, "npc_002", "我愿意帮忙，但先说清楚分工。"
        )
        own_line = service._write_message_locked(
            run, conversation, "npc_001", "那就先保住书店，我会牵头协调。"
        )
        before_relation = dict(run.relationships[("npc_001", "npc_002")])
        decision = ChatDecision.model_validate(
            {
                "result": "decided",
                "action": "wait",
                "goalUpdates": [
                    {
                        "goalId": "goal_001_public",
                        "newStatus": "achieved",
                        "reason": "形成明确协调承诺",
                        "evidenceMessageIds": [own_line["messageId"]],
                    }
                ],
                "relationshipUpdates": [
                    {
                        "targetActorId": "npc_002",
                        "dimension": "trust",
                        "direction": "increase",
                        "evidenceMessageIds": [other_line["messageId"]],
                    },
                    {
                        "targetActorId": "npc_002",
                        "dimension": "tension",
                        "direction": "increase",
                        "evidenceMessageIds": [other_line["messageId"]],
                    },
                    {
                        "targetActorId": "npc_002",
                        "dimension": "trust",
                        "direction": "decrease",
                        "evidenceMessageIds": [other_line["messageId"]],
                    },
                ],
                "chapterEffects": [
                    {
                        "kind": "overall_stance",
                        "value": "support",
                        "evidenceMessageIds": [own_line["messageId"]],
                    }
                ],
            }
        )
        service._apply_chat_drafts(run, conversation, "npc_001", decision)
        effective = json.loads(
            service._npc_prompt(
                run,
                "npc_001",
                "chat_decision",
                {"conversationId": conversation_id, "memoryCache": []},
            )
        )
        assert next(
            goal for goal in effective["goals"] if goal["goalId"] == "goal_001_public"
        )["status"] == "achieved"
        relation = next(
            item for item in effective["relationships"] if item["toActorId"] == "npc_002"
        )
        assert relation["trust"] == min(2, before_relation["trust"] + 1)
        assert relation["tension"] == min(2, before_relation["tension"] + 1)

    await service.remove_participant(
        created["runId"], conversation_id, "npc_001"
    )
    relation = run.relationships[("npc_001", "npc_002")]
    assert run.goals["goal_001_public"]["status"] == "achieved"
    assert relation["trust"] == min(2, before_relation["trust"] + 1)
    assert relation["tension"] == min(2, before_relation["tension"] + 1)
    assert relation["interactionCount"] == before_relation["interactionCount"] + 1
    assert run.chapter_actor_stances["npc_001"] == "support"
    memory = next(
        item for item in run.memories.values()
        if item.get("content") == "我在讨论中明确支持先保住书店。"
    )
    assert memory["ownerNpcId"] == "npc_001"
    assert memory["goalIds"] == ["goal_001_public"]
    short_goal = next(
        item for item in run.goals.values()
        if item.get("parentGoalId") == "goal_001_public"
    )
    assert any(
        link["memoryId"] == memory["memoryId"]
        and link["targetId"] == short_goal["goalId"]
        and link.get("role") == "trigger"
        for link in run.memory_links
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authorization", "stances", "expected"),
    [
        ("approved", ["support"] * 5, "consensus_submitted"),
        (
            "conditional",
            ["conditional", "conditional", "unknown", "unknown", "conditional"],
            "compromise_submitted",
        ),
        ("none", ["unknown"] * 5, "no_submission"),
    ],
)
async def test_day7_resolution_is_fixed_from_committed_state(
    registry, authorization, stances, expected
) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run(agenda_id="agenda_001_literary_society")
    run = await service.get_run_entity(created["runId"])
    deadline = next(
        event for event in registry.events if event.event_id == "event_day7_proposal_deadline"
    )
    async with run.lock:
        run.zhou_authorization = authorization
        for npc, stance in zip(registry.npcs, stances, strict=True):
            run.chapter_actor_stances[npc.actor_id] = stance
            run.chapter_agenda_stances[
                ("agenda_001_literary_society", npc.actor_id)
            ] = stance
        await service._finish_chapter_locked(run, deadline)
    assert run.chapter_resolution["branch"] == expected
    if expected == "consensus_submitted":
        assert run.chapter_resolution["agendaResults"]["agenda_001_literary_society"] == "core_adopted"
        assert run.chapter_resolution["playerTaskResult"] == "completed"
    elif expected == "compromise_submitted":
        assert run.chapter_resolution["agendaResults"]["agenda_001_literary_society"] == "partially_adopted"
        assert run.chapter_resolution["playerTaskResult"] == "partial"
    else:
        assert run.chapter_resolution["playerTaskResult"] == "failed"


@pytest.mark.anyio
async def test_invalid_model_output_uses_one_retry_then_simple_fallback() -> None:
    model = InvalidModel()
    decisions = DecisionService(model)
    assert (await decisions.daily_action("{}")).action == "wait"
    assert model.calls == 2
    with pytest.raises(StructuredCallFailed):
        await decisions.speech("{}")
    assert model.calls == 4

    unconfigured = UnconfiguredModel()
    decisions = DecisionService(unconfigured)
    assert (await decisions.daily_action("{}")).action == "wait"
    assert unconfigured.calls == 1


@pytest.mark.anyio
async def test_npc_departs_after_all_own_goals_are_terminal(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"], ["npc_001", "npc_002"]
    )
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        for goal in run.goals.values():
            if goal["ownerNpcId"] == "npc_001":
                goal["status"] = "achieved"
    await service.remove_participant(
        created["runId"], opened["conversation"]["conversationId"], "npc_001"
    )
    assert run.actor_states["npc_001"]["status"] == "departed"


@pytest.mark.anyio
async def test_chapter_end_rejects_new_invite_before_mutating_state(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    deadline = next(
        event for event in registry.events if event.event_id == "event_day7_proposal_deadline"
    )
    async with run.lock:
        await service._finish_chapter_locked(run, deadline)
        before_seq = run.event_seq
        before_position = dict(run.positions["player_001"])
    with pytest.raises(ChapterAlreadyEndedError):
        await service.player_invite(created["runId"], "npc_001")
    assert run.event_seq == before_seq
    assert run.positions["player_001"] == before_position


@pytest.mark.anyio
async def test_pending_invitation_must_be_answered_before_joining_another_chat(
    registry,
) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"], ["npc_002", "npc_003"]
    )
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        await service._request_invitation_locked(
            run, "npc_001", "player_001", private_goal_id="goal_001_public"
        )
    with pytest.raises(InvalidInvitationError):
        await service.player_join(
            created["runId"], opened["conversation"]["conversationId"]
        )
    assert run.conversations[opened["conversation"]["conversationId"]].participants == [
        "npc_002",
        "npc_003",
    ]


@pytest.mark.anyio
async def test_busy_player_invite_fails_before_movement_or_state_change(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    await service.create_conversation(
        created["runId"], ["player_001", "npc_002"]
    )
    run = await service.get_run_entity(created["runId"])
    before_seq = run.event_seq
    before_position = dict(run.positions["player_001"])
    before_status = run.actor_states["player_001"]["status"]
    with pytest.raises(ActorAlreadyInConversationError):
        await service.player_invite(created["runId"], "npc_001")
    assert run.event_seq == before_seq
    assert run.positions["player_001"] == before_position
    assert run.actor_states["player_001"]["status"] == before_status


@pytest.mark.anyio
async def test_failed_invitation_acceptance_stays_pending_without_clear_event(
    registry,
) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        invitation = await service._request_invitation_locked(
            run, "npc_001", "player_001", private_goal_id="goal_001_public"
        )
        service._open_conversation_locked(
            run, ["player_001", "npc_002"], opening_speech=False
        )
        before_seq = run.event_seq
    with pytest.raises(ActorAlreadyInConversationError):
        await service.respond_invitation(
            created["runId"], invitation["invitationId"], accepted=True
        )
    assert invitation["status"] == "pending"
    assert "conversationId" not in invitation
    assert run.event_seq == before_seq


@pytest.mark.anyio
async def test_departed_npc_cannot_be_created_or_added_to_chat(registry) -> None:
    service = RunService(registry, text_model=None)
    first = await service.create_run()
    run = await service.get_run_entity(first["runId"])
    async with run.lock:
        run.actor_states["npc_001"]["status"] = "departed"
    with pytest.raises(InvalidConversationParticipantsError):
        await service.create_conversation(
            first["runId"], ["npc_001", "npc_002"]
        )

    second = await service.create_run()
    opened = await service.create_conversation(
        second["runId"], ["npc_002", "npc_003"]
    )
    run = await service.get_run_entity(second["runId"])
    async with run.lock:
        run.actor_states["npc_001"]["status"] = "departed"
    with pytest.raises(InvalidConversationParticipantsError):
        await service.add_participant(
            second["runId"], opened["conversation"]["conversationId"], "npc_001"
        )
