"""Liveness and scenario readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


async def _health(request: Request) -> dict[str, Any]:
    loaded = bool(getattr(request.app.state, "scenario_loaded", False))
    repository = getattr(request.app.state, "run_repository", None)
    database_configured = bool(
        getattr(request.app.state, "database_configured", False)
    )
    storage_healthy = bool(repository is not None and await repository.healthcheck())
    result = {
        "status": "ok" if loaded and storage_healthy else "degraded",
        "processAlive": True,
        "scenarioLoaded": loaded,
    }
    if database_configured:
        result.update(persistence="postgres", storageHealthy=storage_healthy)
    return result


@router.get("/health")
@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return await _health(request)
