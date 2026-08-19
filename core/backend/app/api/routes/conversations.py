"""Conversation command routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...contracts.conversation import (
    CreateConversationRequest,
    LeaveCommandRequest,
    ParticipantCommandRequest,
    PlayerMessageRequest,
)
from ...orchestration.run_service import RunService

router = APIRouter(prefix="/api/runs/{run_id}/conversations", tags=["conversations"])


def _service(request: Request) -> RunService:
    return request.app.state.run_service


@router.post("")
async def create_conversation(
    request: Request, run_id: str, body: CreateConversationRequest
) -> dict[str, Any]:
    return await _service(request).create_conversation(
        run_id,
        body.participant_ids,
        body.command_id,
    )


@router.post("/{conversation_id}/participants")
async def add_participant(
    request: Request, run_id: str, conversation_id: str, body: ParticipantCommandRequest
) -> dict[str, Any]:
    return await _service(request).add_participant(
        run_id,
        conversation_id,
        body.actor_id,
        body.command_id,
    )


@router.delete("/{conversation_id}/participants/{actor_id}")
async def remove_participant(
    request: Request,
    run_id: str,
    conversation_id: str,
    actor_id: str,
    body: LeaveCommandRequest | None = Body(default=None),
) -> dict[str, Any]:
    return await _service(request).remove_participant(
        run_id,
        conversation_id,
        actor_id,
        body.command_id if body is not None else None,
    )


@router.get("/{conversation_id}/messages")
async def get_messages(request: Request, run_id: str, conversation_id: str) -> dict[str, Any]:
    return await _service(request).get_messages(run_id, conversation_id)


@router.post("/{conversation_id}/join")
async def player_join(request: Request, run_id: str, conversation_id: str, body: LeaveCommandRequest | None = Body(default=None)) -> dict[str, Any]:
    return await _service(request).player_join(run_id, conversation_id, body.command_id if body else None)


@router.post("/{conversation_id}/messages")
async def player_message(request: Request, run_id: str, conversation_id: str, body: PlayerMessageRequest) -> dict[str, Any]:
    return await _service(request).player_message(run_id, conversation_id, body.text, body.command_id)


@router.post("/{conversation_id}/idle")
async def conversation_idle(request: Request, run_id: str, conversation_id: str) -> dict[str, Any]:
    return await _service(request).conversation_idle(run_id, conversation_id)
