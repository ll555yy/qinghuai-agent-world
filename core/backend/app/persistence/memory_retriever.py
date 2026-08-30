"""Owner-safe PostgreSQL retrieval for the NPC Agent memory tool.

The database is the candidate source.  Every query and every graph hop fixes
``run_id`` and ``owner_npc_id`` before scoring, so model-produced hints cannot
cross an NPC's private-memory boundary.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import cast, or_, select

from ..agents.models import MemoryToolResult
from ..ai.embedding import MEMORY_EMBEDDING_DIMENSIONS, EmbeddingPort
from ..ai.protocols import MemoryQuery
from ..db.models import (
    Memory,
    MemoryActorLink,
    MemoryEdge,
    MemoryGoalLink,
    MemoryTopicLink,
    Topic,
)


class MemoryRetriever(Protocol):
    async def search(
        self,
        *,
        run_id: str,
        owner_npc_id: str,
        query: MemoryQuery,
    ) -> MemoryToolResult:
        """Return only IDs of memories owned by ``owner_npc_id``."""


@dataclass(slots=True)
class _Candidate:
    memory_id: str
    content: str
    importance: int
    confidence: str
    created_day: int
    created_minute: int
    vector_score: float = 0.0
    graph_distance: int | None = None


class DatabaseMemoryRetriever:
    """Small-world hybrid retrieval using PostgreSQL, pgvector and graph edges."""

    def __init__(
        self,
        session_factory: Any,
        *,
        embedding_port: EmbeddingPort | None = None,
        vector_dimensions: int = MEMORY_EMBEDDING_DIMENSIONS,
        candidate_limit: int = 48,
    ) -> None:
        if vector_dimensions != MEMORY_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"memory retriever requires {MEMORY_EMBEDDING_DIMENSIONS}-dimension vectors"
            )
        self._session_factory = session_factory
        self._embedding_port = embedding_port
        self._vector_dimensions = vector_dimensions
        self._candidate_limit = candidate_limit

    async def search(
        self,
        *,
        run_id: str,
        owner_npc_id: str,
        query: MemoryQuery,
    ) -> MemoryToolResult:
        # An empty model-produced query is not permission to fetch recent
        # private memories.  Returning no rows keeps retrieval intentional and
        # makes the empty-query safety contract deterministic.
        if not (
            query.query_text.strip()
            or query.actor_ids
            or query.goal_ids
            or query.topic_hints
        ):
            return MemoryToolResult()

        vector: Sequence[float] | None = None
        if self._embedding_port is not None and query.query_text.strip():
            try:
                produced = tuple(float(value) for value in await self._embedding_port.embed(query.query_text))
                if len(produced) == self._vector_dimensions:
                    vector = produced
            except Exception:
                # Keyword + graph retrieval is a real supported mode.  An
                # embedding outage must not manufacture a placeholder vector.
                vector = None

        async with self._session_factory() as session:
            owned_rows = await self._owned_candidates(
                session,
                run_id=run_id,
                owner_npc_id=owner_npc_id,
                vector=vector,
            )
            if not owned_rows:
                return MemoryToolResult()

            owned_ids = {item.memory_id for item in owned_rows}
            actor_links = await self._link_map(
                session,
                MemoryActorLink,
                MemoryActorLink.actor_id,
                run_id,
                owned_ids,
            )
            goal_links = await self._link_map(
                session,
                MemoryGoalLink,
                MemoryGoalLink.goal_id,
                run_id,
                owned_ids,
            )
            topic_links = await self._link_map(
                session,
                MemoryTopicLink,
                MemoryTopicLink.topic_id,
                run_id,
                owned_ids,
            )
            topic_ids = await self._resolve_topics(session, query.topic_hints)

            seeds = self._score_seeds(
                owned_rows,
                query=query,
                actor_links=actor_links,
                goal_links=goal_links,
                topic_links=topic_links,
                resolved_topic_ids=topic_ids,
            )
            # Irrelevant rows do not become graph seeds merely because they
            # are recent. Empty queries have already returned above.
            has_hints = bool(
                query.query_text.strip()
                or query.actor_ids
                or query.goal_ids
                or query.topic_hints
            )
            by_id = {item.memory_id: item for item in owned_rows}
            seed_ids = {
                memory_id
                for memory_id, score in seeds.items()
                if score > 0 or by_id[memory_id].vector_score > 0 or not has_hints
            }
            distances = await self._graph_distances(
                session,
                run_id=run_id,
                owner_npc_id=owner_npc_id,
                seed_ids=seed_ids,
                max_hops=2,
            )
            for memory_id, distance in distances.items():
                if memory_id in by_id:
                    by_id[memory_id].graph_distance = distance

            confidence_weight = {"low": 0.0, "medium": 0.4, "high": 0.8}
            ranked: list[tuple[float, str]] = []
            for item in owned_rows:
                seed_score = seeds.get(item.memory_id, 0.0)
                if item.memory_id not in seed_ids and item.memory_id not in distances:
                    continue
                graph_score = (
                    2.5 / item.graph_distance
                    if item.graph_distance is not None and item.graph_distance > 0
                    else 0.0
                )
                absolute_minute = item.created_day * 1440 + item.created_minute
                recency = math.log1p(max(1, absolute_minute)) / 20.0
                score = (
                    seed_score
                    + item.vector_score * 4.0
                    + graph_score
                    + item.importance * 0.7
                    + confidence_weight.get(item.confidence, 0.0)
                    + recency
                )
                ranked.append((score, item.memory_id))
            ranked.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
            selected_ids = tuple(memory_id for _, memory_id in ranked[: query.limit])
            return MemoryToolResult(
                selected_ids,
                vector_hits=sum(
                    1 for memory_id in selected_ids if by_id[memory_id].vector_score > 0
                ),
                graph_hits=sum(
                    1
                    for memory_id in selected_ids
                    if by_id[memory_id].graph_distance is not None
                ),
            )

    async def _owned_candidates(
        self,
        session: Any,
        *,
        run_id: str,
        owner_npc_id: str,
        vector: Sequence[float] | None,
    ) -> list[_Candidate]:
        vector_distance = None
        columns: list[Any] = [Memory]
        if vector is not None:
            # Ark Agent Plan returns 2048 values. pgvector HNSW indexes
            # float32 vector only through 2000 dimensions, so preserve the
            # full stored vector and search the matching halfvec expression.
            vector_distance = cast(
                Memory.embedding, HALFVEC(MEMORY_EMBEDDING_DIMENSIONS)
            ).cosine_distance(list(vector)).label("vector_distance")
            columns.append(vector_distance)
        statement = select(*columns).where(
            Memory.run_id == run_id,
            Memory.owner_npc_id == owner_npc_id,
        )
        if vector_distance is not None:
            statement = statement.order_by(
                vector_distance.asc().nullslast(),
                Memory.created_world_day.desc(),
                Memory.created_world_minute.desc(),
            )
        else:
            statement = statement.order_by(
                Memory.created_world_day.desc(),
                Memory.created_world_minute.desc(),
            )
        statement = statement.limit(self._candidate_limit)
        rows = (await session.execute(statement)).all()
        result: list[_Candidate] = []
        for row in rows:
            memory = row[0]
            distance = row[1] if vector_distance is not None else None
            result.append(
                _Candidate(
                    memory_id=memory.memory_id,
                    content=memory.content,
                    importance=memory.importance,
                    confidence=memory.confidence,
                    created_day=memory.created_world_day,
                    created_minute=memory.created_world_minute,
                    vector_score=max(0.0, 1.0 - float(distance)) if distance is not None else 0.0,
                )
            )
        return result

    @staticmethod
    async def _link_map(
        session: Any,
        model: Any,
        value_column: Any,
        run_id: str,
        memory_ids: set[str],
    ) -> dict[str, set[str]]:
        if not memory_ids:
            return {}
        rows = (
            await session.execute(
                select(model.memory_id, value_column).where(
                    model.run_id == run_id,
                    model.memory_id.in_(memory_ids),
                )
            )
        ).all()
        result: dict[str, set[str]] = defaultdict(set)
        for memory_id, value in rows:
            result[str(memory_id)].add(str(value))
        return dict(result)

    @staticmethod
    async def _resolve_topics(session: Any, hints: list[str]) -> set[str]:
        if not hints:
            return set()
        lowered = {hint.casefold() for hint in hints}
        rows = (
            await session.execute(
                select(Topic).where(
                    or_(Topic.topic_id.in_(hints), Topic.name.in_(hints))
                )
            )
        ).scalars().all()
        result = {row.topic_id for row in rows}
        # Aliases are JSON arrays, so resolving them in Python keeps this
        # path deterministic across PostgreSQL minor versions.
        all_topics = (await session.execute(select(Topic))).scalars().all()
        for topic in all_topics:
            if any(str(alias).casefold() in lowered for alias in topic.aliases or ()):
                result.add(topic.topic_id)
        return result

    @staticmethod
    def _score_seeds(
        candidates: list[_Candidate],
        *,
        query: MemoryQuery,
        actor_links: dict[str, set[str]],
        goal_links: dict[str, set[str]],
        topic_links: dict[str, set[str]],
        resolved_topic_ids: set[str],
    ) -> dict[str, float]:
        tokens = {
            token.casefold()
            for token in query.query_text.replace("，", " ").replace("。", " ").split()
            if token.strip()
        }
        actors = set(query.actor_ids)
        goals = set(query.goal_ids)
        result: dict[str, float] = {}
        for item in candidates:
            content = item.content.casefold()
            text_hits = sum(1 for token in tokens if token in content)
            result[item.memory_id] = float(
                text_hits * 2
                + len(actors & actor_links.get(item.memory_id, set())) * 5
                + len(goals & goal_links.get(item.memory_id, set())) * 4
                + len(resolved_topic_ids & topic_links.get(item.memory_id, set())) * 4
            )
        return result

    @staticmethod
    async def _graph_distances(
        session: Any,
        *,
        run_id: str,
        owner_npc_id: str,
        seed_ids: set[str],
        max_hops: int,
    ) -> dict[str, int]:
        if not seed_ids:
            return {}
        visited = set(seed_ids)
        frontier = set(seed_ids)
        distances: dict[str, int] = {}
        for distance in range(1, max_hops + 1):
            edge_rows = (
                await session.execute(
                    select(MemoryEdge.from_memory_id, MemoryEdge.to_memory_id).where(
                        MemoryEdge.run_id == run_id,
                        or_(
                            MemoryEdge.from_memory_id.in_(frontier),
                            MemoryEdge.to_memory_id.in_(frontier),
                        ),
                    )
                )
            ).all()
            adjacent = {
                str(right if left in frontier else left)
                for left, right in edge_rows
            } - visited
            if not adjacent:
                break
            # This owner predicate is repeated at every hop.  A cross-owner
            # edge can exist due to bad imported data but can never traverse.
            owned = set(
                (
                    await session.execute(
                        select(Memory.memory_id).where(
                            Memory.run_id == run_id,
                            Memory.owner_npc_id == owner_npc_id,
                            Memory.memory_id.in_(adjacent),
                        )
                    )
                ).scalars().all()
            )
            for memory_id in owned:
                distances[str(memory_id)] = distance
            visited.update(owned)
            frontier = {str(memory_id) for memory_id in owned}
            if not frontier:
                break
        return distances


__all__ = ["DatabaseMemoryRetriever", "MemoryRetriever"]
