from __future__ import annotations

import json

import pytest
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.simulation.runner import (
    SevenDaySimulationRunner,
    real_quality_gate_failures,
)


class OfflineWaitModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        protocol = next(
            line.split("=", 1)[1]
            for line in request.system_prompt.splitlines()
            if line.startswith("协议=")
        )
        values = {
            "DailyActionDecision": {"action": "wait"},
            "InvitationDecision": {"decision": "refuse"},
            "ChatDecision": {"result": "decided", "action": "wait"},
            "SpeechGeneration": {"text": "今天先说到这里。"},
            "SegmentSummary": {"claims": []},
            "ExitConsolidation": {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            },
        }
        return TextGenerationResult(
            text=json.dumps(values[protocol], ensure_ascii=False),
            provider="offline-test",
            model="offline-test",
        )


class OfflineAcceptModel(OfflineWaitModel):
    async def generate(self, request):
        result = await super().generate(request)
        if "协议=InvitationDecision" in request.system_prompt:
            return result.model_copy(update={"text": json.dumps({"decision": "accept"})})
        return result


@pytest.mark.anyio
async def test_offline_runner_reaches_day7_and_writes_reports(registry, tmp_path) -> None:
    model = OfflineWaitModel()
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="pro_lin",
        mode="offline",
        text_model=model,
    )

    assert report.run_id is not None
    assert report.metrics.rejected is False
    assert report.metrics.final_world_time == "Day7 18:00"
    assert report.metrics.protocol_calls["DailyActionDecision"] == 35
    assert report.metrics.scripted_actions["message_not_sent"] >= 1
    json_path, markdown_path = report.write(tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["route"] == "pro_lin"
    assert "Protocol calls" in markdown_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_route_script_uses_public_player_commands(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="pro_zhao",
        mode="offline",
        text_model=OfflineAcceptModel(),
    )

    assert report.metrics.scripted_actions["invite_sent"] == 1
    assert report.metrics.scripted_actions["message_sent"] == 1
    assert report.metrics.player_speech_count == 1
    projected = report.metrics.to_dict()
    assert "byNpc" in projected["speech"]
    assert projected["conversations"]["items"]
    assert projected["worldEvents"]["firedIds"]
    assert projected["schema"]["firstSuccessRate"] == 1.0


@pytest.mark.anyio
async def test_real_quality_gate_reports_missing_playable_evidence(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="observer",
        mode="offline",
        text_model=OfflineWaitModel(),
    )
    report.metrics.repository_recovered = True

    failures = real_quality_gate_failures(report)

    assert "day7_not_reached" not in failures
    assert "no_conversation" in failures
    assert "no_exit_consolidation" in failures
    assert "no_memory_retrieval" in failures
    assert "embedding_not_enabled" in failures


class UnconfiguredModel:
    configured = False

    async def generate(self, request):
        raise AssertionError("the unconfigured real runner must not call the model")


@pytest.mark.anyio
async def test_real_runner_rejects_without_explicit_network_or_key(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="observer",
        mode="real",
        text_model=UnconfiguredModel(),
    )

    assert report.run_id is None
    assert report.metrics.rejected is True
    assert report.metrics.rejection_reason == "network_opt_in_required"

    configured_report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="observer",
        mode="real",
        text_model=UnconfiguredModel(),
        allow_network=True,
    )
    assert configured_report.run_id is None
    assert configured_report.metrics.rejection_reason == "model_not_configured"
