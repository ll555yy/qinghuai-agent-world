"""Strict, provider-neutral contracts for semantic Judge evaluations.

The evaluation package deliberately keeps these contracts separate from the
playable-world contracts.  A Judge may describe a candidate response, but it
must never be able to submit a world mutation or an authoritative identifier.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

DIMENSION_NAMES: tuple[str, ...] = (
    "persona_consistency",
    "context_faithfulness",
    "response_relevance",
    "naturalness",
    "goal_progress",
    "player_agency",
)


class JudgeProtocolModel(BaseModel):
    """Base class used for every value crossing the Judge boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
    )


class MajorIssue(StrEnum):
    """The finite set of issues that can be emitted by a Judge."""

    NONE = "none"
    PERSONA_BREAK = "persona_break"
    CONTEXT_UNFAITHFUL = "context_unfaithful"
    IRRELEVANT = "irrelevant"
    UNNATURAL = "unnatural"
    GOAL_STALLED = "goal_stalled"
    AGENCY_REDUCED = "agency_reduced"
    CONTRADICTION = "contradiction"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    DIRECT_QUESTION_MISSED = "direct_question_missed"
    REPETITION = "repetition"
    SAFETY = "safety"
    INJECTION_ATTEMPT = "injection_attempt"
    SCHEMA_VIOLATION = "schema_violation"
    OTHER = "other"


class ReviewReason(StrEnum):
    """Reasons for sending an evaluation to human review."""

    JUDGE_DISAGREEMENT = "judge_disagreement"
    LOW_CONFIDENCE = "low_confidence"
    CONTRADICTION_DETECTED = "contradiction_detected"
    UNSUPPORTED_CLAIM_DETECTED = "unsupported_claim_detected"
    DIRECT_QUESTION_UNANSWERED = "direct_question_unanswered"
    RULE_JUDGE_CONFLICT = "rule_judge_conflict"
    FORMAT_ERROR = "format_error"
    PROVIDER_ERROR = "provider_error"
    INJECTION_ATTEMPT = "injection_attempt"


Confidence = Literal["low", "medium", "high"]


class JudgeEvidence(JudgeProtocolModel):
    """Exactly one bounded explanation for each semantic dimension."""

    persona_consistency: Annotated[str, Field(min_length=1, max_length=64)]
    context_faithfulness: Annotated[str, Field(min_length=1, max_length=64)]
    response_relevance: Annotated[str, Field(min_length=1, max_length=64)]
    naturalness: Annotated[str, Field(min_length=1, max_length=64)]
    goal_progress: Annotated[str, Field(min_length=1, max_length=64)]
    player_agency: Annotated[str, Field(min_length=1, max_length=64)]


class JudgeScore(JudgeProtocolModel):
    """One strictly validated six-dimensional semantic assessment.

    Scores are intentionally flat so that report builders can aggregate them
    without understanding a nested provider response.  ``evidence`` must
    contain one short explanation for every dimension; it is not trusted as a
    source of world state.
    """

    DIMENSIONS: ClassVar[tuple[str, ...]] = DIMENSION_NAMES

    persona_consistency: Annotated[int, Field(ge=1, le=5)]
    context_faithfulness: Annotated[int, Field(ge=1, le=5)]
    response_relevance: Annotated[int, Field(ge=1, le=5)]
    naturalness: Annotated[int, Field(ge=1, le=5)]
    goal_progress: Annotated[int, Field(ge=1, le=5)]
    player_agency: Annotated[int, Field(ge=1, le=5)]

    evidence: JudgeEvidence
    contradiction_detected: bool = Field(
        validation_alias=AliasChoices("contradiction_detected", "contradictionDetected"),
    )
    unsupported_claim_detected: bool = Field(
        validation_alias=AliasChoices("unsupported_claim_detected", "unsupportedClaimDetected"),
    )
    direct_question_answered: bool = Field(
        validation_alias=AliasChoices("direct_question_answered", "directQuestionAnswered"),
    )
    major_issues: list[MajorIssue] = Field(
        max_length=8,
        validation_alias=AliasChoices("major_issues", "majorIssues"),
    )
    confidence: Confidence

    @field_validator("major_issues", mode="before")
    @classmethod
    def _parse_wire_issue_names(cls, values: object) -> object:
        """Convert only known JSON enum strings before strict validation."""

        if not isinstance(values, list):
            return values
        return [
            MajorIssue(value) if isinstance(value, str) else value
            for value in values
        ]

    @field_validator("major_issues")
    @classmethod
    def _unique_issues(cls, values: list[MajorIssue]) -> list[MajorIssue]:
        if len(set(values)) != len(values):
            raise ValueError("major_issues must not contain duplicates")
        return values

    @property
    def sum_score(self) -> int:
        """Return the six-score sum for diagnostics only."""

        return sum(getattr(self, name) for name in self.DIMENSIONS)

    @property
    def total_score(self) -> float:
        """Return the locally computed arithmetic mean required by the rubric."""

        return round(self.sum_score / len(self.DIMENSIONS), 2)

    @property
    def average_score(self) -> float:
        """Compatibility spelling for the locally computed total score."""

        return self.total_score

    @property
    def overall_score(self) -> float:
        """Compatibility spelling used by report consumers."""

        return self.average_score


class JudgeMetrics(JudgeProtocolModel):
    """Content-free usage and cost counters for one evaluation."""

    calls: int = Field(default=0, ge=0)
    format_retries: int = Field(default=0, ge=0)
    provider_retries: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    estimated_cost_cny: float = Field(default=0.0, ge=0.0)
    format_error_codes: list[str] = Field(default_factory=list, max_length=16)

    @property
    def request_count(self) -> int:
        return self.calls

    @property
    def retries(self) -> int:
        return self.format_retries + self.provider_retries


class JudgeEvaluation(JudgeProtocolModel):
    """The non-fabricated result of one or two Judge calls."""

    score: JudgeScore | None = None
    duplicate_score: JudgeScore | None = Field(
        default=None,
        validation_alias=AliasChoices("duplicate_score", "duplicateScore"),
    )
    judge_disagreement: bool = Field(
        default=False,
        validation_alias=AliasChoices("judge_disagreement", "judgeDisagreement"),
    )
    disagreement_dimensions: list[str] = Field(
        default_factory=list,
        max_length=len(DIMENSION_NAMES),
        validation_alias=AliasChoices("disagreement_dimensions", "disagreementDimensions"),
    )
    review_reasons: list[ReviewReason] = Field(
        default_factory=list,
        max_length=16,
        validation_alias=AliasChoices("review_reasons", "reviewReasons"),
    )
    metrics: JudgeMetrics
    error_code: str | None = Field(default=None, max_length=80)
    candidate_summary: str = Field(default="", max_length=500)

    @field_validator("disagreement_dimensions")
    @classmethod
    def _known_dimensions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("disagreement_dimensions must not contain duplicates")
        unknown = set(values) - set(DIMENSION_NAMES)
        if unknown:
            raise ValueError(f"unknown disagreement dimensions: {sorted(unknown)}")
        return values

    @field_validator("review_reasons")
    @classmethod
    def _unique_review_reasons(cls, values: list[ReviewReason]) -> list[ReviewReason]:
        if len(set(values)) != len(values):
            raise ValueError("review_reasons must not contain duplicates")
        return values

    @property
    def scores(self) -> tuple[JudgeScore, ...]:
        return tuple(score for score in (self.score, self.duplicate_score) if score is not None)


# A shorter spelling is useful to callers that use ``Result`` as their local
# naming convention; it is the same strict contract, not a second schema.
JudgeResult = JudgeEvaluation


__all__ = [
    "Confidence",
    "DIMENSION_NAMES",
    "JudgeEvaluation",
    "JudgeEvidence",
    "JudgeMetrics",
    "JudgeProtocolModel",
    "JudgeResult",
    "JudgeScore",
    "MajorIssue",
    "ReviewReason",
]
