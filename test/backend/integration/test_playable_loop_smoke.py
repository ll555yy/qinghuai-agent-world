from __future__ import annotations

import json

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.errors import WorldStepError
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.speech_example_retriever import (
    SpeechExampleHit,
    SpeechExampleSearchResult,
)


class ScriptedModel:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def generate(self, request):
        self.requests.append(request)
        if "DailyActionDecision" in request.system_prompt:
            context = json.loads(request.messages[0].content)
            actor_id = context["actor"]["actorId"]
            goal_id = context["goals"][0]["goalId"]
            target = "npc_005" if actor_id != "npc_005" else "npc_001"
            text = {"action": "seek_chat", "goalId": goal_id, "targetActorId": target}
        elif "InvitationDecision" in request.system_prompt:
            text = {"decision": "accept"}
        elif "SpeechGeneration" in request.system_prompt:
            text = {"text": "你好，聊聊书店吧。"}
        elif "ChatDecision" in request.system_prompt:
            text = {"result": "decided", "action": "wait"}
        elif "SegmentSummary" in request.system_prompt:
            text = {"claims": []}
        else:
            text = {"memories": [], "goalUpdates": [], "relationshipUpdates": [], "newShortGoals": [], "chapterEffects": []}
        return TextGenerationResult(text=json.dumps(text), provider="fake", model="fake")


class OneExampleRetriever:
    def __init__(self, registry) -> None:
        self.registry = registry
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, *, npc_id: str, intent: str, limit: int = 3):
        self.calls.append((npc_id, intent, limit))
        example = next(
            item
            for item in self.registry.speech_examples.values()
            if item.npc_id == npc_id
        )
        return SpeechExampleSearchResult(
            hits=(SpeechExampleHit(example=example, similarity=1.0),)
        )


@pytest.mark.anyio
async def test_world_step_runs_daily_action_and_invitation(registry) -> None:
    service = RunService(registry, text_model=ScriptedModel(), seed=1)
    created = await service.create_run()
    assert created["worldEvents"][0]["eventId"] == "event_day1_recovery_notice"
    run_id = created["runId"]
    stepped = await service.world_step(run_id, 240)
    run = await service.get_run(run_id)
    assert stepped["worldTime"]["label"] == "Day1 11:00"
    assert any(event.event_type == "invitation_requested" for event in (await service.get_run_entity(run_id)).events)
    assert run["conversations"]


@pytest.mark.anyio
async def test_playable_opener_injects_intent_selected_example(registry) -> None:
    model = ScriptedModel()
    retriever = OneExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        seed=1,
        chat_publish_delay_min_seconds=0,
        chat_publish_delay_max_seconds=0,
    )
    created = await service.create_run()
    await service.world_step(created["runId"], 240)
    run = await service.get_run_entity(created["runId"])
    for conversation_id in tuple(run.conversations):
        await service.wait_for_chat_idle(run.run_id, conversation_id)

    assert retriever.calls
    speaking_npc_id, final_intent, limit = retriever.calls[0]
    assert final_intent
    assert limit == 3
    speech_payload = next(
        json.loads(request.messages[0].content)
        for request in model.requests
        if "协议=SpeechGeneration" in request.system_prompt
        and json.loads(request.messages[0].content)["actor"]["actorId"]
        == speaking_npc_id
    )
    expected_example = next(
        item
        for item in registry.speech_examples.values()
        if item.npc_id == speaking_npc_id
    )
    assert speech_payload["context"]["speechExamples"] == [
        {
            "situation": expected_example.situation,
            "intendedMove": expected_example.intended_move,
            "reply": expected_example.reply,
        }
    ]
    await service.close()


@pytest.mark.anyio
async def test_playable_opener_without_retriever_keeps_speaking(registry) -> None:
    model = ScriptedModel()
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=None,
        seed=1,
        chat_publish_delay_min_seconds=0,
        chat_publish_delay_max_seconds=0,
    )
    created = await service.create_run()
    await service.world_step(created["runId"], 240)
    run = await service.get_run_entity(created["runId"])
    for conversation_id in tuple(run.conversations):
        await service.wait_for_chat_idle(run.run_id, conversation_id)

    speech_request = next(
        request
        for request in model.requests
        if "协议=SpeechGeneration" in request.system_prompt
    )
    speech_payload = json.loads(speech_request.messages[0].content)
    assert speech_payload["context"]["speechExamples"] == []
    assert any(
        message.get("authorActorId", "").startswith("npc_")
        for messages in run.messages.values()
        for message in messages
    )
    await service.close()


@pytest.mark.anyio
async def test_world_step_reaches_day7_without_model(registry) -> None:
    service = RunService(registry, text_model=None, seed=2)
    created = await service.create_run()
    result = await service.world_step(created["runId"], 20000)
    assert result["worldTime"]["label"] == "Day7 18:00"
    assert result["worldTime"]["status"] == "chapter_ended"
    assert result["run"]["chapterResolution"] is not None
    assert result["run"]["chapterResolution"]["branch"] == "no_submission"


@pytest.mark.anyio
async def test_step_at_day_end_does_not_skip_overnight(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()
    at_end = await service.world_step(created["runId"], 1080)
    assert at_end["worldTime"]["label"] == "Day1 18:00"
    next_day = await service.world_step(created["runId"], 2)
    assert next_day["worldTime"]["label"] == "Day2 08:01"


@pytest.mark.anyio
async def test_world_step_requires_complete_two_second_ticks(registry) -> None:
    service = RunService(registry, text_model=None)
    created = await service.create_run()

    with pytest.raises(WorldStepError, match="multiple of 2"):
        await service.world_step(created["runId"], 1)

    stepped = await service.world_step(created["runId"], 2)
    assert stepped["advancedMinutes"] == 1
    assert stepped["worldTime"]["label"] == "Day1 09:01"


def test_new_routes_keep_private_state_out_of_public_payload(client) -> None:
    run_id = client.post("/api/runs", json={}).json()["runId"]
    stepped = client.post(f"/api/runs/{run_id}/world/step", json={"realSeconds": 2})
    assert stepped.status_code == 200
    assert "coreSecrets" not in str(stepped.json())
    actor = client.get(f"/api/runs/{run_id}/actors/npc_001")
    assert actor.status_code == 200
    assert "coreSecrets" not in str(actor.json())
    assert client.get(f"/api/runs/{run_id}/agendas").status_code == 200
