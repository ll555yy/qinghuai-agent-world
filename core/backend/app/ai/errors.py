"""Internal, provider-neutral AI failure codes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class AIErrorCode(StrEnum):
    NOT_CONFIGURED = "ai_not_configured"
    AUTHENTICATION_FAILED = "ai_authentication_failed"
    RATE_LIMITED = "ai_rate_limited"
    TIMEOUT = "ai_timeout"
    PROVIDER_UNAVAILABLE = "ai_provider_unavailable"
    EMPTY_RESPONSE = "ai_empty_response"
    INVALID_RESPONSE = "ai_invalid_response"
    INVALID_REQUEST = "ai_invalid_request"


class AIError(Exception):
    """Safe error exposed to callers; it never carries a secret or prompt."""

    def __init__(
        self,
        code: AIErrorCode,
        message: str,
        *,
        request_id: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)

