"""Player invitation commands."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ...contracts.conversation import InvitationResponseRequest, PlayerInvitationRequest

router = APIRouter(prefix="/api/runs/{run_id}/invitations", tags=["invitations"])


@router.post("")
async def player_invite(request: Request, run_id: str, body: PlayerInvitationRequest) -> dict[str, Any]:
    return await request.app.state.run_service.player_invite(run_id, body.target_actor_id, body.command_id)


@router.post("/{invitation_id}/respond")
async def respond_invitation(request: Request, run_id: str, invitation_id: str, body: InvitationResponseRequest) -> dict[str, Any]:
    return await request.app.state.run_service.respond_invitation(run_id, invitation_id, body.accepted, body.command_id)
