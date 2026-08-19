"""Run event WebSocket endpoint."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...domain.errors import DomainError
from ...orchestration.run_service import RunService

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/runs/{run_id}")
async def run_websocket(websocket: WebSocket, run_id: str) -> None:
    service: RunService = websocket.app.state.run_service
    try:
        run = await service.get_run_entity(run_id)
    except DomainError:
        await websocket.close(code=4404, reason="run_not_found")
        return

    queue = await service.event_hub.subscribe(run_id)
    await websocket.accept()
    try:
        async with run.lock:
            snapshot = run.to_public_snapshot(service.registry)
            snapshot_event_seq = run.event_seq
        await websocket.send_json(snapshot)
        while True:
            event = await queue.get()
            if int(event["eventSeq"]) <= snapshot_event_seq:
                continue
            await websocket.send_json(event)
            snapshot_event_seq = int(event["eventSeq"])
    except WebSocketDisconnect:
        return
    finally:
        await service.event_hub.unsubscribe(run_id, queue)
