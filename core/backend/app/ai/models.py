"""Provider-neutral text generation contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatMessage(AIContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class TextGenerationRequest(AIContractModel):
    """The deliberately small request accepted by the model port."""

    system_prompt: str = ""
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, gt=0)
    request_id: str | None = None


class TokenUsage(AIContractModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class TextGenerationResult(AIContractModel):
    text: str
    provider: str
    model: str
    usage: TokenUsage | None = None
    provider_request_id: str | None = None
