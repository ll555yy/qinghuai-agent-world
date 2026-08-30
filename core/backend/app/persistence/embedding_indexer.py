"""Idempotent, transaction-safe indexing of Memory text embeddings."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, update

from ..ai.embedding import MEMORY_EMBEDDING_DIMENSIONS, EmbeddingPort
from ..db.models import Memory


@dataclass(frozen=True, slots=True)
class EmbeddingIndexResult:
    indexed: int = 0
    skipped: int = 0
    failed: int = 0


class MemoryEmbeddingIndexer:
    """Generate vectors outside transactions and write them with content guards."""

    def __init__(
        self,
        session_factory: Any,
        embedding_port: EmbeddingPort,
        *,
        batch_size: int = 8,
        dimensions: int = MEMORY_EMBEDDING_DIMENSIONS,
    ) -> None:
        if dimensions != MEMORY_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"memory indexer requires {MEMORY_EMBEDDING_DIMENSIONS}-dimension vectors"
            )
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        self._session_factory = session_factory
        self._embedding_port = embedding_port
        self._batch_size = batch_size
        self._dimensions = dimensions

    async def index_missing(
        self,
        *,
        run_id: str | None = None,
        limit: int | None = None,
        force: bool = False,
    ) -> EmbeddingIndexResult:
        """Index missing/stale vectors, or all rows when ``force`` is requested.

        A second invocation is a no-op for already indexed rows.  The content
        predicate on UPDATE prevents an old provider result overwriting a
        Memory that changed while the request was in flight.
        """

        target_model = self._embedding_port.model_name
        needs_index = or_(
            Memory.embedding.is_(None),
            Memory.embedding_model.is_distinct_from(target_model),
            Memory.embedding_dimensions.is_distinct_from(self._dimensions),
        )
        async with self._session_factory() as session:
            statement = select(
                Memory.run_id,
                Memory.memory_id,
                Memory.owner_npc_id,
                Memory.content,
            ).order_by(Memory.run_id, Memory.memory_id)
            if run_id is not None:
                statement = statement.where(Memory.run_id == run_id)
            if not force:
                statement = statement.where(needs_index)
            if limit is not None:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).mappings())

            already_indexed = 0
            if not force:
                skipped_statement = select(func.count()).select_from(Memory).where(
                    ~needs_index
                )
                if run_id is not None:
                    skipped_statement = skipped_statement.where(Memory.run_id == run_id)
                already_indexed = int(
                    (await session.scalar(skipped_statement)) or 0
                )

        indexed = failed = 0
        skipped = already_indexed
        for start in range(0, len(rows), self._batch_size):
            batch = rows[start : start + self._batch_size]
            generated: list[tuple[Any, str, str, list[float]] | None] = []
            try:
                vectors = await self._embed_batch([str(row["content"]) for row in batch])
            except Exception:
                failed += len(batch)
                continue
            for row, raw_vector in zip(batch, vectors, strict=True):
                try:
                    vector = [float(value) for value in raw_vector]
                    if len(vector) != self._dimensions or not all(
                        math.isfinite(value) for value in vector
                    ):
                        raise ValueError("embedding dimensions or values are invalid")
                except Exception:
                    generated.append(None)
                    failed += 1
                    continue
                generated.append((row["run_id"], row["memory_id"], row["content"], vector))

            async with self._session_factory() as session:
                async with session.begin():
                    for _row, item in zip(batch, generated, strict=True):
                        if item is None:
                            continue
                        row_run_id, memory_id, content, vector = item
                        update_statement = (
                            update(Memory)
                            .where(
                                Memory.run_id == row_run_id,
                                Memory.memory_id == memory_id,
                                Memory.content == content,
                            )
                            .values(
                                embedding=vector,
                                embedding_model=target_model,
                                embedding_dimensions=self._dimensions,
                            )
                        )
                        if not force:
                            update_statement = update_statement.where(needs_index)
                        result = await session.execute(update_statement)
                        if result.rowcount:
                            indexed += int(result.rowcount)
                        else:
                            skipped += 1
        return EmbeddingIndexResult(indexed=indexed, skipped=skipped, failed=failed)

    async def _embed_batch(self, texts: list[str]) -> Sequence[Sequence[float]]:
        embed_many = getattr(self._embedding_port, "embed_many", None)
        if callable(embed_many):
            vectors = await embed_many(texts)
            if len(vectors) != len(texts):
                raise ValueError("embedding provider returned the wrong batch size")
            return vectors
        return [await self._embedding_port.embed(text) for text in texts]


__all__ = ["EmbeddingIndexResult", "MemoryEmbeddingIndexer"]
