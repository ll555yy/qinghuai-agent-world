"""Deterministic retrieval metrics and paired ablation effects."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _top(values: Sequence[Any], k: int) -> tuple[str, ...]:
    return tuple(str(value) for value in values[: max(0, int(k))])


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(percentile) / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _ndcg(expected: set[str], retrieved: Sequence[str], k: int) -> float:
    top = retrieved[: max(0, int(k))]
    if not expected:
        return 1.0 if not top else 0.0
    seen_relevant: set[str] = set()
    dcg = 0.0
    for index, memory_id in enumerate(top):
        if memory_id in expected and memory_id not in seen_relevant:
            dcg += 1.0 / math.log2(index + 2)
            seen_relevant.add(memory_id)
    ideal_hits = min(len(expected), max(0, int(k)))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / ideal if ideal else 0.0


def score_retrieval(
    expected_memory_ids: Sequence[str] | Iterable[str],
    retrieved_memory_ids: Sequence[str] | Iterable[str],
    k: int = 5,
    *,
    owner_memory_ids: Sequence[str] | Iterable[str] = (),
    distractor_memory_ids: Sequence[str] | Iterable[str] = (),
    query_is_empty: bool = False,
) -> dict[str, float | int | bool | None]:
    """Score one owner-scoped ranked result.

    Precision@5 intentionally uses a fixed ``k`` denominator.  The separate
    ``precision_at_returned`` metric makes short results visible without
    changing the historical fixed-K number.  Empty queries are scored as a
    safety contract and can be excluded from ranking aggregates by callers.
    """

    k = max(1, int(k))
    expected = tuple(dict.fromkeys(str(value) for value in expected_memory_ids))
    expected_set = set(expected)
    retrieved = tuple(str(value) for value in retrieved_memory_ids)
    top_k = _top(retrieved, k)
    top_1 = top_k[:1]
    unique_hits_k = len(set(top_k) & expected_set)
    unique_hits_1 = len(set(top_1) & expected_set)
    relevant_returned = sum(value in expected_set for value in retrieved)
    returned_count = len(retrieved)
    false_positive_count = sum(value not in expected_set for value in retrieved)
    duplicate_count = returned_count - len(set(retrieved))
    owner_set = {str(value) for value in owner_memory_ids}
    distractor_set = {str(value) for value in distractor_memory_ids}
    if owner_set:
        owner_violations = sum(value not in owner_set for value in retrieved)
    else:
        owner_violations = sum(value in distractor_set for value in retrieved)
    first_relevant = next(
        (index for index, value in enumerate(top_k, start=1) if value in expected_set),
        None,
    )
    if expected_set:
        recall_1 = unique_hits_1 / len(expected_set)
        recall_k = unique_hits_k / len(expected_set)
        mrr = 1.0 / first_relevant if first_relevant is not None else 0.0
    else:
        # A negative query is correct only when no memory is returned.  This
        # convention keeps hard-negative FPR and ranking metrics aligned.
        recall_1 = 1.0 if not top_1 else 0.0
        recall_k = 1.0 if not top_k else 0.0
        mrr = 1.0 if not top_k else 0.0
    empty_correct: bool | None = None
    if query_is_empty:
        empty_correct = not expected_set and not retrieved
    return {
        "strict_precision_at_k": unique_hits_k / k,
        "precision_at_1": float(unique_hits_1),
        "precision_at_5": unique_hits_k / k,
        "precision_at_returned": (
            relevant_returned / returned_count
            if returned_count
            else (1.0 if not expected_set else 0.0)
        ),
        "recall_at_1": recall_1,
        "recall_at_5": recall_k,
        "recall_at_k": recall_k,
        "ndcg_at_5": _ndcg(expected_set, top_k, k),
        "mrr": mrr,
        "false_positive_rate": (
            false_positive_count / returned_count if returned_count else 0.0
        ),
        "false_positive_count": false_positive_count,
        "owner_boundary_violations": owner_violations,
        "owner_boundary_violation_rate": (
            owner_violations / returned_count if returned_count else 0.0
        ),
        "duplicate_result_count": duplicate_count,
        "duplicate_result_rate": duplicate_count / returned_count if returned_count else 0.0,
        "empty_query_correct": empty_correct,
        "returned_count": returned_count,
        "relevant_returned_count": relevant_returned,
    }


# Compatibility aliases for callers that use the wording in the plan.
retrieval_metrics = score_retrieval
score_memory_retrieval = score_retrieval


_NUMERIC_METRICS = (
    "precision_at_1",
    "precision_at_5",
    "precision_at_returned",
    "recall_at_1",
    "recall_at_5",
    "recall_at_k",
    "ndcg_at_5",
    "mrr",
    "false_positive_rate",
    "owner_boundary_violation_rate",
    "duplicate_result_rate",
)


def _field(item: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return default


def _flat_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    nested = _field(item, "metrics", "metric", default=None)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            result.setdefault(str(key), value)
    return result


def _metric_values(items: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        value = _field(item, key, _camel_metric(key), default=None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _camel_metric(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def _summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies: list[float] = []
    for item in items:
        value = _field(
            item,
            "latency_ms",
            "latencyMs",
            "retrieval_latency_ms",
            "retrievalLatencyMs",
            default=None,
        )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            latencies.append(float(value))
    errors = sum(
        1
        for item in items
        if _field(item, "error", "error_code", "errorCode", "failed", default=None)
    )
    result: dict[str, Any] = {
        "cases": len(items),
        "completed_cases": len(items) - errors,
        "error_cases": errors,
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
    }
    for metric in _NUMERIC_METRICS:
        result[metric] = _mean(_metric_values(items, metric))
    for metric in (
        "false_positive_count",
        "owner_boundary_violations",
        "duplicate_result_count",
    ):
        values = _metric_values(items, metric)
        result[metric] = int(sum(values)) if values else 0
    empty_values = [
        bool(value)
        for item in items
        for value in [_field(item, "empty_query_correct", "emptyQueryCorrect", default=None)]
        if isinstance(value, bool)
    ]
    result["empty_query_cases"] = sum(
        1
        for item in items
        if _field(item, "query_is_empty", "queryIsEmpty", default=False) is True
    )
    result["empty_query_correct"] = (
        sum(empty_values) / len(empty_values) if empty_values else None
    )
    # Camel-case metrics make aggregate.json convenient to consume from the
    # existing backend report tooling; snake_case remains canonical in Python.
    result.update(
        {
            "precisionAt5": result["precision_at_5"],
            "recallAt1": result["recall_at_1"],
            "recallAt5": result["recall_at_5"],
            "ndcgAt5": result["ndcg_at_5"],
            "falsePositiveRate": result["false_positive_rate"],
            "ownerBoundaryViolations": result["owner_boundary_violations"],
            "duplicateResultCount": result["duplicate_result_count"],
            "latencyP50Ms": result["latency_p50_ms"],
            "latencyP95Ms": result["latency_p95_ms"],
        }
    )
    return result


def _group_key(item: Mapping[str, Any], key: str) -> str:
    aliases = {
        "config_id": ("config_id", "configId", "ablation", "condition"),
        "subset": ("subset", "category"),
        "split": ("split",),
    }
    value = _field(item, *aliases.get(key, (key,)), default="unknown")
    return str(value)


def aggregate_observations(
    observations: Iterable[Mapping[str, Any]],
    *,
    include_case_records: bool = True,
) -> dict[str, Any]:
    """Aggregate per-query observations by config, subset and split."""

    items = [_flat_observation(item) for item in observations]
    by_config: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_subset: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_split: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        by_config[_group_key(item, "config_id")].append(item)
        by_subset[_group_key(item, "subset")].append(item)
        by_split[_group_key(item, "split")].append(item)

    config_result: dict[str, Any] = {}
    for config, config_items in sorted(by_config.items()):
        subset_result: dict[str, Any] = {}
        for subset, subset_items in sorted(
            ((name, [x for x in config_items if _group_key(x, "subset") == name])
             for name in { _group_key(x, "subset") for x in config_items }),
            key=lambda pair: pair[0],
        ):
            split_result = {
                split: _summary(
                    [x for x in subset_items if _group_key(x, "split") == split]
                )
                for split in ("tuning", "holdout")
            }
            subset_result[subset] = {
                "aggregate": _summary(subset_items),
                "splits": split_result,
            }
        config_result[config] = {
            "aggregate": _summary(config_items),
            "subsets": subset_result,
        }

    result: dict[str, Any] = {
        "schema_version": 1,
        "case_count": len(items),
        "config_count": len(config_result),
        "by_config": config_result,
        "by_subset": {
            subset: _summary(subset_items)
            for subset, subset_items in sorted(by_subset.items())
        },
        "by_split": {
            split: _summary(split_items) for split, split_items in sorted(by_split.items())
        },
    }
    result["configMetrics"] = {
        config: value["aggregate"] for config, value in config_result.items()
    }
    result["subsetMetrics"] = result["by_subset"]
    if include_case_records:
        result["cases"] = items
    return result


def _paired_values(
    observations: Iterable[Mapping[str, Any]],
    *,
    treatment: str,
    control: str,
    metric: str,
    subset: str | None,
    split: str | None,
) -> tuple[list[str], list[float], list[float]]:
    grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for raw in observations:
        item = _flat_observation(raw)
        if subset is not None and _group_key(item, "subset") != subset:
            continue
        if split is not None and _group_key(item, "split") != split:
            continue
        config = _group_key(item, "config_id")
        if config not in {treatment, control}:
            continue
        case_id = str(_field(item, "case_id", "caseId", "id", default=""))
        if not case_id:
            continue
        value = _field(item, metric, _camel_metric(metric), default=None)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        paired_group = str(
            _field(item, "paired_group", "pairedGroup", "seed", "run_id", "runId", default="")
        )
        grouped[(case_id, paired_group)][config].append(float(value))
    case_ids: list[str] = []
    controls: list[float] = []
    treatments: list[float] = []
    for (case_id, _paired_group), values in sorted(grouped.items()):
        control_values = values.get(control, [])
        treatment_values = values.get(treatment, [])
        for control_value, treatment_value in zip(control_values, treatment_values):
            case_ids.append(case_id)
            controls.append(control_value)
            treatments.append(treatment_value)
    return case_ids, controls, treatments


def paired_effect(
    observations: Iterable[Mapping[str, Any]],
    *,
    treatment: str = "R0_full_hybrid",
    control: str = "R1_keyword_only",
    metric: str = "recall_at_5",
    subset: str | None = None,
    split: str | None = None,
    bootstrap_samples: int = 10_000,
    seed: int = 20260901,
) -> dict[str, Any]:
    """Estimate treatment-control effect with a paired bootstrap CI."""

    case_ids, controls, treatments = _paired_values(
        observations,
        treatment=treatment,
        control=control,
        metric=metric,
        subset=subset,
        split=split,
    )
    differences = [treatment_value - control_value for control_value, treatment_value in zip(controls, treatments)]
    result: dict[str, Any] = {
        "treatment": treatment,
        "control": control,
        "metric": metric,
        "subset": subset,
        "split": split,
        "n_pairs": len(differences),
        "paired_case_ids": case_ids,
        "bootstrap_samples": max(0, int(bootstrap_samples)),
        "seed": int(seed),
        "status": "ok" if differences else "insufficient_pairs",
        "control_mean": _mean(controls),
        "treatment_mean": _mean(treatments),
        "mean_difference": _mean(differences),
        "median_difference": statistics.median(differences) if differences else None,
        "cohen_dz": None,
        "ci95_low": None,
        "ci95_high": None,
    }
    if not differences:
        result["effect_size"] = None
        result["confidence_interval_95"] = [None, None]
        return result
    deviation = statistics.stdev(differences) if len(differences) > 1 else 0.0
    result["cohen_dz"] = (
        statistics.fmean(differences) / deviation if deviation else (math.inf if statistics.fmean(differences) else 0.0)
    )
    draws = max(0, int(bootstrap_samples))
    if draws:
        rng = random.Random(seed)
        bootstrap_means = [
            statistics.fmean(differences[rng.randrange(len(differences))] for _ in differences)
            for _ in range(draws)
        ]
        low = _percentile(bootstrap_means, 2.5)
        high = _percentile(bootstrap_means, 97.5)
        result["ci95_low"] = low
        result["ci95_high"] = high
        result["confidence_interval_95"] = [low, high]
    else:
        result["confidence_interval_95"] = [None, None]
    result["effect_size"] = result["mean_difference"]
    return result


def compute_paired_effects(
    observations: Iterable[Mapping[str, Any]],
    *,
    bootstrap_samples: int = 10_000,
    seed: int = 20260901,
) -> dict[str, Any]:
    """Compute the two pre-registered Memory ablation comparisons."""

    items = list(observations)
    return {
        "semantic_paraphrase": paired_effect(
            items,
            treatment="R0_full_hybrid",
            control="R1_keyword_only",
            metric="recall_at_5",
            subset="semantic_paraphrase",
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        ),
        "graph_only": paired_effect(
            items,
            treatment="R0_full_hybrid",
            control="R3_no_graph",
            metric="recall_at_5",
            subset="graph_only",
            bootstrap_samples=bootstrap_samples,
            seed=seed + 1,
        ),
    }


__all__ = [
    "aggregate_observations",
    "compute_paired_effects",
    "paired_effect",
    "retrieval_metrics",
    "score_memory_retrieval",
    "score_retrieval",
]
