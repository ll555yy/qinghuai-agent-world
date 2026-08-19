"""Run event WebSocket endpoint."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...domain.errors import DomainError
from ...orchestration.run_service import RunService

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/runs/{run_id}")
async def run_websocket(websocket: WebSocket, run_id: str) -> None:
    service: RunService = websocket.app.state.run_service
    raw_after_seq = websocket.query_params.get("afterSeq")
    after_seq: int | None = None
    if raw_after_seq is not None:
        try:
            after_seq = int(raw_after_seq)
        except ValueError:
            await websocket.close(code=4400, reason="invalid_after_seq")
            return
        if after_seq < 0:
            await websocket.close(code=4400, reason="invalid_after_seq")
            return
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
        if after_seq is not None:
            replay = await service.get_events(run_id, after_seq)
            for event in replay["events"]:
                if int(event["eventSeq"]) <= snapshot_event_seq:
                    await websocket.send_json(event)
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
