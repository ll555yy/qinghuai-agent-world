"""Read-only AI adapter status endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ...ai.ark_client import ArkClient

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ai_status(request: Request) -> dict[str, Any]:
    client: ArkClient = request.app.state.ai_client
    return client.status()

