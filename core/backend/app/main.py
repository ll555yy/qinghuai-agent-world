"""FastAPI application entrypoint for the in-memory backend phase."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .ai.ark_client import ArkClient
from .ai.ark_embedding import ArkEmbeddingClient, ArkEmbeddingSettings
from .api.router import router as api_router
from .db.bootstrap import sync_scenario
from .domain.errors import DomainError
from .orchestration.run_service import RunService
from .persistence.embedding_indexer import MemoryEmbeddingIndexer
from .persistence.in_memory import InMemoryRunRepository
from .persistence.indexing_repository import IndexingRunRepository
from .persistence.memory_retriever import DatabaseMemoryRetriever
from .persistence.run_repository import RunRepository
from .persistence.speech_example_retriever import VectorSpeechExampleRetriever
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
        application.state.embedding_client = None
        application.state.embedding_indexer = None
        application.state.speech_example_retriever = None
        embedding_port = None
        if runtime_settings.embedding_model:
            embedding_settings = (
                ArkEmbeddingSettings(
                    model=runtime_settings.embedding_model,
                    base_url=runtime_settings.embedding_base_url,
                )
                if runtime_settings.embedding_base_url
                else ArkEmbeddingSettings(model=runtime_settings.embedding_model)
            )
            application.state.embedding_client = ArkEmbeddingClient(
                embedding_settings
            )
            embedding_port = application.state.embedding_client
            application.state.speech_example_retriever = (
                VectorSpeechExampleRetriever(
                    registry.speech_examples,
                    embedding_port,
                    dimensions=runtime_settings.memory_embedding_dimensions,
                )
            )
        repository: RunRepository
        if runtime_settings.persistence_backend == "postgres":
            base_repository = SQLAlchemyRunRepository(
                runtime_settings.database_url,
                chapter_id=registry.chapter_id,
                echo=runtime_settings.database_echo,
            )
            if not await base_repository.healthcheck():
                await base_repository.close()
                raise RuntimeError(
                    "PostgreSQL is unavailable or not migrated; run `alembic upgrade head`."
                )
            await sync_scenario(base_repository.session_factory, registry)
            if embedding_port is not None:
                application.state.embedding_indexer = MemoryEmbeddingIndexer(
                    base_repository.session_factory,
                    embedding_port,
                    dimensions=runtime_settings.memory_embedding_dimensions,
                )
            memory_retriever = DatabaseMemoryRetriever(
                base_repository.session_factory,
                embedding_port=embedding_port,
                vector_dimensions=runtime_settings.memory_embedding_dimensions,
            )
            repository = (
                IndexingRunRepository(
                    base_repository, application.state.embedding_indexer
                )
                if application.state.embedding_indexer is not None
                else base_repository
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
            speech_example_retriever=application.state.speech_example_retriever,
            segment_summary_trigger_messages=(
                runtime_settings.segment_summary_threshold
            ),
            segment_summary_trigger_tokens=(
                runtime_settings.segment_summary_token_threshold
            ),
            segment_summary_recent_messages=(
                runtime_settings.segment_recent_messages
            ),
            segment_boundary_carryover_messages=(
                runtime_settings.segment_boundary_carryover_messages
            ),
            model_max_concurrency=runtime_settings.model_max_concurrency,
            chat_cooldown_seconds=runtime_settings.chat_cooldown_seconds,
            chat_publish_delay_min_seconds=(
                runtime_settings.chat_publish_delay_min_seconds
            ),
            chat_publish_delay_max_seconds=(
                runtime_settings.chat_publish_delay_max_seconds
            ),
            chat_model_call_timeout_seconds=(
                runtime_settings.chat_model_call_timeout_seconds
            ),
        )
        application.state.scenario_loaded = True
        try:
            yield
        finally:
            await application.state.run_service.close()
            await application.state.ai_client.close()
            if application.state.embedding_client is not None:
                await application.state.embedding_client.close()
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
