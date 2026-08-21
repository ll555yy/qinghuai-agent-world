"""Thin named facade for world stepping.

Keeping this facade lets the future real-time driver use the same scheduler
without duplicating the RunService command and locking rules.
"""

from __future__ import annotations

from typing import Any

from .run_service import RunService


class WorldEngine:
    def __init__(self, service: RunService) -> None:
        self.service = service

    async def step(self, run_id: str, real_seconds: int, command_id: str | None = None) -> dict[str, Any]:
        """Advance foreground time; the service applies the scenario ratio."""
        return await self.service.world_step(run_id, real_seconds, command_id)


__all__ = ["WorldEngine"]
