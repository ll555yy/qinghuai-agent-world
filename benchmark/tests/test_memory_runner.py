from __future__ import annotations

import asyncio

from benchmark.memory.dataset import load_dataset
from benchmark.memory.runner import (
    DEFAULT_CONFIGS,
    RetrievalConfig,
    RetrievalResult,
    run_memory_benchmark,
)


class _ConfigAwareRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bool]] = []

    async def search(self, *, run_id, owner_npc_id, query, retrieval_config):
        self.calls.append(
            (run_id, owner_npc_id, retrieval_config.config_id, retrieval_config.use_graph)
        )
        return RetrievalResult(("m_lan_site",)) if query.query_text else RetrievalResult(())


def test_runner_injects_each_ablation_and_keeps_observations() -> None:
    dataset = load_dataset()
    retriever = _ConfigAwareRetriever()
    traces: list[dict[str, object]] = []
    report = asyncio.run(
        run_memory_benchmark(
            retriever,
            dataset,
            cases=dataset.queries[:2],
            configs=["R0_full_hybrid", "R3_no_graph"],
            trace_sink=traces.append,
            bootstrap_samples=20,
        )
    )

    assert len(retriever.calls) == 4
    assert len(traces) == 4
    assert report["case_count"] == 2
    assert report["config_ids"] == ["R0_full_hybrid", "R3_no_graph"]
    assert report["aggregate"]["by_config"]["R0_full_hybrid"]["aggregate"]["cases"] == 2
    assert set(report["paired_effects"]) == {"semantic_paraphrase", "graph_only"}


def test_config_switches_are_explicit_and_owner_guard_is_only_disabled_in_r4() -> None:
    assert set(DEFAULT_CONFIGS) == {
        "R0_full_hybrid",
        "R1_keyword_only",
        "R2_vector_only",
        "R3_no_graph",
        "R4_no_owner_guard",
    }
    assert RetrievalConfig("R0_full_hybrid").use_owner_guard is True
    assert RetrievalConfig("R4_no_owner_guard").use_owner_guard is False
    assert RetrievalConfig("R3_no_graph").use_graph is False
