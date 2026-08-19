"""FastAPI application entrypoint for the in-memory backend phase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .ai.ark_client import ArkClient
from .api.router import router as api_router
from .domain.errors import DomainError
from .orchestration.run_service import RunService
from .scenario.loader import ScenarioLoader, ScenarioValidationError
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            registry = ScenarioLoader(runtime_settings.scenario_dir).load()
        except ScenarioValidationError as exc:
            application.state.scenario_loaded = False
            raise RuntimeError(f"Scenario startup validation failed: {exc}") from exc
        application.state.scenario_registry = registry
        application.state.ai_client = ArkClient()
        application.state.run_service = RunService(registry, text_model=application.state.ai_client)
        application.state.scenario_loaded = True
        try:
            yield
        finally:
            await application.state.ai_client.close()

    application = FastAPI(title=runtime_settings.app_name, version="0.1.0", lifespan=lifespan)

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    application.include_router(api_router)
    return application


app = create_app()

__all__ = ["app", "create_app"]
