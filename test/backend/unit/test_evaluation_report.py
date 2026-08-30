from __future__ import annotations

import json

from core.backend.app.evaluation.models import EvaluationReport
from core.backend.app.evaluation.report import (
    build_report,
    human_arbitration_table,
    redact,
    render_bad_cases,
    report_to_json,
    report_to_markdown,
    write_report,
)
from core.backend.app.evaluation.runner import EvaluationBudget, EvaluationExecution


def test_report_redacts_credentials_urls_and_forbidden_canary() -> None:
    value = redact(
        {
            "candidate": "the CANARY must not escape",
            "apiKey": "secret-value",
            "databaseUrl": "postgresql://user:pass@example/db",
            "nested": ["Bearer abcdefghijklmnop", "safe"],
        },
        forbidden=["CANARY"],
    )

    serialized = report_to_json(value)
    assert "CANARY" not in serialized
    assert "secret-value" not in serialized
    assert "postgresql://" not in serialized
    assert "Bearer" not in serialized
    assert "safe" in serialized


def test_report_sorting_bad_cases_and_arbitration_are_stable() -> None:
    execution = EvaluationExecution(mode="offline", selected_cases=2, completed_cases=2)
    report = build_report(
        cases=[
            {
                "caseId": "z",
                "category": "boundary",
                "protocol": "daily_action",
                "ruleScore": {"hard_failure": False, "failures": []},
                "reviewReasons": [],
                "runs": [],
            },
            {
                "caseId": "a",
                "category": "persona",
                "protocol": "speech_generation",
                "ruleScore": {"hard_failure": True, "failures": ["canary_leak"]},
                "reviewReasons": ["rule_hard_failure"],
                "candidateSummary": "CANARY",
                "runs": [],
            },
        ],
        execution=execution,
        budget=EvaluationBudget(),
        selected_cases=[{"case_id": "a", "category": "persona", "forbidden_signals": ["CANARY"]}],
    )

    assert [item["caseId"] for item in report["cases"]] == ["a", "z"]
    assert report["badCases"][0]["caseId"] == "a"
    assert report["reviewQueue"][0]["caseId"] == "a"
    assert "CANARY" not in report_to_markdown(report)
    assert "canary_leak" in render_bad_cases(report)
    assert "Decision" in human_arbitration_table(report)
    assert report_to_json(report) == report_to_json(report)


def test_report_exposes_required_rule_and_judge_metric_contracts() -> None:
    execution = EvaluationExecution(mode="live", selected_cases=1, completed_cases=1)
    report = build_report(
        cases=[
            {
                "caseId": "metric_case",
                "category": "relevance",
                "protocol": "speech_generation",
                "ruleScore": {"hard_failure": False, "failures": []},
                "reviewReasons": [],
                "runs": [
                    {
                        "runIndex": 0,
                        "candidateSummary": "清晰回答",
                        "ruleScore": {
                            "hard_failure": False,
                            "failures": [],
                            "schema_valid": True,
                            "latency_ms": 12,
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                            "retries": 0,
                            "estimated_cost_cny": 0.001,
                            "repetition_detected": False,
                        },
                        "judgeScores": [
                            {
                                "persona_consistency": 4,
                                "context_faithfulness": 4,
                                "response_relevance": 5,
                                "naturalness": 4,
                                "goal_progress": 3,
                                "player_agency": 5,
                                "contradiction_detected": False,
                                "unsupported_claim_detected": False,
                                "direct_question_answered": True,
                                "major_issues": [],
                                "confidence": "high",
                                "judgeMetrics": {
                                    "calls": 1,
                                    "prompt_tokens": 20,
                                    "completion_tokens": 10,
                                    "total_tokens": 30,
                                    "latency_ms": 25,
                                    "estimated_cost_cny": 0.002,
                                },
                            }
                        ],
                    }
                ],
                "judgeScores": [],
            }
        ],
        execution=execution,
        budget=EvaluationBudget(),
        selected_cases=[{"case_id": "metric_case", "category": "relevance"}],
        enable_judge=True,
    )

    rule = report["ruleBasedMetrics"]
    judge = report["llmJudgeMetrics"]
    assert {
        "schemaSuccessRate",
        "firstAttemptSchemaSuccessRate",
        "memoryPrecisionAtK",
        "p50LatencyMs",
        "p95LatencyMs",
        "tokenUsage",
        "estimatedCostCny",
    } <= rule.keys()
    assert rule["schemaSuccessRate"] == 1.0
    assert judge["judgeModel"] == "doubao-seed-2.1-turbo"
    assert judge["dimensions"]["responseRelevance"]["mean"] == 5.0
    assert judge["judgeCalls"] == 1
    assert judge["judgeTokenUsage"]["totalTokens"] == 30
    assert judge["judgeRetryCount"] == 0


def test_report_maps_protocol_rubric_v2_and_calibration_advisory(tmp_path) -> None:
    score = {
        "persona_consistency": 4,
        "context_faithfulness": 4,
        "response_relevance": 5,
        "naturalness": 1,
        "goal_progress": 3,
        "player_agency": 5,
        "contradiction_detected": False,
        "unsupported_claim_detected": False,
        "direct_question_answered": True,
        "major_issues": [],
        "confidence": "high",
    }
    cases = [
        {
            "caseId": "chat_rubric",
            "category": "relevance",
            "protocol": "chat_decision",
            "ruleScore": {"hard_failure": False, "failures": []},
            "reviewReasons": [],
            "runs": [{"judgeScores": [score]}],
        },
        {
            "caseId": "invitation_rubric",
            "category": "relevance",
            "protocol": "invitation",
            "ruleScore": {"hard_failure": False, "failures": []},
            "reviewReasons": [],
            "runs": [{"judgeScores": [score]}],
        },
    ]
    report = build_report(
        cases=cases,
        execution=EvaluationExecution(mode="offline", selected_cases=2, completed_cases=2),
        budget=EvaluationBudget(),
        selected_cases=[
            {"case_id": "chat_rubric", "category": "relevance"},
            {"case_id": "invitation_rubric", "category": "relevance"},
        ],
        enable_judge=True,
    )

    judge = report["llmJudgeMetrics"]
    assert judge["rubricVersion"] == "agent-semantic-rubric-v2"
    assert judge["judgeAdvisory"] is True
    assert judge["judgeAdvisoryReasons"] == ["calibration_not_available"]
    chat = judge["protocolRubrics"]["chat_decision"]
    assert "naturalness" not in chat["applicableDimensions"]
    assert "naturalness" in chat["notApplicableDimensions"]
    assert "naturalness" not in chat["dimensions"]
    assert judge["applicableDimensionsByProtocol"]["invitation"]

    report["judgeCalibration"] = {
        "complete": True,
        "calibrationPassRate": 0.9,
        "injectionPassRate": 1.0,
        "injectionCases": 3,
    }
    write_report(
        report,
        tmp_path / "rubric-v2.json",
        write_markdown=False,
        write_bad_cases=False,
        write_review_queue=False,
        write_judge_stability=False,
    )
    assert report["llmJudgeMetrics"]["judgeAdvisory"] is False
    assert report["llmJudgeMetrics"]["judgeInjectionPassRate"] == 1.0

    report["judgeCalibration"] = {
        "complete": True,
        "calibrationPassRate": 0.79,
        "injectionPassRate": 2 / 3,
        "injectionCases": 3,
    }
    report_to_json(report)
    assert report["llmJudgeMetrics"]["judgeAdvisory"] is True
    assert "calibration_below_80_percent" in report["llmJudgeMetrics"]["judgeAdvisoryReasons"]
    assert "injection_not_3_of_3" in report["llmJudgeMetrics"]["judgeAdvisoryReasons"]


def test_generated_report_validates_and_preserves_partial_and_skipped_cases(
    tmp_path,
) -> None:
    execution = EvaluationExecution(
        mode="live",
        selected_cases=2,
        completed_cases=2,
        complete=False,
    )
    report = build_report(
        cases=[
            {
                "caseId": "partial_case",
                "caseVersion": 1,
                "category": "boundary",
                "protocol": "speech_generation",
                "status": "partial",
                "ruleScore": {"hard_failure": True, "failures": ["judge_failed"]},
                "ruleScores": [],
                "judgeScores": [],
                "reviewReasons": ["judge_failed"],
                "candidateSummary": "partial candidate",
                "runs": [],
            },
            {
                "caseId": "skipped_case",
                "caseVersion": 1,
                "category": "memory",
                "protocol": "memory_retrieval",
                "status": "skipped",
                "ruleScores": [],
                "reviewReasons": ["requires_postgres"],
                "runs": [],
            },
        ],
        execution=execution,
        budget=EvaluationBudget(),
        selected_cases=[
            {
                "case_id": "partial_case",
                "category": "boundary",
                "forbidden_signals": [],
            },
            {
                "case_id": "skipped_case",
                "category": "memory",
                "forbidden_signals": [],
            },
        ],
    )

    validated = EvaluationReport.model_validate(report)
    assert [item.status for item in validated.cases] == ["partial", "skipped"]
    output = tmp_path / "report.json"
    write_report(
        report,
        output,
        write_markdown=False,
        write_bad_cases=False,
        write_review_queue=False,
        write_judge_stability=False,
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))
    artifact_model = EvaluationReport.model_validate(artifact)
    assert artifact_model.execution["complete"] is False


def test_report_redacts_candidate_summary_and_internal_fields() -> None:
    report = build_report(
        cases=[
            {
                "caseId": "internal_fields",
                "category": "boundary",
                "protocol": "speech_generation",
                "ruleScore": {"hard_failure": False, "failures": []},
                "reviewReasons": [],
                "candidateSummary": (
                    '{"coreSecrets":"PRIVATE", "ownerNpcId":"npc_005", '
                    '"trace_id":"trace_private", "text":"safe"}'
                ),
                "runs": [
                    {
                        "runIndex": 0,
                        "candidateSummary": (
                            "ownerNpcId=npc_005 trace_id=trace_private "
                            "coreSecrets=PRIVATE safe"
                        ),
                        "ownerNpcId": "npc_005",
                        "trace_id": "trace_private",
                    },
                ],
            }
        ],
        execution=EvaluationExecution(mode="offline", selected_cases=1, completed_cases=1),
        budget=EvaluationBudget(),
        selected_cases=[
            {
                "case_id": "internal_fields",
                "category": "boundary",
                "forbidden_signals": [],
            }
        ],
    )

    serialized = report_to_json(report)
    for marker in ("coreSecrets", "ownerNpcId", "trace_id", "PRIVATE", "npc_005", "trace_private"):
        assert marker not in serialized
    assert "safe" in serialized
