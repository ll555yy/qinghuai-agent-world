"""Liveness and scenario readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


def _health(request: Request) -> dict[str, Any]:
    loaded = bool(getattr(request.app.state, "scenario_loaded", False))
    return {
        "status": "ok" if loaded else "degraded",
        "processAlive": True,
        "scenarioLoaded": loaded,
    }


@router.get("/health")
@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return _health(request)

