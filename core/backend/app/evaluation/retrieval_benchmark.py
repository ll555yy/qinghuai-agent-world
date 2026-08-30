"""DatabaseMemoryRetriever benchmark helpers.

The benchmark is intentionally an orchestration layer, not another retrieval
implementation.  Every case invokes ``DatabaseMemoryRetriever.search`` and
records the result by split and retrieval phase.  Fixture observations cannot
enter this report, which keeps PostgreSQL evidence separate from the historical
fixture Precision@K baseline.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ..ai.protocols import MemoryQuery
from ..persistence.memory_retriever import DatabaseMemoryRetriever
from .rule_scorer import retrieval_metrics

RetrievalPhase = Literal["vector", "keyword", "actor", "goal", "topic", "graph"]
RetrievalSplit = Literal["tuning", "holdout"]


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkCase:
    """One synthetic, owner-scoped query in the benchmark dataset."""

    case_id: str
    run_id: str
    owner_npc_id: str
    query: MemoryQuery | Mapping[str, Any] | None
    expected_memory_ids: tuple[str, ...] = ()
    retrieval_k: int = 3
    phase: RetrievalPhase = "keyword"
    split: RetrievalSplit = "tuning"
    owner_memory_ids: tuple[str, ...] = ()
    retrieval_source: Literal["postgres"] = "postgres"

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.run_id.strip() or not self.owner_npc_id.strip():
            raise ValueError("benchmark identity fields must be non-empty")
        if self.retrieval_k < 1:
            raise ValueError("retrieval_k must be >= 1")
        if self.phase not in {"vector", "keyword", "actor", "goal", "topic", "graph"}:
            raise ValueError(f"unsupported retrieval phase: {self.phase}")
        if self.split not in {"tuning", "holdout"}:
            raise ValueError(f"unsupported retrieval split: {self.split}")


@dataclass(frozen=True, slots=True)
class RetrievalAcceptanceThresholds:
    """Pre-registered, interpretable holdout thresholds."""

    precision_at_returned: float = 0.90
    recall_at_k: float = 0.90
    false_positive_rate: float = 0.10
    mrr_not_below_baseline: bool = True
    require_no_duplicates: bool = True
    require_empty_query_correct: bool = True
    require_owner_boundary: bool = True

    def __post_init__(self) -> None:
        for name in ("precision_at_returned", "recall_at_k", "false_positive_rate"):
            value = float(getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")


def _query(value: MemoryQuery | Mapping[str, Any] | None, limit: int) -> tuple[Any, bool]:
    """Build a MemoryQuery while allowing a deliberately empty-query case."""

    if isinstance(value, MemoryQuery):
        return value, not (
            value.query_text.strip() or value.actor_ids or value.goal_ids or value.topic_hints
        )
    raw = dict(value or {})
    query_text = raw.get("queryText", raw.get("query_text", ""))
    actor_ids = list(raw.get("actorIds", raw.get("actor_ids", [])) or [])
    goal_ids = list(raw.get("goalIds", raw.get("goal_ids", [])) or [])
    topic_hints = list(raw.get("topicHints", raw.get("topic_hints", [])) or [])
    is_empty = not (isinstance(query_text, str) and query_text.strip()) and not (
        actor_ids or goal_ids or topic_hints
    )
    if is_empty:
        # DatabaseMemoryRetriever only reads the MemoryQuery fields; using
        # model_construct lets the benchmark verify the safe empty-query
        # result without weakening the public MemoryQuery validator.
        return (
            MemoryQuery.model_construct(
                query_text="",
                actor_ids=actor_ids,
                goal_ids=goal_ids,
                topic_hints=topic_hints,
                limit=int(raw.get("limit", limit) or limit),
            ),
            True,
        )
    return (
        MemoryQuery(
            queryText=str(query_text or ""),
            actorIds=actor_ids,
            goalIds=goal_ids,
            topicHints=topic_hints,
            limit=int(raw.get("limit", limit) or limit),
        ),
        False,
    )


def _field(result: object, *names: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        for name in names:
            if name in result:
                return result[name]
    for name in names:
        if hasattr(result, name):
            return getattr(result, name)
    return default


def _mean(items: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [
        float(item[key])
        for item in items
        if isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool)
    ]
    return round(statistics.fmean(values), 6) if values else None


def _aggregate(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "cases": 0,
            "strictPrecisionAtK": None,
            "precisionAtReturned": None,
            "recallAtK": None,
            "mrr": None,
            "falsePositiveRate": None,
            "falsePositiveCount": 0,
            "duplicateResultCount": 0,
            "emptyQueryCases": 0,
            "emptyQueryCorrect": None,
            "ownerBoundaryViolations": 0,
        }
    empty = [item for item in items if item.get("queryIsEmpty") is True]
    ranked = [item for item in items if item.get("queryIsEmpty") is not True]
    return {
        "cases": len(items),
        # Empty queries have their own safety metric and must not inflate
        # precision/recall or dilute MRR for actual retrieval queries.
        "rankingCases": len(ranked),
        "strictPrecisionAtK": _mean(ranked, "strictPrecisionAtK"),
        "precisionAtReturned": _mean(ranked, "precisionAtReturned"),
        "recallAtK": _mean(ranked, "recallAtK"),
        "mrr": _mean(ranked, "mrr"),
        "falsePositiveRate": _mean(ranked, "falsePositiveRate"),
        "falsePositiveCount": sum(int(item.get("falsePositiveCount", 0) or 0) for item in items),
        "duplicateResultCount": sum(int(item.get("duplicateResultCount", 0) or 0) for item in items),
        "emptyQueryCases": len(empty),
        "emptyQueryCorrect": (
            round(sum(bool(item.get("emptyQueryCorrect")) for item in empty) / len(empty), 6)
            if empty
            else None
        ),
        "ownerBoundaryViolations": sum(int(item.get("ownerBoundaryViolations", 0) or 0) for item in items),
    }


async def benchmark_database_memory_retriever(
    retriever: DatabaseMemoryRetriever,
    cases: Iterable[RetrievalBenchmarkCase],
    *,
    thresholds: RetrievalAcceptanceThresholds | None = None,
    baseline_mrr: float | None = None,
) -> dict[str, Any]:
    """Run real ``search`` calls and return split/phase-isolated metrics.

    The function intentionally has no fixture fallback.  A fake object is
    useful in unit tests only when it implements the same ``search`` contract;
    production benchmark callers should pass a ``DatabaseMemoryRetriever``.
    """

    selected = list(cases)
    if any(item.retrieval_source != "postgres" for item in selected):
        raise ValueError("PostgreSQL benchmark accepts retrievalSource=postgres only")
    if not hasattr(retriever, "search") or not callable(retriever.search):
        raise TypeError("retriever must provide an async search method")
    threshold = thresholds or RetrievalAcceptanceThresholds()
    observations: list[dict[str, Any]] = []
    for case in selected:
        query, query_is_empty = _query(case.query, case.retrieval_k)
        result = await retriever.search(
            run_id=case.run_id,
            owner_npc_id=case.owner_npc_id,
            query=query,
        )
        recalled = tuple(str(value) for value in (_field(result, "memory_ids", "memoryIds", default=()) or ()))
        metrics = retrieval_metrics(
            case.expected_memory_ids,
            recalled,
            case.retrieval_k,
            query_is_empty=query_is_empty,
        )
        owner_set = set(case.owner_memory_ids)
        owner_violations = sum(1 for memory_id in recalled if owner_set and memory_id not in owner_set)
        observations.append(
            {
                "caseId": case.case_id,
                "split": case.split,
                "phase": case.phase,
                "retrievalSource": "postgres",
                "queryIsEmpty": query_is_empty,
                "retrievedMemoryIds": list(recalled),
                "expectedMemoryIds": list(case.expected_memory_ids),
                "strictPrecisionAtK": metrics["strict_precision_at_k"],
                "precisionAtReturned": metrics["precision_at_returned"],
                "recallAtK": metrics["recall_at_k"],
                "mrr": metrics["mrr"],
                "falsePositiveRate": metrics["false_positive_rate"],
                "falsePositiveCount": metrics["false_positive_count"],
                "duplicateResultCount": metrics["duplicate_result_count"],
                "emptyQueryCorrect": metrics["empty_query_correct"],
                "vectorHits": int(_field(result, "vector_hits", "vectorHits", default=0) or 0),
                "graphHits": int(_field(result, "graph_hits", "graphHits", default=0) or 0),
                "ownerBoundaryViolations": owner_violations,
            }
        )

    by_split = {
        split: _aggregate([item for item in observations if item["split"] == split])
        for split in ("tuning", "holdout")
    }
    by_phase = {
        phase: {
            split: _aggregate(
                [
                    item
                    for item in observations
                    if item["phase"] == phase and item["split"] == split
                ]
            )
            for split in ("tuning", "holdout")
        }
        for phase in ("vector", "keyword", "actor", "goal", "topic", "graph")
    }
    holdout = by_split["holdout"]
    checks = {
        "precisionAtReturned": holdout["precisionAtReturned"] is not None
        and holdout["precisionAtReturned"] >= threshold.precision_at_returned,
        "recallAtK": holdout["recallAtK"] is not None
        and holdout["recallAtK"] >= threshold.recall_at_k,
        "falsePositiveRate": holdout["falsePositiveRate"] is not None
        and holdout["falsePositiveRate"] <= threshold.false_positive_rate,
        "noOwnerBoundaryViolations": holdout["ownerBoundaryViolations"] == 0,
        "noDuplicateResults": (
            not threshold.require_no_duplicates or holdout["duplicateResultCount"] == 0
        ),
        "emptyQueryCorrect": (
            not threshold.require_empty_query_correct
            or holdout["emptyQueryCases"] == 0
            or holdout["emptyQueryCorrect"] == 1.0
        ),
        "mrrNotBelowBaseline": (
            not threshold.mrr_not_below_baseline
            or baseline_mrr is None
            or (
                holdout["mrr"] is not None
                and holdout["mrr"] >= float(baseline_mrr)
            )
        ),
    }
    return {
        "schemaVersion": 1,
        "retrievalSource": "postgres",
        "cases": observations,
        "caseCount": len(observations),
        "splitMetrics": by_split,
        "phaseMetrics": by_phase,
        "baselineMRR": baseline_mrr,
        "thresholds": {
            "precisionAtReturned": threshold.precision_at_returned,
            "recallAtK": threshold.recall_at_k,
            "falsePositiveRate": threshold.false_positive_rate,
            "mrrNotBelowBaseline": threshold.mrr_not_below_baseline,
        },
        "acceptanceChecks": checks,
        "holdoutAccepted": bool(checks) and all(checks.values()),
        "retrieverSearchCalls": len(observations),
    }


# Explicit aliases make the intended operation easy to discover without
# creating a second implementation or a second metric denominator.
run_postgres_retrieval_benchmark = benchmark_database_memory_retriever
run_postgres_benchmark = benchmark_database_memory_retriever


def render_retrieval_benchmark_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact, source-labelled benchmark report."""

    lines = [
        "# PostgreSQL memory retrieval benchmark",
        "",
        f"- Retrieval source: `{report.get('retrievalSource')}`",
        f"- Search calls: `{report.get('retrieverSearchCalls', 0)}`",
        f"- Holdout accepted: `{report.get('holdoutAccepted', False)}`",
        "",
        "| Split | Cases | Precision@returned | Recall@K | FPR | Duplicates | Owner violations |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    split_metrics = report.get("splitMetrics", {})
    if isinstance(split_metrics, Mapping):
        for split in ("tuning", "holdout"):
            metric = split_metrics.get(split, {})
            if not isinstance(metric, Mapping):
                continue
            lines.append(
                f"| `{split}` | {metric.get('cases', 0)} | {metric.get('precisionAtReturned')} | "
                f"{metric.get('recallAtK')} | {metric.get('falsePositiveRate')} | "
                f"{metric.get('duplicateResultCount', 0)} | {metric.get('ownerBoundaryViolations', 0)} |"
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "RetrievalAcceptanceThresholds",
    "RetrievalBenchmarkCase",
    "RetrievalPhase",
    "RetrievalSplit",
    "benchmark_database_memory_retriever",
    "render_retrieval_benchmark_markdown",
    "run_postgres_benchmark",
    "run_postgres_retrieval_benchmark",
]
