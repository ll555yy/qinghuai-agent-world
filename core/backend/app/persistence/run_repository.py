"""Repository contracts shared by in-memory and SQL-backed Run stores.

The orchestration layer deliberately deals in the :class:`~app.domain.run.Run`
aggregate rather than SQLAlchemy rows.  A repository therefore has two small
responsibilities: keep the aggregate identity stable while the process is
running, and atomically save a complete state transition together with its
new event records.  The latter is important because ``Run.append_event`` only
updates the in-memory counters; it is not itself a database operation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.run import Run, RunEvent


class RepositoryConflictError(RuntimeError):
    """The caller attempted to save an out-of-date Run revision."""

    def __init__(self, run_id: str, expected: int, actual: int) -> None:
        self.run_id = run_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Run {run_id!r} revision conflict: expected {expected}, got {actual}."
        )


@runtime_checkable
class RunRepository(Protocol):
    async def add(self, run: Run) -> None:
        """Create a newly initialized Run and its initial events atomically."""

    async def get(self, run_id: str) -> Run | None:
        """Return the process-cached Run or ``None`` when it does not exist."""

    async def save(self, run: Run, *, expected_revision: int | None = None) -> int:
        """Atomically save state, commands, and events; return new revision.

        ``expected_revision`` is optional for backwards compatibility with the
        first in-memory implementation.  SQL callers should always provide it
        (or obtain it from :meth:`revision`) so a stale worker cannot overwrite
        another worker's transition.
        """

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        """Return durable events after ``after_seq`` in ascending order."""

    async def revision(self, run_id: str) -> int | None:
        """Return the storage revision for a Run, if it exists."""

    async def list(self) -> list[Run]:
        """Return all runs, primarily for diagnostics and tests."""

    async def healthcheck(self) -> bool:
        """Return whether the backing store is reachable and usable."""

    async def close(self) -> None:
        """Release backing resources; safe to call more than once."""


__all__ = ["RepositoryConflictError", "RunRepository"]
