from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import delete

from benchmark.memory.dataset import MemoryBenchmarkDataset, MemoryQueryCase
from benchmark.memory.runner import RetrievalConfig, RetrievalResult
from core.backend.app.ai.protocols import MemoryQuery
from core.backend.app.db.bootstrap import sync_scenario
from core.backend.app.db.models import ChapterRun
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.embedding_indexer import MemoryEmbeddingIndexer
from core.backend.app.persistence.memory_retriever import (
    DatabaseMemoryRetriever,
    RetrievalPolicy,
)
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from core.backend.app.scenario.loader import ScenarioLoader


def validate_dataset_references(dataset: MemoryBenchmarkDataset, registry: Any) -> None:
    """Reject synthetic labels that cannot satisfy production foreign keys."""

    actor_ids = set(registry.actors)
    goal_ids = set(registry.goals)
    topic_ids = set(registry.topics)
    referenced_actors = {
        value
        for document in dataset.corpus
        for value in (document.owner_npc_id, *document.actor_ids)
    } | {
        value
        for case in dataset.queries
        for value in (case.owner_npc_id, *case.query.actor_ids)
    }
    referenced_goals = {
        value for document in dataset.corpus for value in document.goal_ids
    } | {value for case in dataset.queries for value in case.query.goal_ids}
    referenced_topics = {
        value for document in dataset.corpus for value in document.topic_ids
    } | {value for case in dataset.queries for value in case.query.topic_hints}
    missing = {
        "actors": sorted(referenced_actors - actor_ids),
        "goals": sorted(referenced_goals - goal_ids),
        "topics": sorted(referenced_topics - topic_ids),
    }
    if any(missing.values()):
        raise ValueError(f"memory dataset contains unknown scenario references: {missing}")


class CachedEmbeddingPort:
    """Cache real vectors so all ablations reuse identical query embeddings."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model_name = inner.model_name
        self.dimensions = inner.dimensions
        self._cache: dict[str, list[float]] = {}

    async def embed(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = list(await self.inner.embed(text))
        return list(self._cache[text])

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in self._cache))
        if missing:
            embed_many = getattr(self.inner, "embed_many", None)
            vectors = await embed_many(missing) if callable(embed_many) else [await self.inner.embed(text) for text in missing]
            for text, vector in zip(missing, vectors, strict=True):
                self._cache[text] = list(vector)
        return [list(self._cache[text]) for text in texts]

    def metrics_snapshot(self) -> dict[str, Any]:
        return getattr(self.inner, "metrics_snapshot", dict)()

    def telemetry_snapshot(self) -> dict[str, Any]:
        return getattr(self.inner, "telemetry_snapshot", self.metrics_snapshot)()


class PostgresMemoryHarness:
    """Disposable dedicated-Postgres fixture for the frozen memory dataset."""

    def __init__(
        self,
        database_url: str,
        dataset: MemoryBenchmarkDataset,
        embedding_port: Any,
        *,
        scenario_root: str = "core/scenario",
        seed: int = 20260901,
    ) -> None:
        self.database_url = database_url
        self.dataset = dataset
        self.embedding_port = CachedEmbeddingPort(embedding_port)
        self.registry = ScenarioLoader(scenario_root).load()
        validate_dataset_references(dataset, self.registry)
        self.repository = SQLAlchemyRunRepository(database_url, chapter_id=self.registry.chapter_id)
        self.seed = seed
        self.run_id: str | None = None

    async def __aenter__(self) -> Self:
        if not await self.repository.healthcheck():
            raise RuntimeError("dedicated PostgreSQL database is unavailable or not migrated")
        await sync_scenario(self.repository.session_factory, self.registry)
        service = RunService(self.registry, repository=self.repository, text_model=None)
        created = await service.create_run(seed=self.seed)
        self.run_id = str(created["runId"])
        run = await service.get_run_entity(self.run_id)
        created_at = str(run.clock.as_dict()["label"])
        run.memories.clear()
        run.memory_links.clear()
        for document in self.dataset.corpus:
            run.memories[document.memory_id] = {
                "memoryId": document.memory_id,
                "ownerNpcId": document.owner_npc_id,
                "type": "belief",
                "content": document.content,
                "actorIds": list(document.actor_ids),
                "goalIds": list(document.goal_ids),
                "topicIds": list(document.topic_ids),
                "importance": 5,
                "confidence": "high",
                # Production schema intentionally restricts memory provenance.
                # The synthetic corpus is seeded before the run and therefore
                # uses the same provenance class as frozen scenario memories.
                "source": "scenario_seed",
                "evidenceMessageIds": [],
                "createdAt": created_at,
            }
        edges: set[tuple[str, str]] = set()
        for document in self.dataset.corpus:
            for neighbor in document.graph_neighbors:
                edge = tuple(sorted((document.memory_id, neighbor)))
                if edge[0] != edge[1]:
                    edges.add(edge)
        run.memory_links.extend(
            {"memoryId": left, "targetId": right, "kind": "SUPPORTS"}
            for left, right in sorted(edges)
        )
        await self.repository.save(run)
        index_result = await MemoryEmbeddingIndexer(
            self.repository.session_factory, self.embedding_port
        ).index_missing(run_id=self.run_id, force=True)
        if index_result.failed:
            raise RuntimeError(f"failed to embed {index_result.failed} benchmark memories")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self.run_id is not None:
                async with self.repository.session_factory() as session, session.begin():
                    await session.execute(delete(ChapterRun).where(ChapterRun.run_id == self.run_id))
        finally:
            await self.repository.close()

    async def search(self, case: MemoryQueryCase, config: RetrievalConfig) -> RetrievalResult:
        if self.run_id is None:
            raise RuntimeError("PostgresMemoryHarness must be entered before search")
        if case.query_is_empty:
            return RetrievalResult()
        if not config.use_owner_guard:
            # Deliberately unsafe benchmark-local negative control. Production
            # RetrievalPolicy never exposes an owner-guard switch.
            return RetrievalResult(
                memory_ids=tuple(case.distractor_memory_ids or case.expected_memory_ids),
                metadata={"securityNegativeControl": True},
            )
        structured = bool(config.use_structured_filter)
        policy = RetrievalPolicy(
            use_keyword=bool(config.use_keyword),
            use_vector=bool(config.use_vector),
            use_actor_filter=structured,
            use_goal_filter=structured,
            use_topic_filter=structured,
            graph_hops=2 if config.use_graph else 0,
            max_seed_candidates=1 if case.graph_seed_memory_ids else None,
        )
        retriever = DatabaseMemoryRetriever(
            self.repository.session_factory,
            embedding_port=self.embedding_port if config.use_vector else None,
            policy=policy,
        )
        query_text = case.query.query_text
        if not query_text.strip() and case.graph_seed_memory_ids:
            # Production MemoryQuery intentionally has no privileged
            # memory-ID lookup. Use the frozen seed memory's exact content as
            # the common retrieval entry point, then let graph_hops be the
            # only difference between R0 and R3.
            corpus_by_id = self.dataset.corpus_by_id
            query_text = " ".join(
                corpus_by_id[memory_id].content
                for memory_id in case.graph_seed_memory_ids
            )
        result = await retriever.search(
            run_id=self.run_id,
            owner_npc_id=case.owner_npc_id,
            query=MemoryQuery(
                queryText=query_text,
                actorIds=list(case.query.actor_ids),
                goalIds=list(case.query.goal_ids),
                topicHints=list(case.query.topic_hints),
                limit=case.query.limit,
            ),
        )
        return RetrievalResult(
            memory_ids=tuple(result.memory_ids),
            vector_hits=result.vector_hits,
            graph_hits=result.graph_hits,
        )


__all__ = ["CachedEmbeddingPort", "PostgresMemoryHarness"]
