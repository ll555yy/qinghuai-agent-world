from __future__ import annotations

from benchmark.memory.scorer import (
    aggregate_observations,
    compute_paired_effects,
    paired_effect,
    score_retrieval,
)


def test_score_retrieval_reports_ranking_safety_and_duplicates() -> None:
    metrics = score_retrieval(
        ["memory_a"],
        ["memory_a", "memory_a", "memory_noise"],
        5,
        owner_memory_ids=["memory_a"],
    )

    assert metrics["recall_at_1"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["precision_at_returned"] == 2 / 3
    assert metrics["precision_at_5"] == 1 / 5
    assert metrics["duplicate_result_count"] == 1
    assert metrics["owner_boundary_violations"] == 1
    assert metrics["ndcg_at_5"] <= 1.0


def test_empty_query_and_hard_negative_are_safe() -> None:
    empty = score_retrieval([], [], 5, query_is_empty=True)
    false_positive = score_retrieval([], ["memory_noise"], 5)

    assert empty["empty_query_correct"] is True
    assert empty["false_positive_rate"] == 0.0
    assert false_positive["recall_at_5"] == 0.0
    assert false_positive["false_positive_rate"] == 1.0


def test_aggregate_preserves_config_subset_split_and_latency_percentiles() -> None:
    observations = [
        {
            "case_id": "q1",
            "config_id": "R0_full_hybrid",
            "subset": "semantic_paraphrase",
            "split": "holdout",
            "recall_at_5": 1.0,
            "latency_ms": 10.0,
        },
        {
            "case_id": "q2",
            "config_id": "R0_full_hybrid",
            "subset": "semantic_paraphrase",
            "split": "holdout",
            "recall_at_5": 0.0,
            "latency_ms": 20.0,
        },
    ]
    report = aggregate_observations(observations)

    summary = report["by_config"]["R0_full_hybrid"]["subsets"]["semantic_paraphrase"]["splits"]["holdout"]
    assert summary["cases"] == 2
    assert summary["recall_at_5"] == 0.5
    assert summary["latency_p50_ms"] == 15.0
    assert summary["latency_p95_ms"] == 19.5


def test_paired_effect_is_reproducible_and_uses_case_pairs() -> None:
    observations = []
    for case_id, control, treatment in (("q1", 0.0, 1.0), ("q2", 0.5, 1.0)):
        observations.extend(
            [
                {
                    "case_id": case_id,
                    "config_id": "R1_keyword_only",
                    "subset": "semantic_paraphrase",
                    "split": "holdout",
                    "recall_at_5": control,
                },
                {
                    "case_id": case_id,
                    "config_id": "R0_full_hybrid",
                    "subset": "semantic_paraphrase",
                    "split": "holdout",
                    "recall_at_5": treatment,
                },
            ]
        )
    first = paired_effect(observations, bootstrap_samples=100)
    second = paired_effect(observations, bootstrap_samples=100)

    assert first == second
    assert first["n_pairs"] == 2
    assert first["mean_difference"] == 0.75
    assert first["ci95_low"] <= first["mean_difference"] <= first["ci95_high"]
    assert compute_paired_effects(observations, bootstrap_samples=10)["graph_only"]["n_pairs"] == 0
