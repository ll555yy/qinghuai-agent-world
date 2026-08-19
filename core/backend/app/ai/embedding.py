"""Replaceable embedding boundary used by database memory retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

MEMORY_EMBEDDING_DIMENSIONS = 1024


class EmbeddingPort(Protocol):
    """Produce one vector without exposing a provider to the domain layer."""

    dimensions: int
    model_name: str

    async def embed(self, text: str) -> Sequence[float]:
        """Return the embedding for ``text`` or raise a provider-specific error."""


__all__ = ["EmbeddingPort", "MEMORY_EMBEDDING_DIMENSIONS"]
