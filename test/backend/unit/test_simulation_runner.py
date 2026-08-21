from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.simulation.runner import (
    ROUTE_PLAYER_MESSAGES,
    SevenDaySimulationRunner,
    SimulationReport,
    _safe_exception_label,
    real_quality_gate_failures,
)


def test_safe_exception_label_includes_only_valid_constraint_names() -> None:
    safe = RuntimeError("hidden database values")
    safe.orig = SimpleNamespace(  # type: ignore[attr-defined]
        diag=SimpleNamespace(constraint_name="pk_memory_evidence_messages")
    )
    assert (
        _safe_exception_label(safe)
        == "RuntimeError:pk_memory_evidence_messages"
    )
    unsafe = RuntimeError("hidden database values")
    unsafe.orig = SimpleNamespace(  # type: ignore[attr-defined]
        diag=SimpleNamespace(constraint_name="bad value=private")
    )
    assert _safe_exception_label(unsafe) == "RuntimeError"


def test_support_routes_ask_for_an_explicit_but_unbiased_stance() -> None:
    for route in ("pro_lin", "pro_zhao"):
        messages = ROUTE_PLAYER_MESSAGES[route]
        assert messages is not None
        history_message, stance_message = messages
        assert "过去" in history_message
        assert "旧事" in history_message or "分歧" in history_message
        if route == "pro_lin":
            assert "具体承诺" in stance_message
        else:
            assert "请直接表态" in stance_message
        assert "是否愿意支持" in stance_message
        assert "支持、附条件支持，还是反对" in stance_message


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


class OfflineRouteStanceModel(OfflineAcceptModel):
    async def generate(self, request):
        result = await super().generate(request)
        if (
            "协议=ChatDecision" in request.system_prompt
            and "是否愿意支持" in request.messages[0].content
        ):
            return result.model_copy(
                update={
                    "text": json.dumps(
                        {
                            "result": "decided",
                            "action": "speak",
                            "responseDesire": 3,
                            "intent": "明确回答自己是否支持提交联合方案",
                            "chapterEffects": [
                                {
                                    "kind": "overall_stance",
                                    "value": "conditional",
                                    "evidenceMessageIds": [],
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                }
            )
        if (
            "协议=SpeechGeneration" in request.system_prompt
            and "明确回答自己是否支持提交联合方案"
            in request.messages[0].content
        ):
            return result.model_copy(
                update={"text": json.dumps({"text": "方案守住底线，我就支持提交。"})}
            )
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

    assert report.metrics.scripted_actions["invite_sent"] == 2
    assert report.metrics.scripted_actions["message_sent"] == 2
    assert report.metrics.scripted_actions["history_message_sent"] == 1
    assert report.metrics.scripted_actions["stance_message_sent"] == 1
    assert report.metrics.player_speech_count == 2
    projected = report.metrics.to_dict()
    assert "byNpc" in projected["speech"]
    assert projected["conversations"]["items"]
    assert projected["worldEvents"]["firedIds"]
    assert projected["schema"]["firstSuccessRate"] == 1.0
    failures = real_quality_gate_failures(report)
    assert "player_message_not_sent" not in failures
    assert "no_chapter_stance_change" in failures
    assert "player_result_missing" not in failures


@pytest.mark.anyio
async def test_route_stance_is_committed_from_generated_npc_speech(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="pro_lin",
        mode="offline",
        text_model=OfflineRouteStanceModel(),
    )

    assert report.metrics.player_speech_count == 2
    assert report.metrics.chapter_stance_changes >= 1
    assert report.metrics.chapter_stances["npc_001"] == "conditional"


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

    real_report = SimulationReport(
        report.route,
        "real",
        report.seed,
        report.metrics,
        report.budget,
        report.run_id,
    )
    assert "temporary_run_not_deleted" in real_quality_gate_failures(real_report)
    real_report.metrics.temporary_run_deleted = True
    assert "temporary_run_not_deleted" not in real_quality_gate_failures(real_report)
    assert real_report.metrics.to_dict()["temporaryRunDeleted"] is True


@pytest.mark.anyio
async def test_real_quality_gate_counts_private_fired_events(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="observer",
        mode="offline",
        text_model=OfflineWaitModel(),
    )
    report.metrics.events["world_event_occurred"] = 5
    report.metrics.skipped_world_event_ids = []

    assert "world_events_incomplete" not in real_quality_gate_failures(report)


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
