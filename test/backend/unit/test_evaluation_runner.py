from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from core.backend.app.ai.models import TextGenerationResult, TokenUsage
from core.backend.app.evaluation.judge import FakeJudge as StrictFakeJudge
from core.backend.app.evaluation.models import EvaluationCase
from core.backend.app.evaluation.runner import (
    ArkCandidateAdapter,
    EvaluationBudget,
    EvaluationExecution,
    EvaluationRunner,
    FakeCandidate,
    FakeEmbedding,
    FakeJudge,
    _build_observation,
    _candidate_prompt_payload,
)
from core.backend.scripts import resume_agent_semantic_calibration as calibration_resume
from core.backend.scripts import run_agent_semantic_evaluation as evaluation_cli


class _RecordingCandidateClient:
    configured = True

    def __init__(self) -> None:
        self.settings = SimpleNamespace(model="doubao-seed-2.0-lite")
        self.requests = []
        self.attempts = 0

    async def generate(self, request):
        self.requests.append(request)
        self.attempts += 1
        return TextGenerationResult(
            text='{"text":"我先核实公开条件。"}',
            provider="fake",
            model="doubao-seed-2.0-lite",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
        )

    def metrics_snapshot(self):
        return {"providerAttempts": self.attempts, "providerRetries": 0}


class _FailingCandidate:
    async def generate(self, case, run_index=0):
        raise RuntimeError("synthetic provider failure")


class _MeteredCandidate:
    async def generate(self, case, run_index=0):
        return {
            "text": '{"action":"wait"}',
            "output": {"action": "wait"},
            "usage": {
                "prompt_tokens": 1_000,
                "completion_tokens": 1_000,
                "total_tokens": 2_000,
            },
        }


class _SlowCandidate:
    async def generate(self, case, run_index=0):
        await asyncio.sleep(0.05)
        return {"text": '{"action":"wait"}', "output": {"action": "wait"}}


class _HighJudge:
    async def score(self, case, observation, *, attempt=0):
        return {
            "persona_consistency": 5,
            "context_faithfulness": 5,
            "response_relevance": 5,
            "naturalness": 5,
            "goal_progress": 5,
            "player_agency": 5,
            "contradiction_detected": False,
            "unsupported_claim_detected": False,
            "direct_question_answered": True,
            "major_issues": [],
            "evidence": {},
            "confidence": "high",
        }


class _DisagreeingJudge:
    def __init__(self) -> None:
        self.calls = 0

    async def score(self, case, observation, *, attempt=0):
        self.calls += 1
        value = 1 if self.calls % 2 else 5
        return {
            "persona_consistency": value,
            "context_faithfulness": value,
            "response_relevance": value,
            "naturalness": value,
            "goal_progress": value,
            "player_agency": value,
            "contradiction_detected": False,
            "unsupported_claim_detected": False,
            "direct_question_answered": True,
            "major_issues": [],
            "evidence": {},
            "confidence": "high",
        }


def _case(
    case_id: str,
    *,
    category: str = "persona",
    protocol: str = "daily_action",
    context: dict[str, object] | None = None,
    forbidden: list[str] | None = None,
    requires_postgres: bool = False,
    requires_live_candidate: bool = False,
    requires_live_embedding: bool = False,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        case_version=1,
        category=category,  # type: ignore[arg-type]
        protocol=protocol,  # type: ignore[arg-type]
        npc_id="npc_001",
        input_context=context or {},
        expected_constraints=[],
        forbidden_signals=forbidden or [],
        allowed_outcomes=["wait"],
        expected_memory_ids=[],
        allowed_evidence_message_ids=[],
        requires_postgres=requires_postgres,
        requires_live_candidate=requires_live_candidate,
        requires_live_embedding=requires_live_embedding,
        judge_rubric=["answer the case"],
        tags=[],
    )


def test_dry_run_validates_and_plans_without_constructing_or_calling_adapters() -> None:
    candidate = FakeCandidate()
    report = asyncio.run(
        EvaluationRunner(
            [_case("b"), _case("a", category="memory")],
            mode="dry-run",
            candidate=candidate,
            enable_judge=False,
        ).run()
    )

    assert report["execution"]["complete"] is True
    assert report["execution"]["plannedCalls"]["candidate"] == 2
    assert report["execution"]["candidateCalls"] == 0
    assert candidate.calls == 0
    assert report["cases"] == []


def test_budget_uses_separate_candidate_and_judge_rate_cards() -> None:
    runner = EvaluationRunner(
        [_case("rate-card")],
        mode="dry-run",
        enable_judge=True,
        budget=EvaluationBudget(candidate_repetitions=1, judge_repetitions=1),
        judge_repeat_sample_rate=0,
    )

    assert runner.planned_calls()["worstCaseEstimatedCostCny"] == 0.0183
    execution = EvaluationExecution(mode="offline")
    runner._record_usage(
        execution,
        "candidate",
        {"usage": {"prompt_tokens": 1_000, "completion_tokens": 1_000}},
    )
    runner._record_usage(
        execution,
        "judge",
        {"metrics": {"prompt_tokens": 1_000, "completion_tokens": 1_000}},
    )
    assert execution.estimated_cost_cny == pytest.approx(0.0222)


def test_cli_dry_run_and_offline_cannot_construct_live_adapters_or_open_network(
    monkeypatch,
    tmp_path,
) -> None:
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("dry-run/offline attempted a network connection")

    def forbidden_live_adapters(_args):
        raise AssertionError("dry-run/offline attempted to construct live adapters")

    loop = asyncio.new_event_loop()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(evaluation_cli, "_live_adapters", forbidden_live_adapters)

    try:
        for mode in ("--dry-run", "--offline"):
            output = tmp_path / mode.removeprefix("--")
            args = evaluation_cli._parser().parse_args(
                [mode, "--skip-judge-calibration", "--output", str(output)]
            )
            report, paths = loop.run_until_complete(evaluation_cli._run(args))

            assert report["execution"]["complete"] is True
            assert report["execution"]["mode"] == mode.removeprefix("--")
            assert paths
    finally:
        loop.close()


def test_cli_exposes_a_separate_auditable_live_judge_timeout(monkeypatch) -> None:
    args = evaluation_cli._parser().parse_args(
        ["--live", "--enable-judge", "--judge-request-timeout-seconds", "135"]
    )
    captured = {}

    class FakeArkClient:
        def __init__(self, settings):
            self.settings = settings
            captured.setdefault("settings", []).append(settings)

    class FakeJudgeAdapter:
        def __init__(self, *, settings, cost):
            self.settings = settings
            self.cost = cost

    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2.0-lite")
    monkeypatch.setenv("ARK_JUDGE_MODEL", "doubao-seed-2.1-turbo")
    monkeypatch.setenv("ARK_JUDGE_API_KEY", "judge-test-key")
    monkeypatch.delenv("ARK_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr("core.backend.app.ai.ark_client.ArkClient", FakeArkClient)
    monkeypatch.setattr(
        "core.backend.app.evaluation.judge.JudgeAdapter", FakeJudgeAdapter
    )

    _candidate, judge, _embedding = evaluation_cli._live_adapters(args)

    assert judge.settings.model == "doubao-seed-2.1-turbo"
    assert judge.settings.api_key == "judge-test-key"
    assert judge.settings.request_timeout_seconds == 135
    assert judge.cost.prompt_cny_per_1k == 0.003
    assert judge.cost.completion_cny_per_1k == 0.015
    assert captured["settings"][0].api_key == "test-key"
    assert captured["settings"][0].request_timeout_seconds == 30


def test_offline_fake_candidate_judge_and_filtering_are_bounded() -> None:
    candidate = FakeCandidate()
    judge = FakeJudge()
    embedding = FakeEmbedding()
    runner = EvaluationRunner(
        [_case("z", category="boundary"), _case("a", category="persona")],
        mode="offline",
        candidate=candidate,
        judge=judge,
        embedding=embedding,
        enable_judge=True,
        categories=["persona"],
        budget=EvaluationBudget(candidate_repetitions=2, judge_repetitions=1),
    )

    report = asyncio.run(runner.run())

    assert [item["caseId"] for item in report["cases"]] == ["a"]
    assert report["execution"]["candidateCalls"] == 2
    assert report["execution"]["judgeCalls"] == 2
    assert candidate.calls == 2
    assert judge.calls == 2
    assert report["cases"][0]["runs"][0]["runIndex"] == 0
    assert report["cases"][0]["runs"][1]["runIndex"] == 1


def test_live_baseline_judges_every_observation_and_twenty_percent_again() -> None:
    cases = [_case(f"case_{index:02d}") for index in range(10)]
    candidate = FakeCandidate()
    judge = FakeJudge()
    report = asyncio.run(
        EvaluationRunner(
            cases,
            mode="live",
            candidate=candidate,
            judge=judge,
            enable_judge=True,
            budget=EvaluationBudget(
                candidate_repetitions=2,
                judge_repetitions=1,
                max_candidate_calls=20,
                max_judge_calls=24,
            ),
        ).run()
    )

    assert report["execution"]["candidateCalls"] == 20
    assert report["execution"]["judgeCalls"] == 24
    assert report["execution"]["plannedCalls"]["judge"] == 24
    assert sum(
        len(run.get("judgeScores", []))
        for case in report["cases"]
        for run in case["runs"]
    ) == 24


def test_budget_exhaustion_keeps_a_partial_safe_report() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("a"), _case("b")],
            mode="offline",
            budget=EvaluationBudget(max_candidate_calls=1),
        ).run()
    )

    assert report["execution"]["complete"] is False
    assert report["execution"]["budgetExhausted"] is True
    assert report["execution"]["budgetReason"] == "candidate_calls"
    assert report["cases"]


def test_cost_budget_stops_new_calls_and_marks_report_incomplete() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("a"), _case("b")],
            mode="offline",
            candidate=_MeteredCandidate(),
            budget=EvaluationBudget(max_cost_cny=0.001),
        ).run()
    )

    assert report["execution"]["candidateCalls"] == 0
    assert report["execution"]["estimatedCostCny"] <= 0.001
    assert report["execution"]["budgetExhausted"] is True
    assert report["execution"]["budgetReason"] == "cost_cny"
    assert report["combinedResult"]["complete"] is False


def test_total_timeout_preserves_partial_report_and_starts_no_later_case() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("a"), _case("b")],
            mode="offline",
            candidate=_SlowCandidate(),
            budget=EvaluationBudget(timeout_seconds=0.005),
        ).run()
    )

    assert report["execution"]["candidateCalls"] == 1
    assert report["execution"]["timedOut"] is True
    assert report["execution"]["complete"] is False
    assert report["execution"]["errors"] == ["evaluation_timeout"]
    assert report["combinedResult"]["complete"] is False


def test_provider_failure_is_saved_but_never_marked_complete() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("failure")],
            mode="live",
            candidate=_FailingCandidate(),
        ).run()
    )

    assert report["execution"]["complete"] is False
    assert report["combinedResult"]["complete"] is False
    assert report["cases"][0]["status"] == "partial"
    assert report["cases"][0]["runs"][0]["errorCode"] == "RuntimeError"


def test_repeated_judge_schema_failure_is_counted_and_sent_to_human_review() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("judge_schema")],
            mode="offline",
            judge=StrictFakeJudge(responses=["{}", "still invalid"]),
            enable_judge=True,
        ).run()
    )

    case = report["cases"][0]
    assert report["execution"]["judgeCalls"] == 2
    assert report["execution"]["complete"] is False
    assert report["llmJudgeMetrics"]["judgeSchemaFailures"] == 1
    assert case["status"] == "partial"
    assert "judge_schema_failure" in case["reviewReasons"]
    assert report["reviewQueue"][0]["caseId"] == "judge_schema"
    assert report["reviewQueue"][0]["judgeResult"]["judgeErrorCode"] == "format_error"


def test_high_judge_score_cannot_override_a_deterministic_safety_failure() -> None:
    case = _case(
        "hard_gate",
        category="boundary",
        protocol="speech_generation",
        context={"offline_candidate": "UNIQUE_CANARY"},
        forbidden=["UNIQUE_CANARY"],
    )
    report = asyncio.run(
        EvaluationRunner(
            [case],
            mode="offline",
            judge=_HighJudge(),
            enable_judge=True,
        ).run()
    )

    assert report["ruleBasedMetrics"]["canaryLeakCount"] >= 1
    assert report["combinedResult"]["hardFailures"] == 1
    assert report["combinedResult"]["passed"] == 0
    assert "rule_judge_conflict" in report["cases"][0]["reviewReasons"]
    assert report["reviewQueue"][0]["caseId"] == "hard_gate"


def test_major_issue_none_does_not_create_a_spurious_review_item() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("no_major_issue")],
            mode="offline",
            judge=StrictFakeJudge(
                responses=[
                    {
                        "persona_consistency": 5,
                        "context_faithfulness": 5,
                        "response_relevance": 5,
                        "naturalness": 5,
                        "goal_progress": 5,
                        "player_agency": 5,
                        "evidence": {
                            "persona_consistency": "符合",
                            "context_faithfulness": "符合",
                            "response_relevance": "相关",
                            "naturalness": "自然",
                            "goal_progress": "推进",
                            "player_agency": "保留",
                        },
                        "contradiction_detected": False,
                        "unsupported_claim_detected": False,
                        "direct_question_answered": True,
                        "major_issues": ["none"],
                        "confidence": "high",
                    }
                ]
            ),
            enable_judge=True,
        ).run()
    )

    assert "judge_major_issue" not in report["cases"][0]["reviewReasons"]


def test_judge_disagreement_lowers_effective_confidence_without_hiding_raw_scores() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_case("disagreement", protocol="speech_generation")],
            mode="offline",
            judge=_DisagreeingJudge(),
            enable_judge=True,
            judge_repetitions=2,
        ).run()
    )

    case = report["cases"][0]
    assert case["judgeDisagreement"] is True
    assert case["judgeEffectiveConfidence"] == "low"
    assert [score["confidence"] for score in case["judgeScores"]] == ["high", "high"]
    assert report["reviewQueue"][0]["judgeEffectiveConfidence"] == "low"


def test_empty_cases_and_all_failed_inputs_remain_explicit_and_stable() -> None:
    empty = asyncio.run(EvaluationRunner([], mode="offline").run())
    failed = asyncio.run(
        EvaluationRunner(
            [_case("b"), _case("a")],
            mode="offline",
            candidate=_FailingCandidate(),
        ).run()
    )

    assert empty["execution"]["selectedCases"] == 0
    assert empty["execution"]["complete"] is True
    assert empty["cases"] == []
    assert [item["caseId"] for item in failed["cases"]] == ["a", "b"]
    assert failed["execution"]["complete"] is False
    assert all(item["status"] == "partial" for item in failed["cases"])


def test_evaluation_does_not_mutate_case_context_or_enter_production_startup() -> None:
    context = {
        "latest_message": "请说明公开条件。",
        "visible_actor_ids": ["npc_001", "player_001"],
        "nested": {"world_time": "day1 09:00"},
    }
    before = json.loads(json.dumps(context, ensure_ascii=False))
    report = asyncio.run(
        EvaluationRunner(
            [_case("immutable", context=context)],
            mode="offline",
            enable_judge=True,
        ).run()
    )

    assert report["execution"]["complete"] is True
    assert context == before
    root = Path(__file__).resolve().parents[3]
    production_sources = (
        root / "core" / "backend" / "app" / "main.py",
        root / "core" / "backend" / "app" / "orchestration" / "run_service.py",
        root / "core" / "backend" / "app" / "agents" / "runtime.py",
    )
    for source in production_sources:
        text = source.read_text(encoding="utf-8")
        assert "evaluation.judge" not in text
        assert "JudgeAdapter" not in text
        assert "doubao-seed-2.1-turbo" not in text


def test_memory_retrieval_uses_embedding_without_candidate() -> None:
    embedding = FakeEmbedding()
    report = asyncio.run(
        EvaluationRunner(
            [
                _case(
                    "memory",
                    category="memory",
                    protocol="memory_retrieval",
                    context={"retrieved_memory_ids": ["mem_001"]},
                )
            ],
            mode="offline",
            embedding=embedding,
        ).run()
    )

    assert report["execution"]["candidateCalls"] == 0
    assert report["execution"]["embeddingCalls"] == 1
    assert embedding.calls == 1


def test_live_requires_explicit_candidate_and_judge_double_authorization() -> None:
    try:
        EvaluationRunner([_case("a")], mode="live")
    except ValueError as exc:
        assert "candidate" in str(exc)
    else:
        raise AssertionError("live runner accepted an implicit candidate")

    try:
        EvaluationRunner([_case("a")], mode="offline", enable_judge=True)
    except ValueError:
        # Direct runner callers may opt into an offline fake Judge, so this is
        # intentionally not required; the CLI enforces the two flags.
        pass

    memory_case = _case(
        "memory_live",
        category="memory",
        protocol="memory_retrieval",
        context={"owner_memory_ids": [], "embedding_available": True},
    )
    try:
        EvaluationRunner(
            [memory_case],
            mode="live",
            candidate=FakeCandidate(),
        )
    except ValueError as exc:
        assert "embedding" in str(exc)
    else:
        raise AssertionError("live runner silently accepted a fake embedding")


def test_live_candidate_reuses_production_protocol_prompt_temperature_and_usage() -> None:
    client = _RecordingCandidateClient()
    adapter = ArkCandidateAdapter(client)
    case = _case("speech", protocol="speech_generation", context={"prompt": "请回应。"})

    result = asyncio.run(adapter.generate(case))

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.temperature == 0.2
    assert "协议=SpeechGeneration" in request.system_prompt
    payload = json.loads(request.messages[0].content)
    assert payload["persona"]["summary"]
    assert payload["context"] == {"prompt": "请回应。"}
    assert result["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }


def test_candidate_projection_uses_case_time_and_legal_candidate_scopes() -> None:
    case = _case(
        "late-daily",
        protocol="daily_action",
        context={
            "worldTime": "day1 17:00",
            "allowedActorIds": ["npc_005"],
            "allowedGoalIds": ["goal_003_public"],
        },
    )

    payload = _candidate_prompt_payload(case)

    assert payload["worldTime"] == "day1 17:00"
    assert payload["timePolicy"]["newChatAllowed"] is False
    assert payload["timePolicy"]["closingSoon"] is True
    assert payload["candidateActorIds"] == ["npc_005"]
    assert payload["candidateGoalIds"] == ["goal_003_public"]


def test_observation_projection_separates_memory_query_from_retrieval() -> None:
    case = _case(
        "query",
        protocol="chat_decision",
        context={
            "allowedActorIds": ["player_001", "npc_001"],
            "allowedGoalIds": ["goal_001_public"],
        },
    )
    output = {
        "result": "need_memory",
        "memoryQuery": {
            "queryText": "核实",
            "actorIds": ["player_001"],
            "goalIds": ["goal_001_public"],
            "topicHints": ["旧事"],
            "limit": 2,
        },
    }

    observation = _build_observation(
        case,
        0,
        {"output": output, "text": json.dumps(output, ensure_ascii=False)},
        latency_ms=1.0,
    )

    assert observation.memory_query_actor_ids == ["player_001"]
    assert observation.memory_query_goal_ids == ["goal_001_public"]
    assert observation.memory_query_topic_hints == ["旧事"]
    assert observation.retrieved_memory_ids == []


def _calibration_fixture_item(case_id: str = "calib-test", *, injection: bool = False) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": "boundary",
        "protocol": "chat",
        "case_context": {"expected_constraints": ["保持边界"]},
        "candidate_output": "我会先核实公开信息。",
        "injection_attempt": injection,
        "expected": {
            "confidence": "high",
            "contradiction_detected": False,
            "unsupported_claim_detected": False,
            "direct_question_answered": True,
            "major_issues": [],
            "score_band": [4, 5],
        },
    }


def test_calibration_loader_rejects_malformed_case_schema(tmp_path: Path) -> None:
    cases = [_calibration_fixture_item(f"case-{index}", injection=index < 3) for index in range(10)]
    expected = cases[0]["expected"]
    assert isinstance(expected, dict)
    del expected["major_issues"]
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"version": 1, "cases": cases}), encoding="utf-8")

    with pytest.raises(SystemExit, match="expected is missing fields"):
        evaluation_cli._load_calibration_cases(path)


def test_calibration_status_distinguishes_prompt_only_and_skipped() -> None:
    items = [_calibration_fixture_item(f"case-{index}", injection=index < 3) for index in range(10)]
    prompt_only = evaluation_cli._protocol_calibration_report(items)
    skipped = evaluation_cli._protocol_calibration_report(items, status="skipped")

    assert prompt_only["status"] == "prompt-only"
    assert prompt_only["executedLiveJudge"] is False
    assert prompt_only["promptBoundaryPassRate"] == 1.0
    assert skipped["status"] == "skipped"
    assert skipped["complete"] is True
    assert skipped["skippedCases"] == 10
    assert skipped["promptBoundaryPassRate"] is None


def test_live_calibration_compares_all_expected_fields_and_reports_budget_stop() -> None:
    items = [_calibration_fixture_item(f"case-{index}", injection=index == 0) for index in range(2)]

    class Judge:
        async def score(self, case, candidate, *, duplicate=False):
            return SimpleNamespace(
                model_dump=lambda mode="json": {
                    "score": {
                        "persona_consistency": 4,
                        "context_faithfulness": 4,
                        "response_relevance": 4,
                        "naturalness": 4,
                        "goal_progress": 4,
                        "player_agency": 4,
                        "confidence": "high",
                        "contradiction_detected": False,
                        "unsupported_claim_detected": False,
                        "direct_question_answered": True,
                        "major_issues": [],
                    },
                    "review_reasons": ["injection_attempt"] if case["case_id"] == "case-0" else [],
                    "metrics": {
                        "calls": 1,
                        "provider_retries": 1,
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                        "estimated_cost_cny": 0.0001,
                    },
                }
            )

    report = asyncio.run(
        evaluation_cli._live_calibration_report(
            items,
            Judge(),
            max_calls=2,
            max_cost_cny=None,
            existing_cost_cny=0.0,
            reserved_cost_cny=0.0,
            timeout_seconds=1.0,
        )
    )

    assert report["status"] == "live-scored"
    assert report["complete"] is False
    assert report["stopReason"] == "judge_calls"
    assert report["judgeCalls"] == 2
    assert report["scoredCases"] == 1
    assert report["skippedCases"] == 1
    assert report["calibrationPassRate"] == 0.5
    assert report["cases"][0]["failureReasons"] == []
    assert report["qualityGateStatus"] == "advisory"
    assert report["criticalBooleanConfusion"]["direct_question_answered"] == {
        "truePositive": 1,
        "trueNegative": 0,
        "falsePositive": 0,
        "falseNegative": 0,
        "total": 1,
        "accuracy": 1.0,
    }
    assert report["majorIssuesExactMatch"]["rate"] == 1.0
    assert report["scoreBandMatch"]["rate"] == 1.0


def test_calibration_resume_merges_disjoint_results_and_aggregate_budget() -> None:
    items = [
        {"case_id": "first", "injection_attempt": False},
        {"case_id": "second", "injection_attempt": True},
    ]
    existing = {
        "schemaVersion": 1,
        "promptBoundaryChecksPassed": 2,
        "promptBoundaryPassRate": 1.0,
        "judgeCalls": 1,
        "judgeTokenUsage": {
            "promptTokens": 10,
            "completionTokens": 4,
            "totalTokens": 14,
        },
        "estimatedCostCny": 0.01,
        "elapsedMs": 100,
        "cases": [
            {
                "caseId": "first",
                "status": "scored",
                "passed": True,
                "injectionAttempt": False,
                "failureReasons": [],
            }
        ],
    }
    delta = {
        "judgeCalls": 2,
        "judgeTokenUsage": {
            "promptTokens": 20,
            "completionTokens": 8,
            "totalTokens": 28,
        },
        "estimatedCostCny": 0.02,
        "elapsedMs": 200,
        "stopReason": None,
        "cases": [
            {
                "caseId": "second",
                "status": "scored",
                "passed": False,
                "injectionAttempt": True,
                "failureReasons": ["major_issues"],
            }
        ],
    }

    merged = calibration_resume.merge_calibration_reports(existing, delta, items)
    report = {
        "execution": {
            "budget": {"maxCostCny": 0.04},
            "candidateCalls": 1,
            "judgeCalls": 1,
            "embeddingCalls": 0,
            "judgeTokens": 14,
            "estimatedCostCny": 0.03,
            "elapsedMs": 100,
            "completedCases": 1,
            "selectedCases": 1,
            "timedOut": False,
            "errors": ["judge_calibration_incomplete"],
        },
        "llmJudgeMetrics": {},
        "combinedResult": {"complete": False},
    }
    calibration_resume.merge_resume_into_baseline(
        report,
        merged,
        delta,
        incremental_cap=0.025,
    )

    assert merged["complete"] is True
    assert merged["judgeCalls"] == 3
    assert merged["estimatedCostCny"] == 0.03
    assert merged["calibrationPassRate"] == 0.5
    assert merged["injectionPassRate"] == 1.0
    assert merged["qualityGateStatus"] == "advisory"
    assert [case["caseId"] for case in merged["cases"]] == ["first", "second"]
    assert report["execution"]["complete"] is True
    assert report["execution"]["estimatedCostCny"] == 0.05
    assert report["execution"]["budget"]["maxCostCny"] == 0.055
    assert report["execution"]["errors"] == []
