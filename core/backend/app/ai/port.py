"""The provider-independent asynchronous text model protocol."""

from __future__ import annotations

from typing import Protocol

from .models import TextGenerationRequest, TextGenerationResult


class TextModel(Protocol):
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Generate one text result without exposing provider SDK objects."""

