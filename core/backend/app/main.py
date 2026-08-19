"""FastAPI application entrypoint for the in-memory backend phase."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .ai.ark_client import ArkClient
from .api.router import router as api_router
from .db.bootstrap import sync_scenario
from .domain.errors import DomainError
from .orchestration.run_service import RunService
from .persistence.in_memory import InMemoryRunRepository
from .persistence.memory_retriever import DatabaseMemoryRetriever
from .persistence.run_repository import RunRepository
from .persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from .scenario.loader import ScenarioLoader, ScenarioValidationError
from .settings import Settings

if sys.platform == "win32":
    # Keep local Uvicorn compatible with psycopg's asynchronous connections.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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
        repository: RunRepository
        if runtime_settings.persistence_backend == "postgres":
            repository = SQLAlchemyRunRepository(
                runtime_settings.database_url,
                chapter_id=registry.chapter_id,
                echo=runtime_settings.database_echo,
            )
            if not await repository.healthcheck():
                await repository.close()
                raise RuntimeError(
                    "PostgreSQL is unavailable or not migrated; run `alembic upgrade head`."
                )
            await sync_scenario(repository.session_factory, registry)
            memory_retriever = DatabaseMemoryRetriever(
                repository.session_factory,
                vector_dimensions=runtime_settings.memory_embedding_dimensions,
            )
        else:
            repository = InMemoryRunRepository()
            memory_retriever = None
        application.state.run_repository = repository
        application.state.database_configured = runtime_settings.persistence_backend == "postgres"
        application.state.run_service = RunService(
            registry,
            repository=repository,
            text_model=application.state.ai_client,
            memory_retriever=memory_retriever,
        )
        application.state.scenario_loaded = True
        try:
            yield
        finally:
            await application.state.ai_client.close()
            await application.state.run_repository.close()

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
