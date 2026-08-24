from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from core.backend.app.ai.protocols import MemoryQuery
from core.backend.app.db.bootstrap import sync_scenario
from core.backend.app.db.models import ChapterRun, Memory
from core.backend.app.evaluation.models import CandidateObservation, EvaluationCase
from core.backend.app.evaluation.rule_scorer import RuleScorer
from core.backend.app.evaluation.runner import EvaluationRunner
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.memory_retriever import DatabaseMemoryRetriever
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from core.backend.app.scenario.loader import ScenarioLoader
from sqlalchemy import delete, select, update


def _postgres_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="postgres_required",
        case_version=1,
        category="memory",
        protocol="memory_retrieval",
        npc_id="npc_001",
        input_context={},
        expected_constraints=[],
        forbidden_signals=[],
        allowed_outcomes=[],
        expected_memory_ids=[],
        allowed_evidence_message_ids=[],
        requires_postgres=True,
        requires_live_candidate=False,
        requires_live_embedding=False,
        judge_rubric=[],
        tags=["postgres"],
    )


def _dedicated_test_database_url() -> str:
    """Read only the explicitly supplied dedicated database URL.

    The test intentionally does not load ``.env`` or fall back to the
    application's ``DATABASE_URL``. A matching process-level development or
    production URL is rejected before any connection is opened.
    """

    database_url = os.environ.get("QINGHUAI_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set QINGHUAI_TEST_DATABASE_URL to run PostgreSQL integration tests")
    for unsafe_name in ("DATABASE_URL", "QINGHUAI_DATABASE_URL"):
        unsafe_url = os.environ.get(unsafe_name, "").strip()
        if unsafe_url and unsafe_url == database_url:
            pytest.fail(
                "QINGHUAI_TEST_DATABASE_URL must be a dedicated database, not "
                f"the process {unsafe_name}"
            )
    return database_url


class _DeterministicEmbedding:
    """A deterministic 2048-d vector source for the real pgvector path."""

    dimensions = MEMORY_EMBEDDING_DIMENSIONS
    model_name = "semantic-postgres-fixed-2048"

    def __init__(self) -> None:
        self.calls = 0
        self.vector = [1.0, *([0.0] * (self.dimensions - 1))]

    async def embed(self, _text: str) -> list[float]:
        self.calls += 1
        return list(self.vector)


def _memory(
    memory_id: str,
    owner_npc_id: str,
    created_at: str,
    content: str,
    *,
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


def _case(
    case_id: str,
    expected_memory_ids: list[str],
    owner_memory_ids: list[str],
    *,
    retrieval_k: int,
    allowed_actor_ids: list[str] | None = None,
    allowed_goal_ids: list[str] | None = None,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        case_version=1,
        category="memory",
        protocol="memory_retrieval",
        npc_id="npc_001",
        input_context={
            "owner_memory_ids": owner_memory_ids,
            "allowed_actor_ids": allowed_actor_ids or [],
            "allowed_goal_ids": allowed_goal_ids or [],
            "retrieval_k": retrieval_k,
        },
        expected_constraints=["only owner memories"],
        forbidden_signals=[],
        allowed_outcomes=[],
        expected_memory_ids=expected_memory_ids,
        allowed_evidence_message_ids=[],
        requires_postgres=True,
        requires_live_candidate=False,
        requires_live_embedding=False,
        judge_rubric=[],
        tags=["postgres", "owner-boundary", "retrieval"],
    )


def _score_retrieval(
    case: EvaluationCase,
    recalled: Any,
    owner_memory_ids: list[str],
) -> None:
    score = RuleScorer().score(
        case,
        CandidateObservation(
            case_id=case.case_id,
            protocol=case.protocol,
            retrieved_memory_ids=list(recalled.memory_ids),
            owner_memory_ids=owner_memory_ids,
            vector_hits=recalled.vector_hits,
            graph_hits=recalled.graph_hits,
            retrieval_k=case.input_context["retrieval_k"],
        ),
    )

    assert score.hard_failure is False
    assert score.schema_valid is True
    assert score.candidate_violation is False
    assert score.system_blocked is not True
    assert score.end_to_end_safety_failure is not True
    assert score.owner_leak_count == 0
    assert score.unauthorized_memory_count == 0
    assert score.owner_boundary_valid is True
    assert score.precision_at_k == 1.0
    assert score.recall_at_k == 1.0


async def _delete_test_run(repository: SQLAlchemyRunRepository, run_id: str) -> None:
    """Delete only the run created by this test from the dedicated database."""

    async with repository.session_factory() as session, session.begin():
        await session.execute(delete(ChapterRun).where(ChapterRun.run_id == run_id))


def test_postgres_required_case_is_skipped_without_database_authority() -> None:
    report = asyncio.run(
        EvaluationRunner(
            [_postgres_case()],
            mode="offline",
            postgres_available=False,
        ).run()
    )

    assert report["cases"][0]["status"] == "skipped"
    assert report["cases"][0]["reviewReasons"] == ["postgres_required"]
    assert report["execution"]["candidateCalls"] == 0
    assert report["execution"]["embeddingCalls"] == 0


@pytest.mark.anyio
async def test_postgres_retrieval_flows_into_owner_safe_semantic_rule_score() -> None:
    database_url = _dedicated_test_database_url()
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    registry = ScenarioLoader(scenario_dir).load()
    repository = SQLAlchemyRunRepository(
        database_url,
        chapter_id=registry.chapter_id,
    )
    run_id: str | None = None
    try:
        await sync_scenario(repository.session_factory, registry)
        service = RunService(registry, repository=repository, text_model=None)
        created = await service.create_run(seed=20260823)
        run_id = str(created["runId"])
        run = await service.get_run_entity(run_id)
        created_at = run.clock.as_dict()["label"]
        topic_id = "topic_qinghuai_literary_society"
        goal_id = "goal_001_public"

        owner_vector = "pg_owner_vector_anchor"
        owner_keyword = "pg_owner_keyword_anchor"
        owner_actor = "pg_owner_actor_anchor"
        owner_goal = "pg_owner_goal_anchor"
        owner_topic = "pg_owner_topic_anchor"
        owner_graph_one = "pg_owner_graph_one_hop"
        owner_graph_two = "pg_owner_graph_two_hop"
        other_owner_vector = "pg_other_owner_similar_vector"
        other_owner_graph = "pg_other_owner_graph_neighbor"

        run.memories.update(
            {
                owner_vector: _memory(
                    owner_vector,
                    "npc_001",
                    created_at,
                    "向量锚点：青槐文社与旧书保护的公开方案。",
                ),
                owner_keyword: _memory(
                    owner_keyword,
                    "npc_001",
                    created_at,
                    "关键词锚点：只用于检验 PostgreSQL 关键词检索。",
                ),
                owner_actor: _memory(
                    owner_actor,
                    "npc_001",
                    created_at,
                    "Actor 锚点：周慎之参与了公开协商。",
                    actor_ids=["npc_005"],
                ),
                owner_goal: _memory(
                    owner_goal,
                    "npc_001",
                    created_at,
                    "Goal 锚点：林慧兰推进青槐文社公开方案。",
                    goal_ids=[goal_id],
                ),
                owner_topic: _memory(
                    owner_topic,
                    "npc_001",
                    created_at,
                    "Topic 锚点：文社公益项目需要保留旧书保护。",
                    topic_ids=[topic_id],
                ),
                owner_graph_one: _memory(
                    owner_graph_one,
                    "npc_001",
                    created_at,
                    "图一跳锚点：公开方案需要透明账目。",
                ),
                owner_graph_two: _memory(
                    owner_graph_two,
                    "npc_001",
                    created_at,
                    "图二跳锚点：公开方案需要逐项授权。",
                ),
                other_owner_vector: _memory(
                    other_owner_vector,
                    "npc_002",
                    created_at,
                    "跨 owner 的相似向量诱饵，不得被 npc_001 召回。",
                ),
                other_owner_graph: _memory(
                    other_owner_graph,
                    "npc_002",
                    created_at,
                    "跨 owner 的图邻居诱饵，不得被图扩展带出。",
                ),
            }
        )
        run.memory_links.extend(
            [
                {
                    "memoryId": owner_vector,
                    "targetId": owner_graph_one,
                    "kind": "CAUSES",
                },
                {
                    "memoryId": owner_graph_one,
                    "targetId": owner_graph_two,
                    "kind": "SUPPORTS",
                },
                {
                    "memoryId": owner_graph_one,
                    "targetId": other_owner_graph,
                    "kind": "CONTRADICTS",
                },
            ]
        )
        await repository.save(run)

        fixed_vector = [1.0, *([0.0] * (MEMORY_EMBEDDING_DIMENSIONS - 1))]
        async with repository.session_factory() as session, session.begin():
            await session.execute(
                update(Memory)
                .where(
                    Memory.run_id == run_id,
                    Memory.memory_id.in_([owner_vector, other_owner_vector]),
                )
                .values(
                    embedding=fixed_vector,
                    embedding_model="semantic-postgres-fixed-2048",
                    embedding_dimensions=MEMORY_EMBEDDING_DIMENSIONS,
                )
            )
            owner_memory_ids = list(
                (
                    await session.execute(
                        select(Memory.memory_id).where(
                            Memory.run_id == run_id,
                            Memory.owner_npc_id == "npc_001",
                        )
                    )
                ).scalars()
            )

        owner_only = set(owner_memory_ids)
        assert owner_vector in owner_only
        assert other_owner_vector not in owner_only

        embedding = _DeterministicEmbedding()
        vector_retriever = DatabaseMemoryRetriever(
            repository.session_factory,
            embedding_port=embedding,
        )
        keyword_retriever = DatabaseMemoryRetriever(repository.session_factory)

        empty_recalled = await keyword_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery.model_construct(
                query_text="",
                actor_ids=[],
                goal_ids=[],
                topic_hints=[],
                limit=3,
            ),
        )
        assert list(empty_recalled.memory_ids) == []
        assert empty_recalled.vector_hits == 0
        assert empty_recalled.graph_hits == 0

        vector_recalled = await vector_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(queryText="向量查询", limit=1),
        )
        assert embedding.calls == 1
        assert list(vector_recalled.memory_ids) == [owner_vector]
        assert other_owner_vector not in vector_recalled.memory_ids
        assert vector_recalled.vector_hits == 1
        _score_retrieval(
            _case("postgres_vector", [owner_vector], owner_memory_ids, retrieval_k=1),
            vector_recalled,
            owner_memory_ids,
        )

        keyword_recalled = await keyword_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(queryText="关键词锚点", limit=1),
        )
        assert list(keyword_recalled.memory_ids) == [owner_keyword]
        _score_retrieval(
            _case("postgres_keyword", [owner_keyword], owner_memory_ids, retrieval_k=1),
            keyword_recalled,
            owner_memory_ids,
        )

        actor_recalled = await keyword_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(actorIds=["npc_005"], limit=1),
        )
        assert list(actor_recalled.memory_ids) == [owner_actor]
        _score_retrieval(
            _case(
                "postgres_actor",
                [owner_actor],
                owner_memory_ids,
                retrieval_k=1,
                allowed_actor_ids=["npc_005"],
            ),
            actor_recalled,
            owner_memory_ids,
        )

        goal_recalled = await keyword_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(goalIds=[goal_id], limit=1),
        )
        assert list(goal_recalled.memory_ids) == [owner_goal]
        _score_retrieval(
            _case(
                "postgres_goal",
                [owner_goal],
                owner_memory_ids,
                retrieval_k=1,
                allowed_goal_ids=[goal_id],
            ),
            goal_recalled,
            owner_memory_ids,
        )

        topic_recalled = await keyword_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(topicHints=["文社公益项目"], limit=1),
        )
        assert list(topic_recalled.memory_ids) == [owner_topic]
        _score_retrieval(
            _case("postgres_topic_alias", [owner_topic], owner_memory_ids, retrieval_k=1),
            topic_recalled,
            owner_memory_ids,
        )

        graph_recalled = await vector_retriever.search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(queryText="图扩展查询", limit=3),
        )
        assert set(graph_recalled.memory_ids) == {
            owner_vector,
            owner_graph_one,
            owner_graph_two,
        }
        assert graph_recalled.graph_hits == 2
        assert other_owner_vector not in graph_recalled.memory_ids
        assert other_owner_graph not in graph_recalled.memory_ids
        assert set(graph_recalled.memory_ids) <= owner_only
        _score_retrieval(
            _case(
                "postgres_graph_two_hop",
                [owner_vector, owner_graph_one, owner_graph_two],
                owner_memory_ids,
                retrieval_k=3,
            ),
            graph_recalled,
            owner_memory_ids,
        )
    finally:
        if run_id is not None:
            await _delete_test_run(repository, run_id)
        await repository.close()
