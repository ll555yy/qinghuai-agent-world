"""Run the dedicated PostgreSQL Memory retrieval tuning/holdout benchmark.

The command creates one disposable Run in an explicitly supplied test
database, executes real ``DatabaseMemoryRetriever.search`` calls, writes a
source-labelled report, and deletes only that Run in ``finally``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS  # noqa: E402
from core.backend.app.db.bootstrap import sync_scenario  # noqa: E402
from core.backend.app.db.models import ChapterRun, Memory  # noqa: E402
from core.backend.app.evaluation.retrieval_benchmark import (  # noqa: E402
    RetrievalBenchmarkCase,
    render_retrieval_benchmark_markdown,
    run_postgres_retrieval_benchmark,
)
from core.backend.app.orchestration.run_service import RunService  # noqa: E402
from core.backend.app.persistence.memory_retriever import (  # noqa: E402
    DatabaseMemoryRetriever,
)
from core.backend.app.persistence.sqlalchemy_repository import (  # noqa: E402
    SQLAlchemyRunRepository,
)
from core.backend.app.scenario.loader import ScenarioLoader  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="QINGHUAI_TEST_DATABASE_URL",
        help="name of the environment variable containing a dedicated database URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "project" / "evaluation-results" / "postgres-retrieval-final",
    )
    return parser.parse_args()


class _BenchmarkEmbedding:
    dimensions = MEMORY_EMBEDDING_DIMENSIONS
    model_name = "postgres-retrieval-benchmark-fixed-2048"

    def __init__(self) -> None:
        self.calls = 0
        self._vectors = {
            "向量调参查询": _unit_vector(0),
            "向量留出查询": _unit_vector(1),
            "图调参查询": _unit_vector(2),
            "图留出查询": _unit_vector(3),
        }

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        if text not in self._vectors:
            raise LookupError("keyword-only benchmark query")
        return list(self._vectors[text])


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * MEMORY_EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


def _memory(
    memory_id: str,
    created_at: str,
    content: str,
    *,
    owner_npc_id: str = "npc_001",
    actor_ids: list[str] | None = None,
    goal_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "memoryId": memory_id,
        "ownerNpcId": owner_npc_id,
        "type": "belief",
        "content": content,
        "actorIds": actor_ids or [],
        "goalIds": goal_ids or [],
        "topicIds": topic_ids or [],
        "importance": 5,
        "confidence": "high",
        "source": "scenario_seed",
        "evidenceMessageIds": [],
        "createdAt": created_at,
    }


def _cases(run_id: str, owner_ids: tuple[str, ...]) -> list[RetrievalBenchmarkCase]:
    definitions: tuple[tuple[str, str, str, dict[str, Any], tuple[str, ...], int], ...] = (
        ("tuning", "vector", "vector_tuning", {"queryText": "向量调参查询"}, ("zz_bench_vector_tuning",), 1),
        ("holdout", "vector", "vector_holdout", {"queryText": "向量留出查询"}, ("zz_bench_vector_holdout",), 1),
        ("tuning", "keyword", "keyword_tuning", {"queryText": "薄荷塔"}, ("zz_bench_keyword_tuning",), 1),
        ("holdout", "keyword", "keyword_holdout", {"queryText": "琥珀桥"}, ("zz_bench_keyword_holdout",), 1),
        ("tuning", "actor", "actor_tuning", {"actorIds": ["npc_004"]}, ("zz_bench_actor_tuning",), 1),
        ("holdout", "actor", "actor_holdout", {"actorIds": ["npc_005"]}, ("zz_bench_actor_holdout",), 1),
        ("tuning", "goal", "goal_tuning", {"goalIds": ["goal_004_deep"]}, ("zz_bench_goal_tuning",), 1),
        ("holdout", "goal", "goal_holdout", {"goalIds": ["goal_005_deep"]}, ("zz_bench_goal_holdout",), 1),
        ("tuning", "topic", "topic_tuning", {"topicHints": ["贵重古籍"]}, ("zz_bench_topic_tuning",), 1),
        ("holdout", "topic", "topic_holdout", {"topicHints": ["书店健康角"]}, ("zz_bench_topic_holdout",), 1),
        ("tuning", "graph", "graph_tuning", {"queryText": "图调参查询"}, ("zz_bench_graph_tuning_root", "zz_bench_graph_tuning_neighbor"), 2),
        ("holdout", "graph", "graph_holdout", {"queryText": "图留出查询"}, ("zz_bench_graph_holdout_root", "zz_bench_graph_holdout_neighbor"), 2),
        ("tuning", "keyword", "empty_tuning", {}, (), 3),
        ("holdout", "keyword", "empty_holdout", {}, (), 3),
    )
    return [
        RetrievalBenchmarkCase(
            case_id=f"postgres_{case_id}",
            run_id=run_id,
            owner_npc_id="npc_001",
            query={**query, "limit": limit},
            expected_memory_ids=expected,
            retrieval_k=limit,
            phase=phase,  # type: ignore[arg-type]
            split=split,  # type: ignore[arg-type]
            owner_memory_ids=owner_ids,
        )
        for split, phase, case_id, query, expected, limit in definitions
    ]


async def _delete_run(repository: SQLAlchemyRunRepository, run_id: str) -> None:
    async with repository.session_factory() as session, session.begin():
        await session.execute(delete(ChapterRun).where(ChapterRun.run_id == run_id))


async def _run(database_url: str, output: Path) -> dict[str, Any]:
    registry = ScenarioLoader(_ROOT / "core" / "scenario").load()
    repository = SQLAlchemyRunRepository(database_url, chapter_id=registry.chapter_id)
    run_id: str | None = None
    try:
        if not await repository.healthcheck():
            raise RuntimeError("dedicated PostgreSQL database is unavailable or not migrated")
        await sync_scenario(repository.session_factory, registry)
        service = RunService(registry, repository=repository, text_model=None)
        created = await service.create_run(seed=20260823)
        run_id = str(created["runId"])
        run = await service.get_run_entity(run_id)
        created_at = run.clock.as_dict()["label"]
        memories = (
            _memory("zz_bench_vector_tuning", created_at, "向量调参锚点"),
            _memory("zz_bench_vector_holdout", created_at, "向量留出锚点"),
            _memory("zz_bench_keyword_tuning", created_at, "独占关键词薄荷塔"),
            _memory("zz_bench_keyword_holdout", created_at, "独占关键词琥珀桥"),
            _memory("zz_bench_actor_tuning", created_at, "Actor 调参锚点", actor_ids=["npc_004"]),
            _memory("zz_bench_actor_holdout", created_at, "Actor 留出锚点", actor_ids=["npc_005"]),
            _memory("zz_bench_goal_tuning", created_at, "Goal 调参锚点", goal_ids=["goal_004_deep"]),
            _memory("zz_bench_goal_holdout", created_at, "Goal 留出锚点", goal_ids=["goal_005_deep"]),
            _memory("zz_bench_topic_tuning", created_at, "Topic 调参锚点", topic_ids=["topic_valuable_ancient_book"]),
            _memory("zz_bench_topic_holdout", created_at, "Topic 留出锚点", topic_ids=["topic_health_corner"]),
            _memory("zz_bench_graph_tuning_root", created_at, "图调参根节点"),
            _memory("zz_bench_graph_tuning_neighbor", created_at, "图调参邻居"),
            _memory("zz_bench_graph_holdout_root", created_at, "图留出根节点"),
            _memory("zz_bench_graph_holdout_neighbor", created_at, "图留出邻居"),
            _memory("zz_bench_other_owner", created_at, "跨 owner 向量诱饵", owner_npc_id="npc_002"),
        )
        run.memories.update({str(item["memoryId"]): item for item in memories})
        run.memory_links.extend(
            [
                {"memoryId": "zz_bench_graph_tuning_root", "targetId": "zz_bench_graph_tuning_neighbor", "kind": "SUPPORTS"},
                {"memoryId": "zz_bench_graph_holdout_root", "targetId": "zz_bench_graph_holdout_neighbor", "kind": "SUPPORTS"},
                {"memoryId": "zz_bench_graph_holdout_root", "targetId": "zz_bench_other_owner", "kind": "CONTRADICTS"},
            ]
        )
        await repository.save(run)

        vectors = {
            "zz_bench_vector_tuning": _unit_vector(0),
            "zz_bench_vector_holdout": _unit_vector(1),
            "zz_bench_graph_tuning_root": _unit_vector(2),
            "zz_bench_graph_holdout_root": _unit_vector(3),
            "zz_bench_other_owner": _unit_vector(3),
        }
        async with repository.session_factory() as session, session.begin():
            for memory_id, vector in vectors.items():
                await session.execute(
                    update(Memory)
                    .where(Memory.run_id == run_id, Memory.memory_id == memory_id)
                    .values(
                        embedding=vector,
                        embedding_model=_BenchmarkEmbedding.model_name,
                        embedding_dimensions=MEMORY_EMBEDDING_DIMENSIONS,
                    )
                )
            owner_ids = tuple(
                str(value)
                for value in (
                    await session.execute(
                        select(Memory.memory_id).where(
                            Memory.run_id == run_id,
                            Memory.owner_npc_id == "npc_001",
                        )
                    )
                ).scalars()
            )

        embedding = _BenchmarkEmbedding()
        retriever = DatabaseMemoryRetriever(
            repository.session_factory,
            embedding_port=embedding,
        )
        report = await run_postgres_retrieval_benchmark(
            retriever,
            _cases(run_id, owner_ids),
            baseline_mrr=0.923077,
        )
        report["datasetVersion"] = "postgres-retrieval-holdout-v1"
        report["embeddingModel"] = embedding.model_name
        report["embeddingCalls"] = embedding.calls
        output.mkdir(parents=True, exist_ok=True)
        json_bytes = (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        (output / "postgres_retrieval_benchmark.json").write_bytes(json_bytes)
        (output / "postgres_retrieval_benchmark.md").write_text(
            render_retrieval_benchmark_markdown(report), encoding="utf-8", newline="\n"
        )
        (output / "postgres_retrieval_benchmark.sha256").write_text(
            sha256(json_bytes).hexdigest() + "\n", encoding="utf-8", newline="\n"
        )
        return report
    finally:
        if run_id is not None:
            await _delete_run(repository, run_id)
        await repository.close()


def main() -> int:
    args = _args()
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise SystemExit(f"set the dedicated database variable {args.database_url_env}")
    for unsafe_name in ("DATABASE_URL", "QINGHUAI_DATABASE_URL"):
        if os.environ.get(unsafe_name, "").strip() == database_url:
            raise SystemExit(f"{args.database_url_env} must not equal {unsafe_name}")
    report = asyncio.run(_run(database_url, args.output))
    print(
        json.dumps(
            {
                "caseCount": report["caseCount"],
                "holdoutAccepted": report["holdoutAccepted"],
                "holdout": report["splitMetrics"]["holdout"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["holdoutAccepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
