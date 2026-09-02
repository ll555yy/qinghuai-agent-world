from __future__ import annotations

from pathlib import Path

import core.backend.app.main as main_module
from core.backend.app.main import create_app
from core.backend.app.settings import Settings
from fastapi.testclient import TestClient


class FakeEmbeddingClient:
    dimensions = 2048
    model_name = "fake-embedding"

    async def embed(self, _text: str) -> list[float]:
        return [1.0, *([0.0] * 2047)]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    async def close(self) -> None:
        return None


def test_liveness_and_scenario_readiness(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "processAlive": True,
        "scenarioLoaded": True,
    }


def test_memory_backend_wires_speech_example_retriever(monkeypatch) -> None:
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    fake_embedding = FakeEmbeddingClient()
    monkeypatch.setattr(
        main_module, "ArkEmbeddingClient", lambda _settings: fake_embedding
    )
    settings = Settings(
        scenario_dir=scenario_dir,
        persistence_backend="memory",
        embedding_model="fake-embedding",
    )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.embedding_client is fake_embedding
        assert client.app.state.speech_example_retriever is not None
        assert (
            client.app.state.run_service.speech_example_retriever
            is client.app.state.speech_example_retriever
        )
