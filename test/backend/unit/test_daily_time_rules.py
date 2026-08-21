from __future__ import annotations

import json
from copy import deepcopy

import pytest
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.clock import WorldTime
from core.backend.app.domain.errors import InvalidInvitationError
from core.backend.app.orchestration.run_service import INITIAL_MEMORY_CACHE_LIMIT, RunService


class RecordingWaitModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def generate(self, request):
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        context = json.loads(request.messages[0].content)
        self.calls.append((protocol, context))
        if protocol == "DailyActionDecision":
            value = {"action": "wait"}
        elif protocol == "InvitationDecision":
            value = {"decision": "refuse"}
        elif protocol == "ChatDecision":
            value = {"result": "decided", "action": "wait"}
        elif protocol == "SpeechGeneration":
            value = {"text": "今天先说到这里。"}
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
            text=json.dumps(value, ensure_ascii=False),
            provider="fake",
            model="offline",
        )


@pytest.mark.anyio
async def test_initial_memory_cache_is_bounded_and_prior_partners_are_counted(
    registry,
) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    await service.create_conversation(
        run.run_id,
        ["npc_001", registry.player_actor_id],
    )
    async with run.lock:
        for index in range(10):
            memory_id = f"memory_test_cache_{index}"
            run.memories[memory_id] = {
                "memoryId": memory_id,
                "ownerNpcId": "npc_001",
                "actorIds": [registry.player_actor_id],
                "topicIds": [],
                "importance": 1,
            }

        selected = service._initial_memory_ids(
            run,
            "npc_001",
            [registry.player_actor_id],
        )
        counts = service._prior_conversation_counts(run, "npc_001")

    assert len(selected) == INITIAL_MEMORY_CACHE_LIMIT
    assert counts == {registry.player_actor_id: 1}


@pytest.mark.anyio
async def test_seeded_schedule_rotates_left_with_compressed_slots(registry) -> None:
    first_service = RunService(registry, text_model=None, seed=37)
    first_snapshot = await first_service.create_run()
    first = await first_service.get_run_entity(first_snapshot["runId"])

    second_service = RunService(registry, text_model=None, seed=37)
    second_snapshot = await second_service.create_run()
    second = await second_service.get_run_entity(second_snapshot["runId"])

    assert first.daily_think_order == second.daily_think_order
    assert first.daily_think_schedule == second.daily_think_schedule
    expected_slots = [9 * 60, 10 * 60, 11 * 60, 12 * 60, 13 * 60]
    baseline = first.daily_think_order
    for day in range(1, 8):
        schedule = first.daily_think_schedule[day]
        assert sorted(schedule.values()) == expected_slots
        order = [actor_id for actor_id, _ in sorted(schedule.items(), key=lambda item: item[1])]
        shift = (day - 1) % len(baseline)
        assert order == baseline[shift:] + baseline[:shift]

    await first_service.world_step(first.run_id, 1200)
    day_two_thoughts = [
        event
        for event in first.events
        if event.event_type == "npc_thought_started"
        and event.payload["worldTime"]["day"] == 2
    ]
    assert [event.payload["actorId"] for event in day_two_thoughts] == [baseline[1]]


@pytest.mark.anyio
async def test_busy_npc_skips_its_only_daily_action_without_catchup(registry) -> None:
    service = RunService(registry, text_model=None, seed=11)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    busy_npc = next(
        actor_id for actor_id, minute in run.daily_think_minutes.items() if minute == 10 * 60
    )
    opened = await service.create_conversation(
        run.run_id,
        [busy_npc, registry.player_actor_id],
    )

    await service.world_step(run.run_id, 9 * 60 * 2)

    skipped = [
        event
        for event in run.events
        if event.event_type == "npc_thought_skipped"
        and event.payload.get("actorId") == busy_npc
        and event.payload["worldTime"]["day"] == 1
    ]
    started = [
        event
        for event in run.events
        if event.event_type == "npc_thought_started"
        and event.payload.get("actorId") == busy_npc
        and event.payload["worldTime"]["day"] == 1
    ]
    assert len(skipped) == 1
    assert started == []
    assert run.conversations[opened["conversation"]["conversationId"]].close_reason == "day_end"


@pytest.mark.anyio
async def test_time_policy_changes_without_creating_an_extra_daily_call(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    model = RecordingWaitModel()
    service.decisions.model = model
    async with run.lock:
        for npc in registry.npcs:
            run.thought_days[npc.actor_id].add(1)
        run.clock.current = WorldTime(day=1, hour=16, minute=59)
        before = json.loads(
            service._npc_prompt(run, "npc_001", "chat_decision", {"memoryCache": []})
        )
    assert before["timePolicy"] == {
        "worldTime": "Day1 16:59",
        "dayEnd": "18:00",
        "newChatCutoff": "17:00",
        "remainingMinutes": 61,
        "newChatAllowed": True,
        "closingSoon": False,
    }

    await service.world_step(run.run_id, 2)
    assert model.calls == []
    async with run.lock:
        at_cutoff = json.loads(
            service._npc_prompt(run, "npc_001", "speech", {"memoryCache": []})
        )
    assert at_cutoff["timePolicy"]["remainingMinutes"] == 60
    assert at_cutoff["timePolicy"]["newChatAllowed"] is False
    assert at_cutoff["timePolicy"]["closingSoon"] is True


@pytest.mark.anyio
async def test_chat_and_exit_model_calls_receive_current_time_policy(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", registry.player_actor_id],
    )
    conversation_id = opened["conversation"]["conversationId"]
    run = await service.get_run_entity(created["runId"])
    model = RecordingWaitModel()
    service.decisions.model = model
    async with run.lock:
        for npc in registry.npcs:
            run.thought_days[npc.actor_id].add(1)
        run.clock.current = WorldTime(day=1, hour=17, minute=10)

    await service.player_message(run.run_id, conversation_id, "今天还能再确认一件事吗？")
    assert [protocol for protocol, _ in model.calls] == ["ChatDecision"]
    chat_policy = model.calls[0][1]["timePolicy"]
    assert chat_policy["worldTime"] == "Day1 17:10"
    assert chat_policy["remainingMinutes"] == 50
    assert chat_policy["newChatAllowed"] is False
    assert chat_policy["closingSoon"] is True
    chat_chapter = model.calls[0][1]["chapterContext"]
    assert len(chat_chapter["agendas"]) == 5
    assert set(chat_chapter["ownAgendaStances"].values()) == {"unknown"}
    assert chat_chapter["canSetZhouAuthorization"] is False

    await service.world_step(run.run_id, 100)
    exit_context = next(
        context for protocol, context in model.calls if protocol == "ExitConsolidation"
    )
    assert exit_context["timePolicy"] == {
        "worldTime": "Day1 18:00",
        "dayEnd": "18:00",
        "newChatCutoff": "17:00",
        "remainingMinutes": 0,
        "newChatAllowed": False,
        "closingSoon": True,
    }
    assert len(exit_context["chapterContext"]["agendas"]) == 5
    assert exit_context["chapterContext"]["ownOverallStance"] == "unknown"


@pytest.mark.anyio
async def test_cutoff_expires_pending_player_response_without_relation_change(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        for npc in registry.npcs:
            run.thought_days[npc.actor_id].add(1)
        run.clock.current = WorldTime(day=1, hour=16, minute=59)
        invitation = await service._request_invitation_locked(
            run,
            "npc_001",
            registry.player_actor_id,
            private_goal_id="goal_001_public",
        )
        relations_before = deepcopy(run.relationships)

    await service.world_step(run.run_id, 2)

    assert invitation["status"] == "expired"
    assert invitation["expiredAt"] == "Day1 17:00"
    assert run.relationships == relations_before
    assert not any(
        event.event_type == "invitation_refused"
        and event.payload.get("invitationId") == invitation["invitationId"]
        for event in run.events
    )
    with pytest.raises(InvalidInvitationError):
        await service.player_invite(run.run_id, "npc_002")
    with pytest.raises(InvalidInvitationError):
        await service.create_conversation(run.run_id, ["npc_002", "npc_003"])


@pytest.mark.anyio
async def test_existing_chat_continues_at_cutoff_and_closes_once_at_day_end(registry) -> None:
    service = RunService(registry, text_model=None)
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
        run.clock.current = WorldTime(day=1, hour=17, minute=0)

    result = await service.player_message(run.run_id, conversation_id, "今天先把重点说清楚。")
    assert result["conversation"]["status"] == "open"

    await service.world_step(run.run_id, 120)
    conversation = run.conversations[conversation_id]
    assert conversation.status == "closed"
    assert conversation.close_reason == "day_end"
    consolidated_before = [
        event for event in run.events if event.event_type == "npc_consolidated"
    ]
    ended_before = [event for event in run.events if event.event_type == "world_day_ended"]
    assert len(consolidated_before) == 1
    assert len(ended_before) == 1
    assert not any(key[0] == conversation_id for key in run.memory_cache)

    async with run.lock:
        await service._process_due_locked(run)
    assert len([event for event in run.events if event.event_type == "npc_consolidated"]) == 1
    assert len([event for event in run.events if event.event_type == "world_day_ended"]) == 1


@pytest.mark.anyio
async def test_day7_consolidates_before_chapter_resolution(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"],
        ["npc_001", "npc_002"],
    )
    run = await service.get_run_entity(created["runId"])
    deadline = next(
        event for event in registry.events if event.event_id == "event_day7_proposal_deadline"
    )
    async with run.lock:
        run.clock.current = WorldTime(day=7, hour=18, minute=0)
        await service._finish_chapter_locked(run, deadline)

    conversation = run.conversations[opened["conversation"]["conversationId"]]
    assert conversation.close_reason == "chapter_deadline"
    resolution_seq = next(
        event.event_seq for event in run.events if event.event_type == "chapter_resolved"
    )
    consolidated = [
        event.event_seq for event in run.events if event.event_type == "npc_consolidated"
    ]
    assert len(consolidated) == 2
    assert max(consolidated) < resolution_seq
