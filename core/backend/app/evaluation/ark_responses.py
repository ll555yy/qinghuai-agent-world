"""Ark Responses API text port used only by the independent evaluator."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from ..ai.ark_client import ArkSettings
from ..ai.errors import AIError, AIErrorCode
from ..ai.models import TextGenerationRequest, TextGenerationResult, TokenUsage

logger = logging.getLogger(__name__)

PROVIDER_NAME = "volcengine_ark"


class ArkResponsesClient:
    """Minimal provider-neutral adapter over Ark's ``/responses`` API.

    Candidate generation deliberately remains on the production Chat
    Completions path.  This adapter exists because Agent Plan exposes newer
    evaluator models through Responses API even when Chat Completions does not
    return for the same model.  Provider-side storage is disabled explicitly.
    """

    provider = PROVIDER_NAME

    def __init__(
        self,
        settings: ArkSettings,
        client: Any | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        max_provider_retries: int = 1,
    ) -> None:
        if max_provider_retries < 0:
            raise ValueError("max_provider_retries must be non-negative")
        self.settings = settings
        self._configured = bool(settings.api_key)
        self._client = client
        self._response_schema = response_schema
        self._max_provider_retries = max_provider_retries
        self._provider_attempts = 0
        self._provider_retries = 0
        self._completed_requests = 0
        self._failed_requests = 0
        self._timeout = httpx.Timeout(
            settings.request_timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        if self._client is None and self._configured:
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=self._timeout,
                max_retries=0,
            )

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def native_structured_output(self) -> bool:
        """Whether the provider request already carries the full JSON Schema."""

        return self._response_schema is not None

    def status(self) -> dict[str, str | bool]:
        return {
            "configured": self.configured,
            "provider": self.provider,
            "model": self.settings.model,
            "baseUrlHost": self.settings.base_url_host,
            "apiMode": "responses",
        }

    def metrics_snapshot(self) -> dict[str, int]:
        return {
            "providerAttempts": self._provider_attempts,
            "providerRetries": self._provider_retries,
            "completedRequests": self._completed_requests,
            "failedRequests": self._failed_requests,
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        request_id = request.request_id or f"resp_{uuid.uuid4().hex}"
        if not self.configured or self._client is None:
            raise AIError(
                AIErrorCode.NOT_CONFIGURED,
                "ARK_API_KEY is not configured.",
                request_id=request_id,
            )
        options: dict[str, Any] = {
            "model": self.settings.model,
            "input": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "store": False,
            "extra_body": {"thinking": {"type": "disabled"}},
            "extra_headers": {"X-Client-Request-Id": request_id},
            "timeout": self._timeout,
        }
        if request.system_prompt:
            options["instructions"] = request.system_prompt
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["max_output_tokens"] = request.max_output_tokens
        if self._response_schema is not None:
            options["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "judge_score_v1",
                    "schema": self._response_schema,
                    "strict": True,
                }
            }

        started = time.perf_counter()
        for attempt in range(self._max_provider_retries + 1):
            try:
                self._provider_attempts += 1
                response = await self._client.responses.create(**options)
            except Exception as exc:
                error = self._map_provider_error(exc, request_id)
                if error.retryable and attempt < self._max_provider_retries:
                    self._provider_retries += 1
                    await asyncio.sleep(0)
                    continue
                self._failed_requests += 1
                self._log_failure(request_id, started, error)
                raise error from None
            try:
                result = self._parse_response(response, request_id)
            except AIError as error:
                self._failed_requests += 1
                self._log_failure(request_id, started, error)
                raise
            self._completed_requests += 1
            logger.info(
                "responses model request completed request_id=%s provider=%s "
                "model=%s duration_ms=%d total_tokens=%s",
                request_id,
                self.provider,
                self.settings.model,
                int((time.perf_counter() - started) * 1000),
                result.usage.total_tokens if result.usage else None,
            )
            return result
        raise AssertionError("unreachable")

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is None:
            return
        value = close()
        if hasattr(value, "__await__"):
            await value

    def _parse_response(self, response: Any, request_id: str) -> TextGenerationResult:
        token_usage = self._usage(response)
        status = getattr(response, "status", None)
        if isinstance(status, str) and status != "completed":
            incomplete = getattr(response, "incomplete_details", None)
            reason = getattr(incomplete, "reason", None)
            raise AIError(
                AIErrorCode.INVALID_RESPONSE,
                "Ark returned an incomplete Responses API result.",
                request_id=request_id,
                details={
                    "responseStatus": status,
                    "incompleteReason": reason if isinstance(reason, str) else None,
                    "usage": token_usage.model_dump() if token_usage else None,
                },
            )
        text = getattr(response, "output_text", None)
        if not isinstance(text, str) or not text.strip():
            text_parts: list[str] = []
            for item in getattr(response, "output", None) or []:
                for content in getattr(item, "content", None) or []:
                    value = getattr(content, "text", None)
                    if isinstance(value, str) and value.strip():
                        text_parts.append(value)
            text = "".join(text_parts)
        if not isinstance(text, str) or not text.strip():
            raise AIError(
                AIErrorCode.EMPTY_RESPONSE,
                "Ark returned an empty Responses API result.",
                request_id=request_id,
                details={"usage": token_usage.model_dump() if token_usage else None},
            )
        return TextGenerationResult(
            text=text,
            provider=self.provider,
            model=self.settings.model,
            usage=token_usage,
            provider_request_id=getattr(response, "id", None),
        )

    @staticmethod
    def _usage(response: Any) -> TokenUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None

        def token(name: str) -> int | None:
            value = (
                usage.get(name)
                if isinstance(usage, dict)
                else getattr(usage, name, None)
            )
            return value if isinstance(value, int) else None

        return TokenUsage(
            prompt_tokens=token("input_tokens"),
            completion_tokens=token("output_tokens"),
            total_tokens=token("total_tokens"),
        )

    @staticmethod
    def _map_provider_error(exc: Exception, request_id: str) -> AIError:
        if isinstance(exc, AuthenticationError):
            return AIError(
                AIErrorCode.AUTHENTICATION_FAILED,
                "Ark authentication failed.",
                request_id=request_id,
            )
        if isinstance(exc, RateLimitError):
            return AIError(
                AIErrorCode.RATE_LIMITED,
                "Ark rate limit or quota was reached.",
                request_id=request_id,
                retryable=True,
            )
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
            return AIError(
                AIErrorCode.TIMEOUT,
                "Ark request timed out.",
                request_id=request_id,
                retryable=True,
            )
        if isinstance(exc, APIConnectionError):
            return AIError(
                AIErrorCode.PROVIDER_UNAVAILABLE,
                "Ark provider is unavailable.",
                request_id=request_id,
                retryable=True,
            )
        if isinstance(exc, BadRequestError):
            return AIError(
                AIErrorCode.INVALID_REQUEST,
                "Ark rejected the request.",
                request_id=request_id,
            )
        if isinstance(exc, APIStatusError):
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403):
                return AIError(
                    AIErrorCode.AUTHENTICATION_FAILED,
                    "Ark authentication failed.",
                    request_id=request_id,
                )
            if status_code == 429:
                return AIError(
                    AIErrorCode.RATE_LIMITED,
                    "Ark rate limit or quota was reached.",
                    request_id=request_id,
                    retryable=True,
                )
            if isinstance(status_code, int) and status_code >= 500:
                return AIError(
                    AIErrorCode.PROVIDER_UNAVAILABLE,
                    "Ark provider is unavailable.",
                    request_id=request_id,
                    retryable=True,
                )
            return AIError(
                AIErrorCode.INVALID_RESPONSE,
                "Ark returned an invalid status.",
                request_id=request_id,
            )
        if isinstance(exc, APIError):
            return AIError(
                AIErrorCode.PROVIDER_UNAVAILABLE,
                "Ark provider is unavailable.",
                request_id=request_id,
                retryable=True,
            )
        return AIError(
            AIErrorCode.PROVIDER_UNAVAILABLE,
            "Ark provider is unavailable.",
            request_id=request_id,
        )

    def _log_failure(
        self,
        request_id: str,
        started: float,
        error: AIError,
    ) -> None:
        logger.warning(
            "responses model request failed request_id=%s provider=%s model=%s "
            "duration_ms=%d code=%s",
            request_id,
            self.provider,
            self.settings.model,
            int((time.perf_counter() - started) * 1000),
            error.code,
        )


__all__ = ["ArkResponsesClient", "PROVIDER_NAME"]
