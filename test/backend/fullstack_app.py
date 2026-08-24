"""Deterministic PostgreSQL-backed app used only by the full-stack browser gate.

This module deliberately does not change the production application factory.
The fake ports are constructed and passed to :class:`RunService` here, so a
normal production import still creates the default Ark client and has no fake
model environment-variable switch.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from core.backend.app.ai.models import TextGenerationRequest, TextGenerationResult
from core.backend.app.api.router import router as api_router
from core.backend.app.db.bootstrap import sync_scenario
from core.backend.app.domain.errors import DomainError
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.embedding_indexer import MemoryEmbeddingIndexer
from core.backend.app.persistence.indexing_repository import IndexingRunRepository
from core.backend.app.persistence.memory_retriever import DatabaseMemoryRetriever
from core.backend.app.persistence.run_repository import RunRepository
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from core.backend.app.scenario.loader import ScenarioLoader, ScenarioValidationError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# The production entrypoint applies the same policy. This test-only app is
# imported directly by Uvicorn, so keep psycopg's Windows event-loop contract
# explicit here rather than changing production startup code.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = PROJECT_ROOT / "core" / "scenario"


class DeterministicTextModel:
    """A legal, deterministic implementation of the text-model port.

    Responses are selected by the protocol supplied in the request's system
    prompt and are always valid protocol objects.  No HTTP client is created.
    """

    provider = "test-deterministic"
    model_name = "fullstack-fake-text-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.chat_calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        self.calls.append(protocol)
        if protocol == "DailyActionDecision":
            payload: dict[str, Any] = {"action": "wait"}
        elif protocol == "InvitationDecision":
            payload = {"decision": "accept"}
        elif protocol == "ChatDecision":
            self.chat_calls += 1
            if self.chat_calls == 1:
                # Force the real owner-safe PostgreSQL retrieval path once;
                # the second graph pass then produces the visible reply.
                payload = {
                    "result": "need_memory",
                    "memoryQuery": {"queryText": "书店", "limit": 1},
                }
            else:
                payload = {
                    "result": "decided",
                    "action": "speak",
                    "responseDesire": 0,
                    "intent": "回应玩家刚才的公开发言",
                }
        elif protocol == "SpeechGeneration":
            payload = {"text": "我愿意先把书店的底线和方案说清楚。"}
        elif protocol == "SegmentSummary":
            payload = {
                "claims": [],
                "commitments": [],
                "revealedFacts": [],
                "openQuestions": [],
                "actorIds": [],
                "topicHints": [],
            }
        elif protocol == "ExitConsolidation":
            payload = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        else:
            raise AssertionError(f"unexpected protocol: {protocol}")
        return TextGenerationResult(
            text=json.dumps(payload, ensure_ascii=False),
            provider=self.provider,
            model=self.model_name,
        )

    async def close(self) -> None:
        return None

    def status(self) -> dict[str, str | bool]:
        return {
            "configured": True,
            "provider": self.provider,
            "model": self.model_name,
            "baseUrlHost": "",
        }


class DeterministicEmbedding:
    """A fixed 2048-dimensional embedding for the real pgvector code path."""

    dimensions = 2048
    model_name = "fullstack-fake-embedding-v1"

    async def embed(self, _text: str) -> list[float]:
        return [1.0, *([0.0] * (self.dimensions - 1))]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        vector = [1.0, *([0.0] * (self.dimensions - 1))]
        return [list(vector) for _ in texts]


def create_test_app(database_url: str) -> FastAPI:
    """Create a fully wired app with explicit deterministic test ports."""

    if not database_url.strip():
        raise ValueError("a dedicated PostgreSQL URL is required")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            registry = ScenarioLoader(SCENARIO_DIR).load()
        except ScenarioValidationError as exc:
            application.state.scenario_loaded = False
            raise RuntimeError(f"Scenario startup validation failed: {exc}") from exc

        base_repository = SQLAlchemyRunRepository(
            database_url,
            chapter_id=registry.chapter_id,
        )
        if not await base_repository.healthcheck():
            await base_repository.close()
            raise RuntimeError("PostgreSQL is unavailable or not migrated")
        await sync_scenario(base_repository.session_factory, registry)

        embedding = DeterministicEmbedding()
        indexer = MemoryEmbeddingIndexer(
            base_repository.session_factory,
            embedding,
            dimensions=embedding.dimensions,
        )
        repository: RunRepository = IndexingRunRepository(base_repository, indexer)
        retriever = DatabaseMemoryRetriever(
            base_repository.session_factory,
            embedding_port=embedding,
            vector_dimensions=embedding.dimensions,
        )
        text_model = DeterministicTextModel()

        application.state.scenario_registry = registry
        application.state.ai_client = text_model
        application.state.embedding_client = embedding
        application.state.embedding_indexer = indexer
        application.state.run_repository = repository
        application.state.database_configured = True
        application.state.run_service = RunService(
            registry,
            repository=repository,
            text_model=text_model,
            memory_retriever=retriever,
        )
        application.state.scenario_loaded = True
        try:
            yield
        finally:
            await text_model.close()
            await repository.close()

    application = FastAPI(
        title="Qinghuai Full-stack Test App",
        version="0.1.0-test",
        lifespan=lifespan,
    )

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


_DATABASE_URL = os.environ.get("QINGHUAI_TEST_DATABASE_URL", "").strip()
if not _DATABASE_URL:
    raise RuntimeError("QINGHUAI_TEST_DATABASE_URL must be set for the full-stack app")

app = create_test_app(_DATABASE_URL)

__all__ = [
    "DeterministicEmbedding",
    "DeterministicTextModel",
    "app",
    "create_test_app",
]
