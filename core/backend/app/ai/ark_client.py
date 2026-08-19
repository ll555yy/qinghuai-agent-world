"""OpenAI-compatible Volcengine Ark Agent Plan text adapter."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

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

from .errors import AIError, AIErrorCode
from .models import TextGenerationRequest, TextGenerationResult, TokenUsage

logger = logging.getLogger(__name__)

PROVIDER_NAME = "volcengine_ark"
DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_ARK_MODEL = "doubao-seed-2.0-lite"

@dataclass(frozen=True, slots=True)
class ArkSettings:
    """Configuration read from environment at client construction time."""

    base_url: str = field(default_factory=lambda: os.environ.get("ARK_BASE_URL", "").strip() or DEFAULT_ARK_BASE_URL)
    model: str = field(default_factory=lambda: os.environ.get("ARK_MODEL", "").strip() or DEFAULT_ARK_MODEL)
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("ARK_API_KEY", "").strip() or None,
        repr=False,
    )
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url_host(self) -> str:
        return urlparse(self.base_url).hostname or ""


class ArkClient:
    """A single, replaceable text model adapter for Ark Agent Plan."""

    provider = PROVIDER_NAME

    def __init__(self, settings: ArkSettings | None = None, client: Any | None = None) -> None:
        self.settings = settings or ArkSettings()
        self._api_key = self.settings.api_key
        self._configured = bool(self._api_key)
        self._client = client
        self._timeout = httpx.Timeout(
            self.settings.request_timeout_seconds,
            connect=self.settings.connect_timeout_seconds,
        )
        if self._client is None and self._configured:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self.settings.base_url,
                timeout=self._timeout,
                max_retries=0,
            )

    @property
    def configured(self) -> bool:
        return self._configured

    def status(self) -> dict[str, str | bool]:
        """Return safe configuration metadata; never return the API key."""

        return {
            "configured": self.configured,
            "provider": self.provider,
            "model": self.settings.model,
            "baseUrlHost": self.settings.base_url_host,
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        request_id = request.request_id or f"req_{uuid.uuid4().hex}"
        if not self.configured or self._client is None:
            raise AIError(
                AIErrorCode.NOT_CONFIGURED,
                "ARK_API_KEY is not configured.",
                request_id=request_id,
            )
        started = time.perf_counter()
        messages = self._messages(request)
        attempts = 2
        for attempt in range(attempts):
            try:
                response = await self._client.chat.completions.create(
                    model=self.settings.model,
                    messages=messages,
                    **self._generation_options(request),
                    extra_headers={"X-Client-Request-Id": request_id},
                    timeout=self._timeout,
                )
            except Exception as exc:
                error = self._map_provider_error(exc, request_id)
                if error.retryable and attempt == 0:
                    await asyncio.sleep(0)
                    continue
                self._log_failure(request_id, started, error)
                raise error from None

            try:
                result = self._parse_response(response, request_id)
            except AIError as error:
                self._log_failure(request_id, started, error)
                raise
            self._log_success(request_id, started, result)
            return result
        raise AssertionError("unreachable")

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    @staticmethod
    def _messages(request: TextGenerationRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend({"role": message.role, "content": message.content} for message in request.messages)
        return messages

    @staticmethod
    def _generation_options(request: TextGenerationRequest) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["max_tokens"] = request.max_output_tokens
        return options

    def _parse_response(self, response: Any, request_id: str) -> TextGenerationResult:
        choices = getattr(response, "choices", None)
        if choices is None:
            raise AIError(
                AIErrorCode.INVALID_RESPONSE,
                "Ark returned an invalid response.",
                request_id=request_id,
            )
        if not choices:
            raise AIError(
                AIErrorCode.EMPTY_RESPONSE,
                "Ark returned no choices.",
                request_id=request_id,
            )
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is None or (isinstance(content, str) and not content.strip()):
            raise AIError(
                AIErrorCode.EMPTY_RESPONSE,
                "Ark returned an empty response.",
                request_id=request_id,
            )
        if not isinstance(content, str):
            raise AIError(
                AIErrorCode.INVALID_RESPONSE,
                "Ark returned a non-text response.",
                request_id=request_id,
            )
        usage = self._usage(getattr(response, "usage", None))
        provider_request_id = (
            getattr(response, "_request_id", None)
            or getattr(response, "request_id", None)
            or getattr(response, "id", None)
        )
        return TextGenerationResult(
            text=content,
            provider=self.provider,
            model=self.settings.model,
            usage=usage,
            provider_request_id=provider_request_id,
        )

    @staticmethod
    def _usage(raw_usage: Any) -> TokenUsage | None:
        if raw_usage is None:
            return None

        def value(name: str) -> int | None:
            if isinstance(raw_usage, dict):
                raw = raw_usage.get(name)
            else:
                raw = getattr(raw_usage, name, None)
            return raw if isinstance(raw, int) else None

        return TokenUsage(
            prompt_tokens=value("prompt_tokens"),
            completion_tokens=value("completion_tokens"),
            total_tokens=value("total_tokens"),
        )

    @staticmethod
    def _map_provider_error(exc: Exception, request_id: str) -> AIError:
        if isinstance(exc, AuthenticationError):
            return AIError(AIErrorCode.AUTHENTICATION_FAILED, "Ark authentication failed.", request_id=request_id)
        if isinstance(exc, RateLimitError):
            return AIError(AIErrorCode.RATE_LIMITED, "Ark rate limit or quota was reached.", request_id=request_id, retryable=True)
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
            return AIError(AIErrorCode.TIMEOUT, "Ark request timed out.", request_id=request_id, retryable=True)
        if isinstance(exc, APIConnectionError):
            return AIError(AIErrorCode.PROVIDER_UNAVAILABLE, "Ark provider is unavailable.", request_id=request_id, retryable=True)
        if isinstance(exc, (BadRequestError,)):
            return AIError(AIErrorCode.INVALID_REQUEST, "Ark rejected the request.", request_id=request_id)
        if isinstance(exc, APIStatusError):
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403):
                return AIError(AIErrorCode.AUTHENTICATION_FAILED, "Ark authentication failed.", request_id=request_id)
            if status_code == 429:
                return AIError(AIErrorCode.RATE_LIMITED, "Ark rate limit or quota was reached.", request_id=request_id, retryable=True)
            if isinstance(status_code, int) and status_code >= 500:
                return AIError(AIErrorCode.PROVIDER_UNAVAILABLE, "Ark provider is unavailable.", request_id=request_id, retryable=True)
            return AIError(AIErrorCode.INVALID_RESPONSE, "Ark returned an invalid status.", request_id=request_id)
        if isinstance(exc, APIError):
            return AIError(AIErrorCode.PROVIDER_UNAVAILABLE, "Ark provider is unavailable.", request_id=request_id, retryable=True)
        return AIError(
            AIErrorCode.PROVIDER_UNAVAILABLE,
            "Ark provider is unavailable.",
            request_id=request_id,
        )

    def _log_success(self, request_id: str, started: float, result: TextGenerationResult) -> None:
        total_tokens = result.usage.total_tokens if result.usage else None
        logger.info(
            "text model request completed request_id=%s provider=%s model=%s duration_ms=%d total_tokens=%s",
            request_id,
            result.provider,
            result.model,
            int((time.perf_counter() - started) * 1000),
            total_tokens,
        )

    def _log_failure(self, request_id: str, started: float, error: AIError) -> None:
        logger.warning(
            "text model request failed request_id=%s provider=%s model=%s duration_ms=%d code=%s",
            request_id,
            self.provider,
            self.settings.model,
            int((time.perf_counter() - started) * 1000),
            error.code,
        )
