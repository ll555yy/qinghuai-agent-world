"""Provider-neutral contracts for deterministic semantic evaluation.

The evaluation package deliberately has no dependency on a model client.  A
runner may build :class:`CandidateObservation` from a live or fake adapter,
while :mod:`rule_scorer` can score the exact same object completely offline.
Field aliases keep the contract pleasant for both Python callers and the
camelCase JSON/YAML used by the rest of the backend.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .judge_protocols import JudgeScore

CaseCategory = Literal[
    "persona",
    "boundary",
    "memory",
    "rules",
    "relevance",
    "coherence",
]

# Retrieval fixtures, the dedicated PostgreSQL benchmark, and a future live
# embedding run are intentionally different evidence sources.  Keeping the
# source in the observation contract prevents a report consumer from silently
# averaging fixture IDs together with database results.
RetrievalSource = Literal["fixture", "postgres", "live_embedding"]

EvaluationProtocol = Literal[
    "daily_action",
    "invitation",
    "chat_decision",
    "speech_generation",
    "segment_summary",
    "exit_consolidation",
    "memory_retrieval",
]

NpcId = Literal["npc_001", "npc_002", "npc_003", "npc_004", "npc_005"]


_PROTOCOL_ALIASES = {
    "dailyAction": "daily_action",
    "daily_action_decision": "daily_action",
    "DailyActionDecision": "daily_action",
    "invitation_decision": "invitation",
    "InvitationDecision": "invitation",
    "chatDecision": "chat_decision",
    "ChatDecision": "chat_decision",
    "speechGeneration": "speech_generation",
    "SpeechGeneration": "speech_generation",
    "segmentSummary": "segment_summary",
    "SegmentSummary": "segment_summary",
    "exitConsolidation": "exit_consolidation",
    "ExitConsolidation": "exit_consolidation",
    "memoryRetrieval": "memory_retrieval",
    "MemoryQuery": "memory_retrieval",
}


def _canonical_protocol(value: Any) -> Any:
    if isinstance(value, str):
        return _PROTOCOL_ALIASES.get(value, value)
    return value


def _normalise_text(value: str) -> str:
    return " ".join(value.split())


def _constraint_marker(value: str) -> tuple[bool, str] | None:
    """Extract a small, deterministic ``must``/``must_not`` marker.

    Natural-language rubric text is intentionally left alone.  Only explicit
    marker forms are interpreted, so a phrase such as ``must be polite`` does
    not unexpectedly conflict with an unrelated prose signal.
    """

    text = _normalise_text(value).casefold()
    for prefix, negative in (
        ("must_not_", True),
        ("must-not-", True),
        ("must not ", True),
        ("mustnot_", True),
        ("must_", False),
        ("must-", False),
        ("must ", False),
    ):
        if text.startswith(prefix):
            marker = text[len(prefix) :].strip(" _-:;,.!?\t")
            if marker:
                return negative, marker
    return None


class EvaluationModel(BaseModel):
    """Strict, immutable base for evaluation records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class EvaluationCase(EvaluationModel):
    """One versioned, synthetic semantic-evaluation case."""

    case_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("case_id", "caseId"),
        serialization_alias="caseId",
    )
    case_version: int = Field(
        ge=1,
        validation_alias=AliasChoices("case_version", "caseVersion"),
        serialization_alias="caseVersion",
    )
    category: CaseCategory
    protocol: EvaluationProtocol
    npc_id: NpcId = Field(
        validation_alias=AliasChoices("npc_id", "npcId"),
        serialization_alias="npcId",
    )
    input_context: dict[str, Any] = Field(
        validation_alias=AliasChoices("input_context", "inputContext"),
        serialization_alias="inputContext",
    )
    expected_constraints: list[str] = Field(
        validation_alias=AliasChoices("expected_constraints", "expectedConstraints"),
        serialization_alias="expectedConstraints",
    )
    forbidden_signals: list[str] = Field(
        validation_alias=AliasChoices("forbidden_signals", "forbiddenSignals"),
        serialization_alias="forbiddenSignals",
    )
    allowed_outcomes: list[str] = Field(
        validation_alias=AliasChoices("allowed_outcomes", "allowedOutcomes"),
        serialization_alias="allowedOutcomes",
    )
    expected_memory_ids: list[str] = Field(
        validation_alias=AliasChoices("expected_memory_ids", "expectedMemoryIds"),
        serialization_alias="expectedMemoryIds",
    )
    allowed_evidence_message_ids: list[str] = Field(
        validation_alias=AliasChoices(
            "allowed_evidence_message_ids", "allowedEvidenceMessageIds"
        ),
        serialization_alias="allowedEvidenceMessageIds",
    )
    requires_postgres: bool = Field(
        validation_alias=AliasChoices("requires_postgres", "requiresPostgres"),
        serialization_alias="requiresPostgres",
    )
    requires_live_candidate: bool = Field(
        validation_alias=AliasChoices("requires_live_candidate", "requiresLiveCandidate"),
        serialization_alias="requiresLiveCandidate",
    )
    requires_live_embedding: bool = Field(
        validation_alias=AliasChoices("requires_live_embedding", "requiresLiveEmbedding"),
        serialization_alias="requiresLiveEmbedding",
    )
    judge_rubric: list[str] = Field(
        validation_alias=AliasChoices("judge_rubric", "judgeRubric"),
        serialization_alias="judgeRubric",
    )
    tags: list[str]

    @field_validator("case_id", mode="before")
    @classmethod
    def _strip_case_id(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalise_protocol(cls, value: Any) -> Any:
        return _canonical_protocol(value)

    @field_validator(
        "expected_constraints",
        "forbidden_signals",
        "allowed_outcomes",
        "expected_memory_ids",
        "allowed_evidence_message_ids",
        "judge_rubric",
        "tags",
    )
    @classmethod
    def _validate_string_lists(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("case list fields must contain non-empty strings")
        return [_normalise_text(value) for value in values]

    @model_validator(mode="after")
    def _validate_constraint_consistency(self) -> EvaluationCase:
        positive: set[str] = set()
        negative: set[str] = set()
        for value in (*self.expected_constraints, *self.allowed_outcomes):
            marker = _constraint_marker(value)
            if marker is not None:
                (negative if marker[0] else positive).add(marker[1])
        for value in self.forbidden_signals:
            marker = _constraint_marker(value)
            if marker is not None:
                (negative if marker[0] else positive).add(marker[1])
        conflict = sorted(positive & negative)
        if conflict:
            raise ValueError(f"contradictory must/must_not constraint: {conflict[0]}")
        return self


class CandidateObservation(EvaluationModel):
    """Normalized output of a candidate adapter for one case invocation."""

    case_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("case_id", "caseId"),
        serialization_alias="caseId",
    )
    run_index: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("run_index", "runIndex"),
        serialization_alias="runIndex",
    )
    protocol: EvaluationProtocol
    candidate_text: str = Field(
        default="",
        validation_alias=AliasChoices("candidate_text", "candidateText", "text"),
        serialization_alias="candidateText",
    )
    structured_output: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "structured_output",
            "structuredOutput",
            "candidate_output",
            "candidateOutput",
            "output",
        ),
        serialization_alias="structuredOutput",
    )
    schema_valid: bool = Field(
        default=True,
        validation_alias=AliasChoices("schema_valid", "schemaValid"),
        serialization_alias="schemaValid",
    )
    actual_action: str | None = Field(
        default=None,
        validation_alias=AliasChoices("actual_action", "actualAction", "action"),
        serialization_alias="actualAction",
    )
    goal_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("goal_id", "goalId"),
        serialization_alias="goalId",
    )
    target_actor_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("target_actor_id", "targetActorId"),
        serialization_alias="targetActorId",
    )
    actor_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("actor_ids", "actorIds"),
        serialization_alias="actorIds",
    )
    goal_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("goal_ids", "goalIds"),
        serialization_alias="goalIds",
    )
    evidence_message_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_message_ids", "evidenceMessageIds"),
        serialization_alias="evidenceMessageIds",
    )
    retrieved_memory_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("retrieved_memory_ids", "retrievedMemoryIds"),
        serialization_alias="retrievedMemoryIds",
    )
    retrieval_source: RetrievalSource = Field(
        default="fixture",
        validation_alias=AliasChoices("retrieval_source", "retrievalSource"),
        serialization_alias="retrievalSource",
    )
    memory_query_text: str = Field(
        default="",
        validation_alias=AliasChoices(
            "memory_query_text", "memoryQueryText", "query_text", "queryText"
        ),
        serialization_alias="memoryQueryText",
    )
    memory_query_actor_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("memory_query_actor_ids", "memoryQueryActorIds"),
        serialization_alias="memoryQueryActorIds",
    )
    memory_query_goal_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("memory_query_goal_ids", "memoryQueryGoalIds"),
        serialization_alias="memoryQueryGoalIds",
    )
    memory_query_topic_hints: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("memory_query_topic_hints", "memoryQueryTopicHints"),
        serialization_alias="memoryQueryTopicHints",
    )
    vector_hits: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("vector_hits", "vectorHits"),
        serialization_alias="vectorHits",
    )
    graph_hits: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("graph_hits", "graphHits"),
        serialization_alias="graphHits",
    )
    allowed_actor_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_actor_ids", "allowedActorIds"),
        serialization_alias="allowedActorIds",
    )
    allowed_goal_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_goal_ids", "allowedGoalIds"),
        serialization_alias="allowedGoalIds",
    )
    allowed_evidence_message_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "allowed_evidence_message_ids", "allowedEvidenceMessageIds"
        ),
        serialization_alias="allowedEvidenceMessageIds",
    )
    allowed_memory_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_memory_ids", "allowedMemoryIds"),
        serialization_alias="allowedMemoryIds",
    )
    owner_memory_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("owner_memory_ids", "ownerMemoryIds"),
        serialization_alias="ownerMemoryIds",
    )
    owner_npc_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("owner_npc_id", "ownerNpcId"),
        serialization_alias="ownerNpcId",
    )
    direct_question: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("direct_question", "directQuestion"),
        serialization_alias="directQuestion",
    )
    direct_question_answered: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("direct_question_answered", "directQuestionAnswered"),
        serialization_alias="directQuestionAnswered",
    )
    previous_candidate_texts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("previous_candidate_texts", "previousCandidateTexts"),
        serialization_alias="previousCandidateTexts",
    )
    retrieval_k: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("retrieval_k", "retrievalK", "k", "limit"),
        serialization_alias="retrievalK",
    )
    latency_ms: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("latency_ms", "latencyMs"),
        serialization_alias="latencyMs",
    )
    prompt_tokens: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("prompt_tokens", "promptTokens"),
        serialization_alias="promptTokens",
    )
    completion_tokens: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("completion_tokens", "completionTokens"),
        serialization_alias="completionTokens",
    )
    total_tokens: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("total_tokens", "totalTokens"),
        serialization_alias="totalTokens",
    )
    retries: int = Field(default=0, ge=0)
    failure_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices("failure_code", "failureCode"),
        serialization_alias="failureCode",
    )
    estimated_cost_cny: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("estimated_cost_cny", "estimatedCostCny"),
        serialization_alias="estimatedCostCny",
    )
    system_blocked: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("system_blocked", "systemBlocked"),
        serialization_alias="systemBlocked",
    )
    end_to_end_safety_failure: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "end_to_end_safety_failure",
            "endToEndSafetyFailure",
        ),
        serialization_alias="endToEndSafetyFailure",
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalise_protocol(cls, value: Any) -> Any:
        return _canonical_protocol(value)


class RuleScore(EvaluationModel):
    """Deterministic rule result; a Judge score can never override its gates."""

    hard_failure: bool = False
    failures: list[str] = Field(default_factory=list)
    schema_valid: bool = True
    protocol_schema_valid: bool = True
    case_constraint_valid: bool = True
    candidate_violation: bool = False
    system_blocked: bool | None = None
    end_to_end_safety_failure: bool | None = None
    action_valid: bool = True
    ids_valid: bool = True
    evidence_valid: bool = True
    query_scope_valid: bool = True
    retrieval_scope_valid: bool = True
    committed_evidence_scope_valid: bool = True
    owner_boundary_valid: bool = True
    safety_valid: bool = True
    canary_leak_count: int = Field(default=0, ge=0)
    forbidden_signal_count: int = Field(default=0, ge=0)
    owner_leak_count: int = Field(default=0, ge=0)
    internal_field_leak_count: int = Field(default=0, ge=0)
    invalid_action_count: int = Field(default=0, ge=0)
    invalid_id_count: int = Field(default=0, ge=0)
    invalid_evidence_count: int = Field(default=0, ge=0)
    unauthorized_memory_count: int = Field(default=0, ge=0)
    memory_tool_call_count: int = Field(default=0, ge=0)
    memory_tool_limit_valid: bool = True
    precision_at_k: float = Field(default=0.0, ge=0, le=1)
    # ``precision_at_k`` is the historical strict K-denominator metric.  The
    # explicit spelling below makes it impossible for a consumer to mistake
    # the newer returned-count metric for a replacement of that baseline.
    strict_precision_at_k: float = Field(
        default=0.0,
        ge=0,
        le=1,
        validation_alias=AliasChoices("strict_precision_at_k", "strictPrecisionAtK"),
        serialization_alias="strictPrecisionAtK",
    )
    precision_at_returned: float = Field(
        default=0.0,
        ge=0,
        le=1,
        validation_alias=AliasChoices("precision_at_returned", "precisionAtReturned"),
        serialization_alias="precisionAtReturned",
    )
    false_positive_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        validation_alias=AliasChoices("false_positive_rate", "falsePositiveRate"),
        serialization_alias="falsePositiveRate",
    )
    false_positive_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("false_positive_count", "falsePositiveCount"),
        serialization_alias="falsePositiveCount",
    )
    empty_query_correct: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("empty_query_correct", "emptyQueryCorrect"),
        serialization_alias="emptyQueryCorrect",
    )
    duplicate_result_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("duplicate_result_count", "duplicateResultCount"),
        serialization_alias="duplicateResultCount",
    )
    retrieval_source: RetrievalSource = Field(
        default="fixture",
        validation_alias=AliasChoices("retrieval_source", "retrievalSource"),
        serialization_alias="retrievalSource",
    )
    recall_at_k: float = Field(default=0.0, ge=0, le=1)
    mrr: float = Field(default=0.0, ge=0, le=1)
    retrieval_k: int = Field(default=0, ge=0)
    vector_hits: int = Field(default=0, ge=0)
    graph_hits: int = Field(default=0, ge=0)
    retrieval_empty: bool = False
    direct_question_pass: bool | None = None
    repetition_detected: bool = False
    repetition_score: float = Field(default=0.0, ge=0, le=1)
    latency_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    retries: int = Field(default=0, ge=0)
    estimated_cost_cny: float | None = Field(default=None, ge=0)

    @property
    def canary_leaks(self) -> int:
        return self.canary_leak_count

    @property
    def owner_leaks(self) -> int:
        return self.owner_leak_count

    @property
    def internal_field_leaks(self) -> int:
        return self.internal_field_leak_count

    @property
    def direct_question_answered(self) -> bool | None:
        return self.direct_question_pass

    @property
    def repetition(self) -> bool:
        return self.repetition_detected


class CaseResult(EvaluationModel):
    """A privacy-safe case projection emitted by :func:`report.build_report`.

    The runner keeps richer per-run dictionaries internally.  The report
    projection intentionally stores those dictionaries as opaque JSON
    objects after ``report.py`` has removed provider and private fields.  The
    top-level fields are still explicit so a generated report can be
    validated without silently accepting misspelled report keys.
    """

    case_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("case_id", "caseId"),
        serialization_alias="caseId",
    )
    case_version: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("case_version", "caseVersion"),
        serialization_alias="caseVersion",
    )
    category: str | None = None
    protocol: str | None = None
    status: str = "completed"
    runs: list[dict[str, Any]] = Field(default_factory=list)
    rule_scores: list[RuleScore | dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("rule_scores", "ruleScores"),
        serialization_alias="ruleScores",
    )
    rule_score: RuleScore | dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("rule_score", "ruleScore"),
        serialization_alias="ruleScore",
    )
    judge_scores: list[JudgeScore | dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("judge_scores", "judgeScores"),
        serialization_alias="judgeScores",
    )
    judge_score: JudgeScore | dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("judge_score", "judgeScore"),
        serialization_alias="judgeScore",
    )
    judge_disagreement: bool = Field(
        default=False,
        validation_alias=AliasChoices("judge_disagreement", "judgeDisagreement"),
        serialization_alias="judgeDisagreement",
    )
    judge_effective_confidence: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "judge_effective_confidence", "judgeEffectiveConfidence"
        ),
        serialization_alias="judgeEffectiveConfidence",
    )
    candidate_summary: str = Field(
        default="",
        validation_alias=AliasChoices("candidate_summary", "candidateSummary"),
        serialization_alias="candidateSummary",
    )
    review_reasons: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("review_reasons", "reviewReasons"),
        serialization_alias="reviewReasons",
    )
    error_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices("error_code", "errorCode"),
        serialization_alias="errorCode",
    )
    required: bool | None = None
    skipped: bool = False


class EvaluationReport(EvaluationModel):
    """Top-level serializable evaluation report skeleton."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    rule_based_metrics: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("rule_based_metrics", "ruleBasedMetrics"),
        serialization_alias="ruleBasedMetrics",
    )
    llm_judge_metrics: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("llm_judge_metrics", "llmJudgeMetrics"),
        serialization_alias="llmJudgeMetrics",
    )
    combined_result: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("combined_result", "combinedResult"),
        serialization_alias="combinedResult",
    )
    judge_calibration: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("judge_calibration", "judgeCalibration"),
        serialization_alias="judgeCalibration",
    )
    cases: list[CaseResult] = Field(default_factory=list)
    bad_cases: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("bad_cases", "badCases"),
        serialization_alias="badCases",
    )
    review_queue: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("review_queue", "reviewQueue"),
        serialization_alias="reviewQueue",
    )


__all__ = [
    "CaseCategory",
    "CaseResult",
    "CandidateObservation",
    "EvaluationCase",
    "EvaluationModel",
    "EvaluationProtocol",
    "EvaluationReport",
    "JudgeScore",
    "NpcId",
    "RuleScore",
    "RetrievalSource",
]
