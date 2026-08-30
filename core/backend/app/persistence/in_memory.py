"""Single-process Run repository.

The in-memory implementation intentionally mirrors the durable repository
contract.  This keeps the orchestration code testable without PostgreSQL and
also catches missing save calls before the SQL repository is enabled.
"""

from __future__ import annotations

import asyncio

from ..domain.run import Run, RunEvent
from .run_repository import RepositoryConflictError


class InMemoryRunRepository:
    """A small async-safe repository; no database or external state is used."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._revisions: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def add(self, run: Run) -> None:
        async with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"Run {run.run_id!r} already exists")
            self._runs[run.run_id] = run
            self._revisions[run.run_id] = 0

    async def save(self, run: Run, *, expected_revision: int | None = None) -> int:
        """Atomically record a Run transition in the process cache."""

        async with self._lock:
            if run.run_id not in self._runs:
                raise KeyError(f"unknown Run: {run.run_id}")
            current = self._revisions[run.run_id]
            expected = current if expected_revision is None else expected_revision
            if expected != current:
                raise RepositoryConflictError(run.run_id, expected, current)
            revision = current + 1
            self._revisions[run.run_id] = revision
            return revision

    async def get(self, run_id: str) -> Run | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return list(run.events_after(after_seq))

    async def revision(self, run_id: str) -> int | None:
        async with self._lock:
            return self._revisions.get(run_id)

    async def list(self) -> list[Run]:
        async with self._lock:
            return list(self._runs.values())

    async def healthcheck(self) -> bool:
        return True

    async def close(self) -> None:
        return None


__all__ = ["InMemoryRunRepository"]
