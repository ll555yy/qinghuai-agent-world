from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.simulation.manifest import (
    AttemptLedger,
    load_manifest,
    planned_attempts,
)
from core.backend.app.simulation.runner import (
    PRO_LIN_V2_STEPS,
    PRO_LIN_V3_STEPS,
    ROUTE_PLAYER_MESSAGES,
    ROUTE_PLAYER_STEPS,
    SevenDaySimulationRunner,
    SimulationReport,
    _safe_exception_label,
    player_strategy_steps,
    real_quality_gate_failures,
)
from core.backend.scripts import run_seven_day_simulation as simulation_cli


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
        assert messages
        history_message = messages[0]
        stance_message = messages[-1]
        assert any(cue in history_message for cue in ("过去", "以前"))
        assert any(cue in history_message for cue in ("旧事", "分歧", "顾虑"))
        if route == "pro_lin":
            assert "请你最后明确" in stance_message
            assert "授权" in stance_message
            assert "支持或附条件支持" in stance_message
        else:
            assert "请直接表态" in stance_message
            assert "是否愿意支持" in stance_message
            assert "支持、附条件支持，还是反对" in stance_message


def test_coalition_route_contacts_all_npcs_without_private_state_branching() -> None:
    steps = ROUTE_PLAYER_STEPS["pro_lin"]

    assert [step.day for step in steps] == list(range(1, 8))
    assert {step.target_actor_id for step in steps} == {
        "npc_001",
        "npc_002",
        "npc_003",
        "npc_004",
        "npc_005",
    }
    assert sum(step.target_actor_id == "npc_005" for step in steps) == 2
    assert "授权" in steps[-1].message


def test_coalition_v2_preserves_v1_history_and_closes_satisfied_conditions() -> None:
    v1 = ROUTE_PLAYER_STEPS["pro_lin"]
    v2 = PRO_LIN_V2_STEPS

    assert v2[:-1] == v1[:-1]
    assert v2[-1] != v1[-1]
    assert v2[-1].day == 7
    assert v2[-1].target_actor_id == "npc_005"
    assert "已经写入" in v2[-1].message
    assert "不要把已经满足的条件" in v2[-1].message
    assert "只有仍有具体未满足事项时" in v2[-1].message
    assert player_strategy_steps("pro_lin", "strategy.pro_lin.v1") == v1
    assert player_strategy_steps("pro_lin", "strategy.pro_lin.v2") == v2


def test_coalition_v3_separates_agenda_support_from_submission_authorization() -> None:
    v1 = ROUTE_PLAYER_STEPS["pro_lin"]
    v3 = PRO_LIN_V3_STEPS

    assert v3[:-2] == v1[:-1]
    assert len(v3) == len(v1) + 1
    agenda_step, authorization_step = v3[-2:]
    assert agenda_step.day == authorization_step.day == 7
    assert agenda_step.target_actor_id == authorization_step.target_actor_id == "npc_005"
    assert "先只确认青槐文社议案本身" in agenda_step.message
    assert "无附加条件支持青槐文社作为核心议案" in agenda_step.message
    assert "现在单独确认截止日提交权限" in authorization_step.message
    assert "批准并授权今天正式提交联合方案" in authorization_step.message
    assert "不是未来承诺" in agenda_step.message
    assert "不要再次附加已经写入" in authorization_step.message
    assert player_strategy_steps("pro_lin", "strategy.pro_lin.v3") == v3


def test_strategy_version_must_match_route() -> None:
    with pytest.raises(ValueError, match="does not match route"):
        player_strategy_steps("observer", "strategy.pro_lin.v2")


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


class OfflineV3CoalitionModel(OfflineAcceptModel):
    async def generate(self, request):
        result = await super().generate(request)
        visible_text = "\n".join(message.content for message in request.messages)
        if "协议=ChatDecision" in request.system_prompt and any(
            marker in visible_text
            for marker in (
                "青槐文社成为核心主张",
                "以青槐文社为核心",
                "青槐文社主张",
                "青槐文社这项核心主张",
                "明确支持整体提交和青槐文社",
                "先只确认青槐文社议案本身",
                "现在单独确认截止日提交权限",
            )
        ):
            effects = [
                {"kind": "overall_stance", "value": "support", "evidenceMessageIds": []},
                {
                    "kind": "agenda_stance",
                    "agendaId": "agenda_001_literary_society",
                    "value": "support",
                    "evidenceMessageIds": [],
                },
            ]
            if "现在单独确认截止日提交权限" in visible_text:
                effects.append(
                    {
                        "kind": "zhou_authorization",
                        "value": "approved",
                        "evidenceMessageIds": [],
                    }
                )
            return result.model_copy(
                update={
                    "text": json.dumps(
                        {
                            "result": "decided",
                            "action": "speak",
                            "responseDesire": 3,
                            "intent": "分别确认议案支持与提交授权",
                            "chapterEffects": effects,
                        },
                        ensure_ascii=False,
                    )
                }
            )
        if (
            "协议=SpeechGeneration" in request.system_prompt
            and "分别确认议案支持与提交授权" in visible_text
        ):
            return result.model_copy(
                update={"text": json.dumps({"text": "我确认支持，并按约定授权提交。"})}
            )
        return result


@pytest.mark.anyio
async def test_coalition_v3_reaches_state_level_completed_result(registry) -> None:
    report = await SevenDaySimulationRunner(registry, seed=73).run(
        route="pro_lin",
        mode="offline",
        text_model=OfflineV3CoalitionModel(),
        attempt={
            "attemptId": "offline-v3:pro_lin:73",
            "strategyId": "strategy.pro_lin.v3",
        },
    )

    assert report.metrics.scripted_actions["strategy_step_sent"] == 8
    assert report.metrics.chapter_stances["npc_005"] == "support"
    assert report.metrics.chapter_stances["zhouAuthorization"] == "approved"
    assert report.metrics.agenda_results["agenda_001_literary_society"] == "core_adopted"
    assert report.metrics.final_day7_branch == "consensus_submitted"
    assert report.metrics.player_result == "completed"


@pytest.mark.anyio
async def test_attempt_checkpoint_is_atomic_and_uses_safe_identity(registry, tmp_path) -> None:
    original = await SevenDaySimulationRunner(registry, seed=37).run(
        route="observer",
        mode="offline",
        text_model=OfflineWaitModel(),
    )
    report = SimulationReport(
        original.route,
        original.mode,
        original.seed,
        original.metrics,
        original.budget,
        original.run_id,
        "experiment:observer:37",
        "completed",
        "a" * 64,
    )

    json_path, markdown_path = simulation_cli._write_attempt_checkpoint(
        report, tmp_path
    )

    assert json_path.name == "experiment_observer_37.json"
    assert json.loads(json_path.read_text(encoding="utf-8"))["attemptId"] == report.attempt_id
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# Qinghuai seven-day simulation report"
    )
    assert not list((tmp_path / "attempt_reports").glob("*.tmp"))


def test_resume_plan_reuses_completed_and_terminalizes_stale_started(tmp_path) -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)[:3]
    ledger = AttemptLedger(
        tmp_path / "attempts",
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=planned,
    )
    ledger.prepare()
    ledger.start(planned[0])
    ledger.attach_run(planned[0], "run_completed")
    ledger.finish(
        planned[0],
        "completed",
        run_id="run_completed",
        infra_valid=True,
        gameplay_pass=True,
    )
    simulation_cli._write_resume_checkpoint(
        {
            "attemptId": planned[0]["attemptId"],
            "attemptStatus": "completed",
            "manifestDigest": digest,
            "runId": "run_completed",
            "metrics": {"physicalProviderRequests": 7},
        },
        tmp_path,
    )
    ledger.start(planned[1])
    ledger.attach_run(planned[1], "run_interrupted")

    pending, resumed, stale_run_ids = simulation_cli._resume_manifest_plan(
        planned,
        attempt_ledger=ledger,
        output=tmp_path,
        manifest_digest=digest,
    )

    assert pending == (planned[2],)
    assert [item["attemptId"] for item in resumed] == [planned[0]["attemptId"]]
    assert stale_run_ids == {"run_completed", "run_interrupted"}
    interrupted = ledger.get(planned[1]["attemptId"])
    assert interrupted["status"] == "runner_failed"
    assert interrupted["reason"] == "stale_started_attempt_on_resume"
    assert interrupted["runId"] == "run_interrupted"


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


class OfflineCoalitionModel(OfflineAcceptModel):
    async def generate(self, request):
        result = await super().generate(request)
        payload = json.loads(request.messages[0].content)
        actor_id = payload.get("actor", {}).get("actorId")
        if "协议=ChatDecision" in request.system_prompt:
            effects = [
                {
                    "kind": "overall_stance",
                    "value": "conditional",
                    "evidenceMessageIds": [],
                },
                {
                    "kind": "agenda_stance",
                    "agendaId": "agenda_001_literary_society",
                    "value": "conditional",
                    "evidenceMessageIds": [],
                },
            ]
            if actor_id == "npc_005":
                effects.append(
                    {
                        "kind": "zhou_authorization",
                        "value": "conditional",
                        "evidenceMessageIds": [],
                    }
                )
            return result.model_copy(
                update={
                    "text": json.dumps(
                        {
                            "result": "decided",
                            "action": "speak",
                            "responseDesire": 3,
                            "intent": "明确回答整体、青槐文社及授权立场",
                            "chapterEffects": effects,
                        },
                        ensure_ascii=False,
                    )
                }
            )
        if "协议=SpeechGeneration" in request.system_prompt:
            text = "守住公开透明和旧书保护这些条件，我支持整体提交，也附条件支持青槐文社。"
            if actor_id == "npc_005":
                text += "我对正式提交给出附条件授权。"
            return result.model_copy(
                update={"text": json.dumps({"text": text}, ensure_ascii=False)}
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
    assert report.metrics.scripted_actions["strategy_step_sent"] == 2
    assert report.metrics.scripted_actions["player_left"] == 2
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

    assert report.metrics.player_speech_count == 7
    assert report.metrics.chapter_stance_changes >= 1
    assert report.metrics.chapter_stances["npc_001"] == "conditional"


@pytest.mark.anyio
async def test_coalition_route_can_reach_compromise_without_runner_stance_writes(
    registry,
) -> None:
    report = await SevenDaySimulationRunner(registry, seed=37).run(
        route="pro_lin",
        mode="offline",
        text_model=OfflineCoalitionModel(),
    )

    assert report.metrics.player_speech_count == 7
    assert report.metrics.chapter_stance_changes == 5
    assert report.metrics.chapter_stances["zhouAuthorization"] == "conditional"
    assert report.metrics.final_day7_branch == "compromise_submitted"
    assert report.metrics.player_result == "partial"
    failures = real_quality_gate_failures(report)
    assert "success_branch_not_reached" not in failures
    assert "coalition_not_formed" not in failures
    assert "support_task_not_completed" not in failures


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


class RunCreationFailureService:
    def __init__(self) -> None:
        self.decisions = SimpleNamespace(model=None)

    async def create_run(self, agenda_id, *, seed):
        raise RuntimeError("database is unavailable")


@pytest.mark.anyio
async def test_preregistered_attempt_is_terminal_when_run_creation_fails(registry, tmp_path) -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    ledger = AttemptLedger(
        tmp_path,
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=planned,
    )
    ledger.prepare()

    report = await SevenDaySimulationRunner(registry, seed=planned[0]["seed"]).run(
        route="observer",
        mode="offline",
        service=RunCreationFailureService(),
        attempt_ledger=ledger,
        attempt=planned[0],
    )

    assert report.run_id is None
    assert report.attempt_id == planned[0]["attemptId"]
    assert report.attempt_status == "runner_failed"
    record = ledger.get(planned[0]["attemptId"])
    assert record["status"] == "runner_failed"
    assert record["startedAt"]
    assert record["terminalAt"]


@pytest.mark.anyio
async def test_preregistered_attempt_binds_created_run_to_ledger(registry, tmp_path) -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    ledger = AttemptLedger(
        tmp_path,
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=planned,
    )
    ledger.prepare()

    report = await SevenDaySimulationRunner(registry, seed=planned[0]["seed"]).run(
        route="observer",
        mode="offline",
        text_model=OfflineWaitModel(),
        attempt_ledger=ledger,
        attempt=planned[0],
        manifest_digest=digest,
    )

    record = ledger.get(planned[0]["attemptId"])
    assert report.attempt_status == "completed"
    assert report.run_id
    assert record["status"] == "completed"
    assert record["runId"] == report.run_id


@pytest.mark.anyio
async def test_preregistered_provider_preflight_failure_is_not_started(registry, tmp_path) -> None:
    manifest, digest = load_manifest()
    planned = planned_attempts(manifest)
    ledger = AttemptLedger(
        tmp_path,
        experiment_id=manifest["experimentId"],
        manifest_digest=digest,
        planned=planned,
    )
    ledger.prepare()

    report = await SevenDaySimulationRunner(registry, seed=planned[0]["seed"]).run(
        route="observer",
        mode="real",
        text_model=UnconfiguredModel(),
        allow_network=True,
        attempt_ledger=ledger,
        attempt=planned[0],
    )

    assert report.attempt_status == "not_started"
    record = ledger.get(planned[0]["attemptId"])
    assert record["status"] == "not_started"
    assert record["terminalAt"]
