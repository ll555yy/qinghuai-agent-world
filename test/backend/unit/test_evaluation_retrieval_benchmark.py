from __future__ import annotations

import asyncio

import pytest

from core.backend.app.ai.protocols import MemoryQuery
from core.backend.app.evaluation.annotation import (
    build_annotation_package,
    freeze_annotation_samples,
    validate_annotation_submission,
)
from core.backend.app.evaluation.calibration import calibration_quality_gate
from core.backend.app.evaluation.case_loader import load_cases
from core.backend.app.evaluation.models import CandidateObservation, EvaluationCase
from core.backend.app.evaluation.readiness import (
    build_final_47_case_report_skeleton,
    validate_final_case_set,
)
from core.backend.app.evaluation.report import build_report
from core.backend.app.evaluation.retrieval_benchmark import (
    RetrievalBenchmarkCase,
    benchmark_database_memory_retriever,
)
from core.backend.app.evaluation.rule_scorer import RuleScorer, retrieval_metrics
from core.backend.app.evaluation.runner import EvaluationBudget, EvaluationExecution


def _case(*, expected: list[str], context: dict[str, object] | None = None) -> EvaluationCase:
    return EvaluationCase(
        case_id="retrieval_metrics",
        case_version=1,
        category="memory",
        protocol="memory_retrieval",
        npc_id="npc_001",
        input_context=context or {"owner_memory_ids": expected, "retrieval_k": 3},
        expected_constraints=[],
        forbidden_signals=[],
        allowed_outcomes=[],
        expected_memory_ids=expected,
        allowed_evidence_message_ids=[],
        requires_postgres=False,
        requires_live_candidate=False,
        requires_live_embedding=False,
        judge_rubric=[],
        tags=["synthetic"],
    )


def test_returned_metrics_preserve_strict_k_and_expose_duplicates_and_empty_query() -> None:
    metrics = retrieval_metrics(
        ["memory_a"],
        ["memory_a", "memory_a", "memory_noise"],
        3,
    )
    assert metrics["strict_precision_at_k"] == 1 / 3
    assert metrics["precision_at_returned"] == 2 / 3
    assert metrics["false_positive_rate"] == 1 / 3
    assert metrics["duplicate_result_count"] == 1
    assert metrics["false_positive_count"] == 1

    empty = retrieval_metrics([], [], 3, query_is_empty=True)
    assert empty["empty_query_correct"] is True


def test_rule_score_keeps_retrieval_source_and_new_metrics() -> None:
    case = _case(expected=["memory_a"], context={"owner_memory_ids": ["memory_a"], "queryText": "anchor"})
    score = RuleScorer().score(
        case,
        CandidateObservation(
            case_id=case.case_id,
            protocol=case.protocol,
            retrieved_memory_ids=["memory_a", "memory_noise"],
            owner_memory_ids=["memory_a"],
            retrieval_source="postgres",
            retrieval_k=2,
        ),
    )
    assert score.retrieval_source == "postgres"
    assert score.strict_precision_at_k == score.precision_at_k
    assert score.precision_at_returned == 0.5
    assert score.false_positive_rate == 0.5


class _FakeDatabaseRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, MemoryQuery]] = []

    async def search(self, *, run_id: str, owner_npc_id: str, query: MemoryQuery):
        self.calls.append((run_id, owner_npc_id, query))
        if query.query_text == "anchor":
            return type("Result", (), {"memory_ids": ("memory_a",), "vector_hits": 1, "graph_hits": 0})()
        return type("Result", (), {"memory_ids": (), "vector_hits": 0, "graph_hits": 0})()


def test_postgres_benchmark_calls_search_and_keeps_tuning_holdout_and_phase() -> None:
    retriever = _FakeDatabaseRetriever()
    report = asyncio.run(
        benchmark_database_memory_retriever(
            retriever,
            [
                RetrievalBenchmarkCase(
                    case_id="tuning-vector",
                    run_id="run-1",
                    owner_npc_id="npc_001",
                    query={"queryText": "anchor", "limit": 1},
                    expected_memory_ids=("memory_a",),
                    retrieval_k=1,
                    phase="vector",
                    split="tuning",
                    owner_memory_ids=("memory_a",),
                ),
                RetrievalBenchmarkCase(
                    case_id="holdout-empty",
                    run_id="run-1",
                    owner_npc_id="npc_001",
                    query={},
                    expected_memory_ids=(),
                    retrieval_k=1,
                    phase="keyword",
                    split="holdout",
                    owner_memory_ids=("memory_a",),
                ),
            ],
        )
    )
    assert len(retriever.calls) == 2
    assert report["retrievalSource"] == "postgres"
    assert report["splitMetrics"]["tuning"]["precisionAtReturned"] == 1.0
    assert report["splitMetrics"]["holdout"]["emptyQueryCorrect"] == 1.0
    assert report["phaseMetrics"]["vector"]["tuning"]["cases"] == 1
    assert report["cases"][0]["retrievalSource"] == "postgres"


def test_empty_query_is_excluded_from_ranking_aggregates() -> None:
    retriever = _FakeDatabaseRetriever()
    report = asyncio.run(
        benchmark_database_memory_retriever(
            retriever,
            [
                RetrievalBenchmarkCase(
                    case_id="holdout-ranked",
                    run_id="run-1",
                    owner_npc_id="npc_001",
                    query={"queryText": "anchor", "limit": 1},
                    expected_memory_ids=("memory_a",),
                    retrieval_k=1,
                    split="holdout",
                    owner_memory_ids=("memory_a",),
                ),
                RetrievalBenchmarkCase(
                    case_id="holdout-empty",
                    run_id="run-1",
                    owner_npc_id="npc_001",
                    query={},
                    expected_memory_ids=(),
                    retrieval_k=3,
                    split="holdout",
                    owner_memory_ids=("memory_a",),
                ),
            ],
            baseline_mrr=1.0,
        )
    )
    holdout = report["splitMetrics"]["holdout"]
    assert holdout["cases"] == 2
    assert holdout["rankingCases"] == 1
    assert holdout["mrr"] == 1.0
    assert holdout["strictPrecisionAtK"] == 1.0
    assert report["holdoutAccepted"] is True


def test_mixed_retrieval_sources_have_no_aggregate_metric() -> None:
    cases = []
    for source in ("fixture", "postgres"):
        observation = CandidateObservation(
            case_id="retrieval_metrics",
            protocol="memory_retrieval",
            retrieved_memory_ids=["memory_a"],
            owner_memory_ids=["memory_a"],
            retrieval_source=source,  # type: ignore[arg-type]
            retrieval_k=1,
        )
        score = RuleScorer().score(_case(expected=["memory_a"]), observation)
        cases.append(
            {
                "caseId": source,
                "protocol": "memory_retrieval",
                "ruleScore": score.model_dump(mode="json", by_alias=True),
                "runs": [],
                "reviewReasons": [],
            }
        )
    report = build_report(
        cases=cases,
        execution=EvaluationExecution(mode="offline", selected_cases=2, completed_cases=2),
        budget=EvaluationBudget(),
        selected_cases=[],
    )
    assert report["ruleBasedMetrics"]["memoryPrecisionAtK"] is None
    assert set(report["ruleBasedMetrics"]["retrievalMetricsBySource"]) == {"fixture", "postgres"}


def test_annotation_package_requires_two_real_humans_and_is_blank() -> None:
    cases = [
        {"caseId": f"case-{index:02d}", "category": category, "protocol": "speech_generation"}
        for index, category in enumerate(("persona", "boundary", "memory", "rules", "relevance", "coherence") * 4)
    ]
    samples = freeze_annotation_samples(cases)
    package = build_annotation_package(cases)
    assert len(samples) == 24
    assert package["humanRequired"] is True
    assert package["automatedLabelsAllowed"] is False
    with pytest.raises(ValueError, match="real-human"):
        validate_annotation_submission(
            {"annotator": "A", "labels": [{"sampleId": "sample-001"}], "source": "judge"},
            expected_annotator="A",
        )


def test_calibration_quality_gate_is_advisory_until_all_explicit_gates_pass() -> None:
    advisory = calibration_quality_gate(
        {
            "complete": True,
            "datasetCases": 13,
            "scoredCases": 13,
            "criticalBooleanMacroAccuracy": 0.79,
            "scoreBandMatch": {"rate": 1.0},
            "injectionCases": 3,
            "injectionPassRate": 1.0,
            "providerSchemaErrors": 0,
        }
    )
    assert advisory["qualityGateStatus"] == "advisory"
    assert "critical_boolean_macro_below_80_percent" in advisory["reasons"]
    passed = calibration_quality_gate(
        {
            "complete": True,
            "datasetCases": 13,
            "scoredCases": 13,
            "criticalBooleanMacroAccuracy": 0.8,
            "scoreBandMatch": {"rate": 0.8},
            "injectionCases": 3,
            "injectionPassRate": 1.0,
            "providerSchemaErrors": 0,
        }
    )
    assert passed["qualityGateStatus"] == "quality-gate"


def test_final_readiness_freezes_the_47_case_denominator() -> None:
    cases = load_cases("core/evaluation/agent_semantic_cases.yaml")
    denominator = validate_final_case_set(cases)
    skeleton = build_final_47_case_report_skeleton(cases)
    assert denominator["caseCount"] == 47
    assert skeleton["status"] == "prepared_not_run"
    assert skeleton["execution"]["complete"] is False
