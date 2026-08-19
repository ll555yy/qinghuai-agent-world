"""OpenAI-compatible Volcengine Ark text embedding adapter."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI

from .embedding import MEMORY_EMBEDDING_DIMENSIONS, EmbeddingPort

DEFAULT_ARK_EMBEDDING_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


@dataclass(frozen=True, slots=True)
class ArkEmbeddingSettings:
    """Configuration for the standard Ark ``/embeddings`` endpoint."""

    model: str
    base_url: str = field(
        default_factory=lambda: os.environ.get("ARK_EMBEDDING_BASE_URL", "").strip()
        or DEFAULT_ARK_EMBEDDING_BASE_URL
    )
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("ARK_API_KEY", "").strip() or None,
        repr=False,
    )
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("ARK_EMBEDDING_MODEL must be explicitly configured")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class ArkEmbeddingClient(EmbeddingPort):
    """Small provider adapter; failures are handled by the retrieval/indexing layer."""

    dimensions = MEMORY_EMBEDDING_DIMENSIONS

    def __init__(self, settings: ArkEmbeddingSettings, client: Any | None = None) -> None:
        self.settings = settings
        self.model_name = settings.model
        self._client = client
        self._last_metadata: dict[str, Any] = {}
        self._timeout = httpx.Timeout(settings.timeout_seconds)
        if self._client is None and settings.configured:
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=self._timeout,
                max_retries=0,
            )

    @property
    def configured(self) -> bool:
        return self.settings.configured and self._client is not None

    @property
    def last_metadata(self) -> dict[str, Any]:
        """Return safe metadata from the latest provider response."""

        return deepcopy(self._last_metadata)

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed one bounded batch using Ark's documented list input."""

        if not self.configured:
            raise RuntimeError("ARK_API_KEY is not configured for embeddings")
        assert self._client is not None
        inputs = [str(text) for text in texts]
        if not inputs or len(inputs) > 256 or any(not text.strip() for text in inputs):
            raise ValueError("embedding batch must contain 1..256 non-empty texts")
        response = await self._client.embeddings.create(
            model=self.model_name,
            input=inputs,
            encoding_format="float",
            timeout=self._timeout,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != len(inputs):
            raise RuntimeError("Ark returned no embedding data")
        ordered = sorted(data, key=lambda item: int(getattr(item, "index", 0)))
        dimensions = [
            len(vector)
            for item in ordered
            if isinstance((vector := getattr(item, "embedding", None)), list)
        ]
        usage = getattr(response, "usage", None)
        self._last_metadata = {
            "providerRequestId": getattr(response, "id", None),
            "actualModel": getattr(response, "model", None),
            "vectorCount": len(ordered),
            "actualDimensions": dimensions[0]
            if dimensions and len(set(dimensions)) == 1
            else None,
            "totalTokens": getattr(usage, "total_tokens", None),
        }
        results: list[list[float]] = []
        for item in ordered:
            vector = getattr(item, "embedding", None)
            if not isinstance(vector, list):
                raise RuntimeError("Ark returned an invalid embedding")
            result = [float(value) for value in vector]
            if len(result) != self.dimensions or not all(
                math.isfinite(value) for value in result
            ):
                raise ValueError(f"Ark embedding dimensions must be {self.dimensions}")
            results.append(result)
        return results

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = [
    "ArkEmbeddingClient",
    "ArkEmbeddingSettings",
    "DEFAULT_ARK_EMBEDDING_BASE_URL",
]
