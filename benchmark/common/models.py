from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class AttemptStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INFRA_INVALID = "infra_invalid"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    afp_five_hour_cap: float = 2000.0
    reserve_afp: float = 100.0
    max_concurrency: int = 1
    require_live_meter: bool = True

    def __post_init__(self) -> None:
        if self.afp_five_hour_cap <= 0:
            raise ValueError("afp_five_hour_cap must be positive")
        if not 0 <= self.reserve_afp < self.afp_five_hour_cap:
            raise ValueError("reserve_afp must be non-negative and below the cap")
        if self.max_concurrency != 1:
            raise ValueError("live P0 benchmarks require max_concurrency=1")

    @property
    def usable_afp(self) -> float:
        return self.afp_five_hour_cap - self.reserve_afp


@dataclass(slots=True)
class TelemetryRecord:
    candidate_calls: int = 0
    candidate_physical_requests: int = 0
    candidate_retries: int = 0
    embedding_calls: int = 0
    embedding_physical_requests: int = 0
    embedding_retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0
    afp_used: float | None = None
    cost_cny_estimated: float | None = None
    step_latencies_ms: list[float] = field(default_factory=list)
    retrieval_latencies_ms: list[float] = field(default_factory=list)
    run_duration_ms: float | None = None
    failures: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.embedding_tokens

    def as_dict(self) -> dict[str, Any]:
        from .statistics import percentile

        value = asdict(self)
        value.update(
            {
                "totalTokens": self.total_tokens,
                "stepLatencyP50Ms": percentile(self.step_latencies_ms, 50),
                "stepLatencyP95Ms": percentile(self.step_latencies_ms, 95),
                "retrievalLatencyP50Ms": percentile(self.retrieval_latencies_ms, 50),
                "retrievalLatencyP95Ms": percentile(self.retrieval_latencies_ms, 95),
            }
        )
        return value


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    suite: str
    execution_mode: str
    seeds: tuple[int, ...]
    scenario_digest: str
    prompt_digest: str
    dataset_digest: str | None = None
    candidate_model: str | None = None
    embedding_model: str | None = None
    temperature: float | None = None
    preregistered_thresholds: Mapping[str, float] = field(default_factory=dict)
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    schema_version: str = "1.0"
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if self.suite not in {"business", "memory", "reliability", "all"}:
            raise ValueError(f"unknown suite: {self.suite}")
        if self.execution_mode not in {"offline", "pilot", "live"}:
            raise ValueError(f"unknown execution_mode: {self.execution_mode}")
        if not self.seeds:
            raise ValueError("at least one frozen seed is required")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique")
        if not self.scenario_digest or not self.prompt_digest:
            raise ValueError("scenario_digest and prompt_digest are required")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["seeds"] = list(self.seeds)
        return value


@dataclass(slots=True)
class AttemptRecord:
    experiment_id: str
    case_id: str
    condition_id: str
    seed: int
    paired_group_id: str
    attempt_index: int = 0
    route: str | None = None
    status: AttemptStatus = AttemptStatus.PLANNED
    infra_valid: bool | None = None
    gameplay_success: bool | None = None
    completion_rounds: int | None = None
    failure_type: str | None = None
    telemetry: TelemetryRecord = field(default_factory=TelemetryRecord)
    started_at: str | None = None
    completed_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["telemetry"] = self.telemetry.as_dict()
        return value


@dataclass(frozen=True, slots=True)
class ResumeMetrics:
    experiment_id: str
    sample_size: int
    primary_metric_name: str
    primary_metric_value: float | None
    primary_metric_unit: str
    baselines: tuple[Mapping[str, Any], ...] = ()
    effect_size: float | None = None
    confidence_interval_95: tuple[float, float] | None = None
    afp_per_success: float | None = None
    cost_per_success_cny_estimated: float | None = None
    p95_latency_ms: float | None = None
    limitations: tuple[str, ...] = ()
    hypothesis_verified: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def telemetry_from_mapping(value: Mapping[str, Any]) -> TelemetryRecord:
    known = {item.name for item in __import__("dataclasses").fields(TelemetryRecord)}
    return TelemetryRecord(**{key: raw for key, raw in value.items() if key in known})


__all__ = [
    "AttemptRecord",
    "AttemptStatus",
    "BudgetPolicy",
    "ExperimentManifest",
    "ResumeMetrics",
    "TelemetryRecord",
    "telemetry_from_mapping",
    "utc_now_iso",
]
