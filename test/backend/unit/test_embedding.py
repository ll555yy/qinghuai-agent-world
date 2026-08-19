from __future__ import annotations

from types import SimpleNamespace

import pytest
from core.backend.app.ai.ark_embedding import (
    DEFAULT_ARK_EMBEDDING_BASE_URL,
    ArkEmbeddingClient,
    ArkEmbeddingSettings,
)
from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from core.backend.app.persistence.normalized_projection import _memory_rows
from core.backend.scripts import check_ark_embedding


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        inputs = kwargs["input"]
        assert isinstance(inputs, list)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    index=index,
                    embedding=[float(index + 1)] * MEMORY_EMBEDDING_DIMENSIONS,
                )
                for index, _ in enumerate(inputs)
            ]
        )


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


class _InvalidEmbeddings:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def create(self, **_kwargs: object) -> object:
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=self.vector)],
            usage=None,
        )


@pytest.mark.anyio
async def test_ark_embedding_uses_explicit_model_and_standard_endpoint() -> None:
    fake = _FakeClient()
    provider = ArkEmbeddingClient(
        ArkEmbeddingSettings(model="ep-test", api_key="not-reported"),
        client=fake,
    )

    vector = await provider.embed("书店计划")

    assert len(vector) == MEMORY_EMBEDDING_DIMENSIONS
    assert fake.embeddings.kwargs == {
        "model": "ep-test",
        "input": ["书店计划"],
        "encoding_format": "float",
        "timeout": provider._timeout,
    }
    assert provider.settings.base_url == DEFAULT_ARK_EMBEDDING_BASE_URL


@pytest.mark.anyio
async def test_ark_embedding_batches_inputs_in_one_provider_call() -> None:
    fake = _FakeClient()
    provider = ArkEmbeddingClient(
        ArkEmbeddingSettings(model="ep-test", api_key="not-reported"),
        client=fake,
    )

    vectors = await provider.embed_many(["旧书店", "公益文社"])

    assert len(vectors) == 2
    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0
    assert fake.embeddings.kwargs is not None
    assert fake.embeddings.kwargs["input"] == ["旧书店", "公益文社"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "vector",
    (
        [0.1] * (MEMORY_EMBEDDING_DIMENSIONS - 1),
        [float("nan")] * MEMORY_EMBEDDING_DIMENSIONS,
        [float("inf")] * MEMORY_EMBEDDING_DIMENSIONS,
    ),
)
async def test_ark_embedding_rejects_wrong_dimension_and_non_finite_values(
    vector: list[float],
) -> None:
    fake = SimpleNamespace(embeddings=_InvalidEmbeddings(vector))
    provider = ArkEmbeddingClient(
        ArkEmbeddingSettings(model="ep-test", api_key="not-reported"),
        client=fake,
    )

    with pytest.raises(ValueError, match="dimensions"):
        await provider.embed("书店计划")


@pytest.mark.anyio
async def test_ark_embedding_rejects_empty_and_oversized_batches() -> None:
    provider = ArkEmbeddingClient(
        ArkEmbeddingSettings(model="ep-test", api_key="not-reported"),
        client=_FakeClient(),
    )

    with pytest.raises(ValueError, match="1..256"):
        await provider.embed_many([])
    with pytest.raises(ValueError, match="1..256"):
        await provider.embed_many(["x"] * 257)


def test_ark_embedding_model_is_required() -> None:
    with pytest.raises(ValueError, match="ARK_EMBEDDING_MODEL"):
        ArkEmbeddingSettings(model="", api_key="not-reported")


def test_embedding_probe_provider_failure_projection_is_safe() -> None:
    class BadRequestError(Exception):
        status_code = 400
        code = "InvalidParameter"
        body = {"message": "must never be projected"}
        response = SimpleNamespace(headers={"x-request-id": "safe-request-id"})

    report = check_ark_embedding._safe_provider_failure(BadRequestError("secret text"))

    assert report == {
        "exceptionType": "BadRequestError",
        "httpStatus": 400,
        "providerErrorCode": "InvalidParameter",
        "providerRequestId": "safe-request-id",
    }
    assert "secret" not in str(report)


@pytest.mark.anyio
async def test_embedding_probe_is_dry_by_default_and_safe_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dry_report, dry_code = await check_ark_embedding.run_probe(live=False)
    assert dry_report == {
        "live": False,
        "requestSent": False,
        "status": "dry_run",
    }
    assert dry_code == 0

    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_EMBEDDING_MODEL", raising=False)
    live_report, live_code = await check_ark_embedding.run_probe(live=True)
    assert live_report["requestSent"] is False
    assert live_report["status"] == "not_configured"
    assert live_code == 2


def test_projection_preserves_unchanged_embedding_and_invalidates_changed_content() -> None:
    run = SimpleNamespace(
        run_id="run_test",
        memories={
            "m_same": {
                "ownerNpcId": "npc_001",
                "content": "unchanged",
                "createdAt": "Day1 09:00",
            },
            "m_changed": {
                "ownerNpcId": "npc_001",
                "content": "new text",
                "createdAt": "Day1 09:00",
            },
        },
        segments={},
        clock=SimpleNamespace(current=SimpleNamespace(day=1, clock_minutes=540)),
    )
    old_vector = [0.1] * MEMORY_EMBEDDING_DIMENSIONS

    rows = _memory_rows(
        run,
        existing_embeddings={
            "m_same": {
                "owner_npc_id": "npc_001",
                "content": "unchanged",
                "embedding": old_vector,
                "embedding_model": "ep-old",
                "embedding_dimensions": MEMORY_EMBEDDING_DIMENSIONS,
            },
            "m_changed": {
                "owner_npc_id": "npc_001",
                "content": "old text",
                "embedding": old_vector,
                "embedding_model": "ep-old",
                "embedding_dimensions": MEMORY_EMBEDDING_DIMENSIONS,
            },
        },
    )

    by_id = {row["memory_id"]: row for row in rows}
    assert by_id["m_same"]["embedding"] == old_vector
    assert by_id["m_same"]["embedding_model"] == "ep-old"
    assert by_id["m_changed"]["embedding"] is None
    assert by_id["m_changed"]["embedding_model"] is None
