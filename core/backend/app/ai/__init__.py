"""Provider-independent text model port and the Volcengine Ark adapter."""

from .ark_client import ArkClient, ArkSettings
from .ark_embedding import ArkEmbeddingClient, ArkEmbeddingSettings
from .errors import AIError, AIErrorCode
from .models import TextGenerationRequest, TextGenerationResult, TokenUsage
from .port import TextModel

__all__ = [
    "AIError",
    "AIErrorCode",
    "ArkClient",
    "ArkSettings",
    "ArkEmbeddingClient",
    "ArkEmbeddingSettings",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TextModel",
    "TokenUsage",
]
