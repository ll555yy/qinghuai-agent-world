from __future__ import annotations

import pytest

from benchmark.cli import _live_preflight, _memory_hypothesis, _resume_metrics


def _memory_aggregate(owner_violations: int = 0) -> dict:
    return {
        "by_config": {
            "R0_full_hybrid": {
                "aggregate": {"ownerBoundaryViolations": owner_violations}
            }
        }
    }


def test_memory_hypothesis_uses_preregistered_effect_thresholds() -> None:
    effects = {
        "semantic_paraphrase": {"status": "ok", "mean_difference": 0.10},
        "graph_only": {"status": "ok", "mean_difference": 0.15},
    }
    assert _memory_hypothesis(effects, _memory_aggregate()) is True
    effects["semantic_paraphrase"]["mean_difference"] = 0.099
    assert _memory_hypothesis(effects, _memory_aggregate()) is False
    effects["semantic_paraphrase"]["mean_difference"] = 0.10
    assert _memory_hypothesis(effects, _memory_aggregate(1)) is False


def test_memory_resume_metric_is_recall_at_5() -> None:
    result = _resume_metrics(
        "memory-test",
        "memory",
        {"sampleSize": 70, "recallAt5": 0.8, "hypothesisVerified": False},
        (),
    )
    assert result.primary_metric_name == "recallAt5"
    assert result.primary_metric_value == 0.8


def test_reliability_live_preflight_needs_only_dedicated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QINGHUAI_TEST_DATABASE_URL", "postgresql://benchmark.invalid/db")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_MODEL", raising=False)
    assert _live_preflight("reliability") is None
