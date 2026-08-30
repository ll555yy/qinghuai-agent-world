"""Run creation, snapshot, time, and event REST routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, status

from ...contracts.run import AdvanceTimeRequest, CreateRunRequest, WorldStepRequest
from ...orchestration.run_service import RunService

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _service(request: Request) -> RunService:
    return request.app.state.run_service


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_run(request: Request, body: CreateRunRequest | None = None) -> dict[str, Any]:
    body = body or CreateRunRequest()
    return await _service(request).create_run(body.agenda_id, body.seed)


@router.get("/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    return await _service(request).get_run(run_id)


@router.post("/{run_id}/time/advance")
async def advance_time(request: Request, run_id: str, body: AdvanceTimeRequest) -> dict[str, Any]:
    return await _service(request).advance_time(
        run_id,
        body.virtual_minutes,
        body.command_id,
    )


@router.post("/{run_id}/world/step")
async def world_step(request: Request, run_id: str, body: WorldStepRequest) -> dict[str, Any]:
    return await _service(request).world_step(run_id, body.real_seconds, body.command_id)


@router.get("/{run_id}/actors/{actor_id}")
async def get_actor(request: Request, run_id: str, actor_id: str) -> dict[str, Any]:
    return await _service(request).get_actor_public(run_id, actor_id)


@router.get("/{run_id}/agendas")
async def get_agendas(request: Request, run_id: str) -> dict[str, Any]:
    return {"agendas": await _service(request).get_public_agendas(run_id)}


@router.get("/{run_id}/events")
async def get_events(
    request: Request,
    run_id: str,
    after_seq: int = Query(default=0, alias="afterSeq", ge=0),
) -> dict[str, Any]:
    return await _service(request).get_events(run_id, after_seq)
