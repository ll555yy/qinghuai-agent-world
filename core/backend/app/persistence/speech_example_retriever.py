"""Intent-driven retrieval over immutable, per-NPC speech examples."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..ai.embedding import MEMORY_EMBEDDING_DIMENSIONS, EmbeddingPort
from ..scenario.models import SpeechExampleDefinition


@dataclass(frozen=True, slots=True)
class SpeechExampleHit:
    example: SpeechExampleDefinition
    similarity: float


@dataclass(frozen=True, slots=True)
class SpeechExampleSearchResult:
    hits: tuple[SpeechExampleHit, ...] = ()
    failure_code: str | None = None


class SpeechExampleRetriever(Protocol):
    async def search(
        self, *, npc_id: str, intent: str, limit: int = 3
    ) -> SpeechExampleSearchResult:
        """Return examples owned by ``npc_id`` without raising provider failures."""


class VectorSpeechExampleRetriever:
    def __init__(
        self,
        examples: Mapping[str, SpeechExampleDefinition],
        embedding_port: EmbeddingPort,
        *,
        dimensions: int = MEMORY_EMBEDDING_DIMENSIONS,
        index_batch_size: int = 10,
    ) -> None:
        if dimensions != MEMORY_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"speech example retriever requires {MEMORY_EMBEDDING_DIMENSIONS}-dimension vectors"
            )
        if index_batch_size <= 0:
            raise ValueError("index_batch_size must be greater than zero")
        self._examples = examples
        self._embedding_port = embedding_port
        self._dimensions = dimensions
        self._index_batch_size = index_batch_size
        self._index: dict[str, tuple[float, ...]] | None = None
        self._index_lock = asyncio.Lock()

    @staticmethod
    def index_text(example: SpeechExampleDefinition) -> str:
        return f"情境：{example.situation}\n回应方式：{example.intended_move}"

    def _vector(self, values: Sequence[float]) -> tuple[float, ...] | None:
        try:
            vector = tuple(float(value) for value in values)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(vector) != self._dimensions or not all(
            math.isfinite(value) for value in vector
        ):
            return None
        return vector

    async def _embed_many(self, texts: list[str]) -> Sequence[Sequence[float]]:
        embed_many = getattr(self._embedding_port, "embed_many", None)
        if callable(embed_many):
            vectors: list[Sequence[float]] = []
            for start in range(0, len(texts), self._index_batch_size):
                batch = texts[start : start + self._index_batch_size]
                produced = await embed_many(batch)
                vectors.extend(produced)
            return vectors
        return await asyncio.gather(
            *(self._embedding_port.embed(text) for text in texts)
        )

    async def _ensure_index(self) -> str | None:
        if self._index is not None:
            return None
        async with self._index_lock:
            if self._index is not None:
                return None
            ordered = sorted(self._examples.values(), key=lambda item: item.example_id)
            try:
                raw_vectors = await self._embed_many(
                    [self.index_text(example) for example in ordered]
                )
            except Exception:
                return "embedding_error"
            try:
                if len(raw_vectors) != len(ordered):
                    return "invalid_vector"
                vectors = [self._vector(vector) for vector in raw_vectors]
            except Exception:
                return "invalid_vector"
            if any(vector is None for vector in vectors):
                return "invalid_vector"
            self._index = {
                example.example_id: vector
                for example, vector in zip(ordered, vectors, strict=True)
                if vector is not None
            }
        return None

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    async def search(
        self, *, npc_id: str, intent: str, limit: int = 3
    ) -> SpeechExampleSearchResult:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        owned = tuple(
            example
            for example in self._examples.values()
            if example.npc_id == npc_id
        )
        if not owned:
            return SpeechExampleSearchResult(failure_code="unknown_npc")
        query = intent.strip()
        if not query:
            return SpeechExampleSearchResult(failure_code="empty_intent")
        failure_code = await self._ensure_index()
        if failure_code is not None:
            return SpeechExampleSearchResult(failure_code=failure_code)
        try:
            query_vector = self._vector(await self._embedding_port.embed(query))
        except Exception:
            return SpeechExampleSearchResult(failure_code="embedding_error")
        if query_vector is None or self._index is None:
            return SpeechExampleSearchResult(failure_code="invalid_vector")
        ranked: list[SpeechExampleHit] = []
        for example in owned:
            similarity = self._cosine(
                query_vector, self._index[example.example_id]
            )
            if not math.isfinite(similarity):
                return SpeechExampleSearchResult(failure_code="invalid_vector")
            ranked.append(SpeechExampleHit(
                example=example,
                similarity=similarity,
            ))
        ranked.sort(key=lambda hit: (-hit.similarity, hit.example.example_id))
        deduplicated: list[SpeechExampleHit] = []
        seen: set[str] = set()
        for hit in ranked:
            if hit.example.example_id in seen:
                continue
            seen.add(hit.example.example_id)
            deduplicated.append(hit)
            if len(deduplicated) == min(limit, 3):
                break
        return SpeechExampleSearchResult(hits=tuple(deduplicated))


__all__ = [
    "SpeechExampleHit",
    "SpeechExampleRetriever",
    "SpeechExampleSearchResult",
    "VectorSpeechExampleRetriever",
]
