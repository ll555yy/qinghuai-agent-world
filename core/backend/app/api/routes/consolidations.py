"""Retry endpoint for normal model consolidation failures."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/runs/{run_id}/consolidations", tags=["consolidations"])


@router.post("/{npc_id}/retry")
async def retry(request: Request, run_id: str, npc_id: str) -> dict[str, Any]:
    return await request.app.state.run_service.retry_consolidation(run_id, npc_id)
