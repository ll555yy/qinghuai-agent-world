from __future__ import annotations

import json

import pytest
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.domain.errors import WorldStepError
from core.backend.app.orchestration.run_service import RunService


class ScriptedModel:
    async def generate(self, request):
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
