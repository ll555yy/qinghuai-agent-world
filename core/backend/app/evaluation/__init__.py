"""Offline-first semantic evaluation for the NPC Agent runtime."""

from .annotation import (
    AnnotationSample,
    build_annotation_package,
    freeze_annotation_samples,
    validate_annotation_submission,
)
from .calibration import (
    CalibrationExpectation,
    CalibrationQualityGate,
    calibration_quality_gate,
    compare_calibration_case,
    load_calibration_cases,
    validate_calibration_cases,
)
from .case_loader import CaseLoader, CaseValidationError, load_cases
from .judge import FakeJudge as ProtocolFakeJudge
from .judge import JudgeAdapter
from .models import CandidateObservation, EvaluationCase, RetrievalSource, RuleScore
from .retrieval_benchmark import (
    RetrievalAcceptanceThresholds,
    RetrievalBenchmarkCase,
    benchmark_database_memory_retriever,
    render_retrieval_benchmark_markdown,
    run_postgres_benchmark,
    run_postgres_retrieval_benchmark,
)
from .rule_scorer import RuleScorer, retrieval_metrics
from .runner import EvaluationBudget, EvaluationRunner

__all__ = [
    "CandidateObservation",
    "AnnotationSample",
    "CaseLoader",
    "CaseValidationError",
    "CalibrationExpectation",
    "CalibrationQualityGate",
    "EvaluationBudget",
    "EvaluationCase",
    "EvaluationRunner",
    "JudgeAdapter",
    "ProtocolFakeJudge",
    "RuleScore",
    "RuleScorer",
    "RetrievalSource",
    "RetrievalAcceptanceThresholds",
    "RetrievalBenchmarkCase",
    "benchmark_database_memory_retriever",
    "build_annotation_package",
    "calibration_quality_gate",
    "compare_calibration_case",
    "load_cases",
    "load_calibration_cases",
    "freeze_annotation_samples",
    "retrieval_metrics",
    "render_retrieval_benchmark_markdown",
    "run_postgres_benchmark",
    "run_postgres_retrieval_benchmark",
    "validate_calibration_cases",
    "validate_annotation_submission",
]
