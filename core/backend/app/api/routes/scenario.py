"""Public scenario metadata available before a Run is created."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/scenario", tags=["scenario"])


@router.get("/agendas")
async def public_agendas(request: Request) -> dict[str, Any]:
    registry = request.app.state.scenario_registry
    return {
        "chapter": {
            "chapterId": registry.chapter_id,
            "name": registry.chapter_name,
            "startDay": registry.start_day,
            "endDay": registry.end_day,
            "endsAt": f"Day{registry.end_day} {registry.end_hour:02d}:{registry.end_minute:02d}",
        },
        "agendas": [
            registry.public_agenda(agenda.agenda_id)
            for agenda in registry.public_agendas
        ],
        "actors": [registry.public_actor(npc.actor_id) for npc in registry.npcs],
    }
