"""RunRepository decorator that indexes committed Memory rows."""

from __future__ import annotations

import logging

from ..domain.run import Run, RunEvent
from .embedding_indexer import MemoryEmbeddingIndexer
from .run_repository import RunRepository

logger = logging.getLogger(__name__)


class IndexingRunRepository:
    """Keep Memory text authoritative when optional vector generation fails."""

    def __init__(self, delegate: RunRepository, indexer: MemoryEmbeddingIndexer) -> None:
        self._delegate = delegate
        self._indexer = indexer

    async def add(self, run: Run) -> None:
        await self._delegate.add(run)
        await self._index(run.run_id)

    async def save(self, run: Run, *, expected_revision: int | None = None) -> int:
        revision = await self._delegate.save(run, expected_revision=expected_revision)
        await self._index(run.run_id)
        return revision

    async def _index(self, run_id: str) -> None:
        try:
            result = await self._indexer.index_missing(run_id=run_id)
        except Exception as exc:
            logger.warning("memory embedding indexing failed error_type=%s", type(exc).__name__)
            return
        if result.failed:
            logger.warning(
                "memory embedding indexing partially failed failed=%d", result.failed
            )

    async def get(self, run_id: str) -> Run | None:
        return await self._delegate.get(run_id)

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        return await self._delegate.events_after(run_id, after_seq)

    async def revision(self, run_id: str) -> int | None:
        return await self._delegate.revision(run_id)

    async def list(self) -> list[Run]:
        return await self._delegate.list()

    async def healthcheck(self) -> bool:
        return await self._delegate.healthcheck()

    async def close(self) -> None:
        await self._delegate.close()


__all__ = ["IndexingRunRepository"]
