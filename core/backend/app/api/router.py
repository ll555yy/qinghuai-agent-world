"""Top-level API router."""

from fastapi import APIRouter

from .routes.ai import router as ai_router
from .routes.consolidations import router as consolidations_router
from .routes.conversations import router as conversations_router
from .routes.health import router as health_router
from .routes.invitations import router as invitations_router
from .routes.join_requests import router as join_requests_router
from .routes.runs import router as runs_router
from .routes.scenario import router as scenario_router
from .routes.websocket import router as websocket_router

router = APIRouter()
router.include_router(ai_router)
router.include_router(health_router)
router.include_router(scenario_router)
router.include_router(runs_router)
router.include_router(conversations_router)
router.include_router(invitations_router)
router.include_router(join_requests_router)
router.include_router(consolidations_router)
router.include_router(websocket_router)
