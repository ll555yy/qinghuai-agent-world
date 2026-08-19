"""Single-process Run repository."""

from __future__ import annotations

import asyncio

from ..domain.run import Run


class InMemoryRunRepository:
    """A small async-safe repository; no database or external state is used."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()

    async def add(self, run: Run) -> None:
        async with self._lock:
            self._runs[run.run_id] = run

    async def get(self, run_id: str) -> Run | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def list(self) -> list[Run]:
        async with self._lock:
            return list(self._runs.values())

