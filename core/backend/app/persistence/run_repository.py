"""Repository protocol for in-memory Run storage."""

from __future__ import annotations

from typing import Protocol

from ..domain.run import Run


class RunRepository(Protocol):
    async def add(self, run: Run) -> None:
        """Store a newly created Run."""

    async def get(self, run_id: str) -> Run | None:
        """Return a Run or ``None`` when it does not exist."""

    async def list(self) -> list[Run]:
        """Return all runs, primarily for diagnostics and tests."""

