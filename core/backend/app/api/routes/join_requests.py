"""Conversation join-request queries and player approval commands."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ...contracts.conversation import JoinRequestResponseRequest

router = APIRouter(prefix="/api/runs/{run_id}/join-requests", tags=["join-requests"])


@router.get("/{join_request_id}")
async def get_join_request(
    request: Request,
    run_id: str,
    join_request_id: str,
) -> dict[str, Any]:
    return await request.app.state.run_service.get_join_request(
        run_id,
        join_request_id,
    )


@router.post("/{join_request_id}/respond")
async def respond_join_request(
    request: Request,
    run_id: str,
    join_request_id: str,
    body: JoinRequestResponseRequest,
) -> dict[str, Any]:
    return await request.app.state.run_service.respond_join_request(
        run_id,
        join_request_id,
        body.accepted,
        body.command_id,
    )
