from __future__ import annotations

import math

import pytest

from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from core.backend.app.persistence.speech_example_retriever import (
    VectorSpeechExampleRetriever,
)


def _vector(x: float, y: float = 0.0) -> list[float]:
    return [x, y, *([0.0] * (MEMORY_EMBEDDING_DIMENSIONS - 2))]


class FakeEmbeddingPort:
    dimensions = MEMORY_EMBEDDING_DIMENSIONS
    model_name = "fake"

    def __init__(
        self,
        vectors: dict[str, list[float]],
        *,
        fail: bool = False,
        wrong_batch_size: bool = False,
    ) -> None:
        self.vectors = vectors
        self.fail = fail
        self.wrong_batch_size = wrong_batch_size
        self.calls: list[str] = []
        self.batch_sizes: list[int] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("unavailable")
        return self.vectors[text]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        values = [await self.embed(text) for text in texts]
        return values[:-1] if self.wrong_batch_size else values


def _vectors(registry, intent: str) -> dict[str, list[float]]:
    return {
        intent: _vector(1.0),
        **{
            VectorSpeechExampleRetriever.index_text(example): _vector(1.0)
            for example in registry.speech_examples.values()
        },
    }


@pytest.mark.anyio
async def test_retrieval_is_npc_scoped_deterministic_and_cached(registry) -> None:
    first_intent = "拒绝替玩家向别人说情"
    second_intent = "追问具体承诺"
    vectors = _vectors(registry, first_intent)
    vectors[second_intent] = _vector(0.0, 1.0)
    port = FakeEmbeddingPort(vectors)
    retriever = VectorSpeechExampleRetriever(registry.speech_examples, port)

    first = await retriever.search(
        npc_id="npc_001", intent=first_intent, limit=99
    )
    index_call_count = len(port.calls) - 1
    second = await retriever.search(npc_id="npc_001", intent=second_intent)

    assert len(first.hits) == 3
    assert all(hit.example.npc_id == "npc_001" for hit in first.hits)
    assert [hit.example.example_id for hit in first.hits] == sorted(
        example.example_id
        for example in registry.speech_examples.values()
        if example.npc_id == "npc_001"
    )[:3]
    assert len(second.hits) == 3
    assert len(port.calls) == index_call_count + 2
    assert port.batch_sizes == [10, 10, 10, 10, 4]
    assert all("这话我替你递不合适" not in text for text in port.calls)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("npc_id", "intent", "failure_code"),
    [
        ("npc_missing", "有效意图", "unknown_npc"),
        ("npc_001", "   ", "empty_intent"),
    ],
)
async def test_invalid_query_fails_open(
    registry, npc_id: str, intent: str, failure_code: str
) -> None:
    result = await VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort({})
    ).search(npc_id=npc_id, intent=intent)
    assert result.hits == ()
    assert result.failure_code == failure_code


@pytest.mark.anyio
async def test_provider_and_vector_failures_are_safe(registry) -> None:
    failed = await VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort({}, fail=True)
    ).search(npc_id="npc_001", intent="拒绝")
    assert failed.failure_code == "embedding_error"

    vectors = _vectors(registry, "拒绝")
    vectors[VectorSpeechExampleRetriever.index_text(next(iter(registry.speech_examples.values())))] = [math.nan] * MEMORY_EMBEDDING_DIMENSIONS
    invalid = await VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort(vectors)
    ).search(npc_id="npc_001", intent="拒绝")
    assert invalid.failure_code == "invalid_vector"

    bad_batch = await VectorSpeechExampleRetriever(
        registry.speech_examples,
        FakeEmbeddingPort(_vectors(registry, "拒绝"), wrong_batch_size=True),
    ).search(npc_id="npc_001", intent="拒绝")
    assert bad_batch.failure_code == "invalid_vector"
