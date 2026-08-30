from __future__ import annotations

import json
from copy import deepcopy

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.clock import WorldTime
from core.backend.app.domain.errors import (
    ActorAlreadyInConversationError,
    ConversationFullError,
    InvalidConversationParticipantsError,
    InvalidInvitationError,
    InvalidJoinRequestError,
)
from core.backend.app.orchestration.run_service import RunService


class JoinDecisionModel:
    def __init__(self, decisions: dict[str, str] | None = None) -> None:
        self.decisions = decisions or {}
        self.join_contexts: list[dict] = []
        self.chat_contexts: list[dict] = []
        self.speech_contexts: list[dict] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        if protocol == "InvitationDecision":
            self.join_contexts.append(context)
            actor_id = context["actor"]["actorId"]
            value = {"decision": self.decisions.get(actor_id, "accept")}
        elif protocol == "ChatDecision":
            self.chat_contexts.append(context)
            value = {"result": "decided", "action": "wait"}
        elif protocol == "DailyActionDecision":
            value = {"action": "wait"}
        elif protocol == "SegmentSummary":
            value = {"claims": []}
        elif protocol == "SpeechGeneration":
            self.speech_contexts.append(context)
            value = {"text": ""}
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
            provider="fake",
            model="join-decisions",
        )


class SeekJoinModel(JoinDecisionModel):
    def __init__(self, applicant_id: str, goal_id: str, target_id: str) -> None:
        super().__init__()
        self.applicant_id = applicant_id
        self.goal_id = goal_id
        self.target_id = target_id

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        if protocol != "DailyActionDecision":
            return await super().generate(request)
        context = json.loads(request.messages[0].content)
        if context["actor"]["actorId"] == self.applicant_id:
            value = {
                "action": "seek_chat",
                "goalId": self.goal_id,
                "targetActorId": self.target_id,
                "intent": "申请加入正在进行的聊天",
            }
        else:
            value = {"action": "wait"}
        return TextGenerationResult(
            text=json.dumps(value, ensure_ascii=False),
            provider="fake",
            model="seek-join",
        )


async def _two_npc_chat(service: RunService) -> tuple[object, object, str]:
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", "npc_002"],
    )
    run = await service.get_run_entity(created["runId"])
    return created, run, opened["conversation"]["conversationId"]


@pytest.mark.anyio
async def test_two_npc_approvers_must_both_accept_before_join(registry) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created, run, conversation_id = await _two_npc_chat(service)
    await service.wait_for_chat_idle(created["runId"], conversation_id)
    model.chat_contexts.clear()
    conversation = run.conversations[conversation_id]
    async with run.lock:
        service._write_message_locked(run, conversation, "npc_001", "加入前的旧话")

    result = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_003",
        "join-once",
    )
    repeated = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_003",
        "join-once",
    )
    await service.wait_for_chat_idle(created["runId"], conversation_id)

    assert result == repeated
    assert result["joinRequest"]["status"] == "accepted"
    assert conversation.participants == ["npc_001", "npc_002", "npc_003"]
    assert [context["actor"]["actorId"] for context in model.join_contexts] == [
        "npc_001",
        "npc_002",
    ]
    assert all(
        context["context"]["applicant"]["actorId"] == "npc_003"
        and context["context"]["requestKind"] == "join_request"
        and "privateGoalId" not in context["context"]
        for context in model.join_contexts
    )
    assert service._visible_messages(run, conversation, "npc_003") == []
    assert service._visible_messages(run, conversation, "npc_001")[0]["text"] == "加入前的旧话"
    assert len(run.segments[conversation_id]) == 2
    assert run.segments[conversation_id][0]["endedAt"] == "Day1 09:00"
    assert run.segments[conversation_id][1]["participants"] == conversation.participants
    assert [context["actor"]["actorId"] for context in model.chat_contexts] == [
        "npc_003"
    ]
    assert model.chat_contexts[0]["context"]["trigger"] == "join_opener"
    join_speech_context = model.speech_contexts[-1]["context"]
    assert join_speech_context["speechExamples"] == []
    assert all(
        message["text"] != "加入前的旧话"
        for message in join_speech_context["messages"]
    )
    assert len(
        [event for event in run.events if event.event_type == "join_request_created"]
    ) == 1
    assert len(
        [
            event
            for event in run.events
            if event.event_type == "conversation_participant_joined"
        ]
    ) == 1


@pytest.mark.anyio
async def test_one_npc_refusal_stops_join_without_relation_change(registry) -> None:
    model = JoinDecisionModel({"npc_001": "refuse"})
    service = RunService(registry, text_model=model)
    created, run, conversation_id = await _two_npc_chat(service)
    applicant_relationships_before = {
        key: deepcopy(value)
        for key, value in run.relationships.items()
        if "npc_002" in key
    }

    result = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_003",
    )

    assert result["joinRequest"]["status"] == "refused"
    assert run.conversations[conversation_id].participants == ["npc_001", "npc_002"]
    assert [context["actor"]["actorId"] for context in model.join_contexts] == [
        "npc_001",
        "npc_002",
    ]
    assert run.actor_states["npc_003"]["status"] == "waiting"
    assert {
        key: value for key, value in run.relationships.items() if "npc_002" in key
    } == applicant_relationships_before
    resolved = next(
        event for event in run.events if event.event_type == "join_request_resolved"
    )
    assert resolved.payload["status"] == "refused"
    assert "approverActorId" not in resolved.payload
    assert not any(
        event.event_type == "conversation_participant_joined" for event in run.events
    )


@pytest.mark.anyio
async def test_player_approver_must_respond_and_response_is_idempotent(registry) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    conversation_id = opened["conversation"]["conversationId"]
    await service.wait_for_chat_idle(created["runId"], conversation_id)
    run = await service.get_run_entity(created["runId"])

    pending = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_002",
        "request-player-approval",
    )
    assert pending["joinRequest"]["status"] == "pending"
    assert pending["joinRequest"]["pendingPlayerDecision"] is True
    assert run.conversations[conversation_id].participants == [
        "npc_001",
        registry.player_actor_id,
    ]
    assert [context["actor"]["actorId"] for context in model.join_contexts] == [
        "npc_001"
    ]

    join_request_id = pending["joinRequest"]["joinRequestId"]
    accepted = await service.respond_join_request(
        created["runId"],
        join_request_id,
        True,
        "player-accept-once",
    )
    repeated = await service.respond_join_request(
        created["runId"],
        join_request_id,
        True,
        "player-accept-once",
    )
    assert accepted == repeated
    assert accepted["joinRequest"]["status"] == "accepted"
    assert run.conversations[conversation_id].participants == [
        "npc_001",
        registry.player_actor_id,
        "npc_002",
    ]
    assert len(
        [
            event
            for event in run.events
            if event.event_type == "conversation_participant_joined"
        ]
    ) == 1


@pytest.mark.anyio
async def test_player_approver_can_refuse_without_revealing_who_refused(
    registry,
) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    conversation_id = opened["conversation"]["conversationId"]
    run = await service.get_run_entity(created["runId"])
    pending = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_002",
    )

    refused = await service.respond_join_request(
        created["runId"],
        pending["joinRequest"]["joinRequestId"],
        False,
    )

    assert refused["joinRequest"]["status"] == "refused"
    assert run.conversations[conversation_id].participants == [
        "npc_001",
        registry.player_actor_id,
    ]
    assert run.actor_states["npc_002"]["status"] == "waiting"
    event = next(
        event
        for event in reversed(run.events)
        if event.event_type == "join_request_resolved"
    )
    assert event.payload["status"] == "refused"
    assert "approverActorId" not in event.payload


@pytest.mark.anyio
async def test_player_applicant_needs_both_npc_approvals_and_gets_history(registry) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created, run, conversation_id = await _two_npc_chat(service)
    conversation = run.conversations[conversation_id]
    async with run.lock:
        service._write_message_locked(run, conversation, "npc_001", "玩家面板中的旧记录")

    result = await service.player_join(created["runId"], conversation_id)

    assert result["joinRequest"]["status"] == "accepted"
    assert [context["actor"]["actorId"] for context in model.join_contexts] == [
        "npc_001",
        "npc_002",
    ]
    assert result["messages"][0]["text"] == "玩家面板中的旧记录"
    assert "visibleToNpcIds" not in result["messages"][0]


@pytest.mark.anyio
async def test_cutoff_expires_pending_join_without_refusal_or_relation_change(
    registry,
) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    conversation_id = opened["conversation"]["conversationId"]
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        for npc in registry.npcs:
            run.thought_days[npc.actor_id].add(1)
        run.clock.current = WorldTime(day=1, hour=16, minute=59)
    pending = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_002",
    )
    applicant_relationships_before = {
        key: deepcopy(value)
        for key, value in run.relationships.items()
        if "npc_002" in key
    }

    await service.world_step(created["runId"], 2)

    join_request_id = pending["joinRequest"]["joinRequestId"]
    assert run.join_requests[join_request_id]["status"] == "expired"
    assert run.join_requests[join_request_id]["expiredAt"] == "Day1 17:00"
    assert {
        key: value for key, value in run.relationships.items() if "npc_002" in key
    } == applicant_relationships_before
    assert run.actor_states["npc_002"]["status"] == "waiting"
    resolved = next(
        event
        for event in reversed(run.events)
        if event.event_type == "join_request_resolved"
    )
    assert resolved.payload["status"] == "expired"
    assert resolved.payload["reason"] == "new_chat_cutoff"
    assert "approverActorId" not in resolved.payload
    with pytest.raises(InvalidJoinRequestError):
        await service.respond_join_request(
            created["runId"],
            join_request_id,
            True,
        )
    with pytest.raises(InvalidInvitationError):
        await service.player_join(created["runId"], conversation_id)


@pytest.mark.anyio
async def test_join_request_rejects_full_closed_and_busy_applicant(registry) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)

    first = await service.create_run()
    with pytest.raises(InvalidConversationParticipantsError):
        await service.create_conversation(
            first["runId"],
            ["npc_001", "npc_002", "npc_003"],
        )
    full = await service.create_conversation(
        first["runId"],
        ["npc_001", "npc_002"],
    )
    await service.add_participant(
        first["runId"],
        full["conversation"]["conversationId"],
        "npc_003",
    )
    with pytest.raises(ConversationFullError):
        await service.add_participant(
            first["runId"],
            full["conversation"]["conversationId"],
            "npc_004",
        )

    second = await service.create_run()
    closed = await service.create_conversation(
        second["runId"],
        ["npc_001", "npc_002"],
    )
    await service.remove_participant(
        second["runId"],
        closed["conversation"]["conversationId"],
        "npc_002",
    )
    with pytest.raises(InvalidJoinRequestError):
        await service.add_participant(
            second["runId"],
            closed["conversation"]["conversationId"],
            "npc_003",
        )

    third = await service.create_run()
    target = await service.create_conversation(
        third["runId"],
        ["npc_001", "npc_002"],
    )
    await service.create_conversation(
        third["runId"],
        ["npc_003", "npc_004"],
    )
    with pytest.raises(ActorAlreadyInConversationError):
        await service.add_participant(
            third["runId"],
            target["conversation"]["conversationId"],
            "npc_003",
        )


@pytest.mark.anyio
async def test_daily_action_targeting_a_chat_creates_join_request(registry) -> None:
    service = RunService(registry, text_model=None, seed=19)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    applicant_id = next(
        actor_id
        for actor_id, minute in run.daily_think_minutes.items()
        if minute == 10 * 60
    )
    participants = [
        npc.actor_id for npc in registry.npcs if npc.actor_id != applicant_id
    ][:2]
    opened = await service.create_conversation(created["runId"], participants)
    goal_id = next(
        goal["goalId"]
        for goal in run.goals.values()
        if goal["ownerNpcId"] == applicant_id and goal["status"] == "active"
    )
    service.decisions.model = SeekJoinModel(
        applicant_id,
        goal_id,
        participants[0],
    )

    await service.world_step(created["runId"], 120)

    conversation = run.conversations[opened["conversation"]["conversationId"]]
    assert applicant_id in conversation.participants
    join_request = next(iter(run.join_requests.values()))
    assert join_request["applicantActorId"] == applicant_id
    assert join_request["status"] == "accepted"
    event_types = [event.event_type for event in run.events]
    assert event_types.index("join_request_created") < event_types.index(
        "conversation_participant_joined"
    )
    assert len(
        [
            event
            for event in run.events
            if event.event_type == "npc_thought_started"
            and event.payload.get("actorId") == applicant_id
        ]
    ) == 1


@pytest.mark.anyio
async def test_day7_clears_stale_pending_join_before_chapter_resolution(registry) -> None:
    model = JoinDecisionModel()
    service = RunService(registry, text_model=model)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    conversation_id = opened["conversation"]["conversationId"]
    await service.wait_for_chat_idle(created["runId"], conversation_id)
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        run.clock.current = WorldTime(day=7, hour=16, minute=59)
    pending = await service.add_participant(
        created["runId"],
        conversation_id,
        "npc_002",
    )
    applicant_relationships_before = {
        key: deepcopy(value)
        for key, value in run.relationships.items()
        if "npc_002" in key
    }
    deadline = next(
        event
        for event in registry.events
        if event.event_id == "event_day7_proposal_deadline"
    )

    async with run.lock:
        run.clock.current = WorldTime(day=7, hour=18, minute=0)
        await service._finish_chapter_locked(run, deadline)

    join_request_id = pending["joinRequest"]["joinRequestId"]
    assert run.join_requests[join_request_id]["status"] == "expired"
    assert run.conversations[conversation_id].close_reason == "chapter_deadline"
    assert run.chapter_resolution is not None
    assert "npc_002" not in run.conversations[conversation_id].participants
    assert {
        key: value for key, value in run.relationships.items() if "npc_002" in key
    } == applicant_relationships_before
    resolved_seq = next(
        event.event_seq for event in run.events if event.event_type == "chapter_resolved"
    )
    expired_seq = next(
        event.event_seq
        for event in run.events
        if event.event_type == "join_request_resolved"
        and event.payload["joinRequestId"] == join_request_id
    )
    assert expired_seq < resolved_seq
