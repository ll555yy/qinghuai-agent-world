from __future__ import annotations

from types import SimpleNamespace

import pytest
from core.backend.app.persistence.embedding_indexer import EmbeddingIndexResult
from core.backend.app.persistence.indexing_repository import IndexingRunRepository


class _Delegate:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.saved: list[str] = []

    async def add(self, run: object) -> None:
        self.added.append(run.run_id)  # type: ignore[attr-defined]

    async def save(self, run: object, *, expected_revision: int | None = None) -> int:
        self.saved.append(run.run_id)  # type: ignore[attr-defined]
        return 7


class _Indexer:
    def __init__(self, *, fail: bool = False) -> None:
        self.run_ids: list[str] = []
        self.fail = fail

    async def index_missing(self, *, run_id: str) -> EmbeddingIndexResult:
        self.run_ids.append(run_id)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return EmbeddingIndexResult(indexed=1)


@pytest.mark.anyio
async def test_indexing_repository_indexes_only_after_committed_add_and_save() -> None:
    delegate = _Delegate()
    indexer = _Indexer()
    repository = IndexingRunRepository(delegate, indexer)  # type: ignore[arg-type]
    run = SimpleNamespace(run_id="run_1")

    await repository.add(run)  # type: ignore[arg-type]
    revision = await repository.save(run)  # type: ignore[arg-type]

    assert delegate.added == ["run_1"]
    assert delegate.saved == ["run_1"]
    assert indexer.run_ids == ["run_1", "run_1"]
    assert revision == 7


@pytest.mark.anyio
async def test_embedding_failure_does_not_undo_authoritative_run_save() -> None:
    delegate = _Delegate()
    indexer = _Indexer(fail=True)
    repository = IndexingRunRepository(delegate, indexer)  # type: ignore[arg-type]
    run = SimpleNamespace(run_id="run_2")

    revision = await repository.save(run)  # type: ignore[arg-type]

    assert revision == 7
    assert delegate.saved == ["run_2"]
