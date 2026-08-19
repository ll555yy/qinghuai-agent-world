from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, RateLimitError
from pydantic import ValidationError

from core.backend.app.ai.ark_client import (
    DEFAULT_ARK_BASE_URL,
    DEFAULT_ARK_MODEL,
    ArkClient,
    ArkSettings,
)
from core.backend.app.ai.errors import AIError, AIErrorCode
from core.backend.app.ai.models import ChatMessage, TextGenerationRequest


@dataclass
class FakeMessage:
    content: object


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    usage: object | None = None
    id: str = "provider-response-id"


class FakeCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        completions = FakeCompletions(responses)
        self.completions = completions
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def request() -> TextGenerationRequest:
    return TextGenerationRequest(
        system_prompt="system text",
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.1,
        max_output_tokens=12,
        request_id="test-request",
    )


def test_defaults_and_status_never_expose_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    settings = ArkSettings()
    assert settings.base_url == DEFAULT_ARK_BASE_URL
    assert settings.model == DEFAULT_ARK_MODEL
    client = ArkClient(settings=settings)
    status = client.status()
    assert status == {
        "configured": False,
        "provider": "volcengine_ark",
        "model": DEFAULT_ARK_MODEL,
        "baseUrlHost": "ark.cn-beijing.volces.com",
    }
    assert "key" not in str(status).lower()


def test_settings_snapshot_key_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "first-test-key")
    settings = ArkSettings()
    monkeypatch.delenv("ARK_API_KEY")
    assert settings.configured is True
    assert "first-test-key" not in repr(settings)


@pytest.mark.parametrize(
    "request_data",
    [
        {"messages": []},
        {"messages": [{"role": "tool", "content": "hello"}]},
        {"messages": [{"role": "user", "content": "hello"}], "temperature": 3},
        {"messages": [{"role": "user", "content": "hello"}], "max_output_tokens": 0},
    ],
)
def test_request_rejects_common_invalid_inputs(request_data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(request_data)


@pytest.mark.anyio
async def test_missing_key_fails_without_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    fake = FakeClient([])
    client = ArkClient(client=fake)
    with pytest.raises(AIError) as raised:
        await client.generate(request())
    assert raised.value.code == AIErrorCode.NOT_CONFIGURED
    assert fake.completions.calls == []


@pytest.mark.anyio
async def test_success_maps_text_usage_and_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    fake = FakeClient(
        [
            FakeResponse(
                choices=[FakeChoice(FakeMessage("hello back"))],
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        ]
    )
    client = ArkClient(client=fake)
    result = await client.generate(request())
    assert result.text == "hello back"
    assert result.provider == "volcengine_ark"
    assert result.model == DEFAULT_ARK_MODEL
    assert result.usage is not None
    assert result.usage.total_tokens == 5
    call = fake.completions.calls[0]
    assert call["model"] == DEFAULT_ARK_MODEL
    assert call["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "hello"},
    ]
    assert "test-only-key" not in repr(result)


@pytest.mark.anyio
async def test_authentication_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    response = httpx.Response(401, request=httpx.Request("POST", "https://ark.invalid"))
    fake = FakeClient([AuthenticationError("unauthorized", response=response, body={})])
    with pytest.raises(AIError) as raised:
        await ArkClient(client=fake).generate(request())
    assert raised.value.code == AIErrorCode.AUTHENTICATION_FAILED
    assert len(fake.completions.calls) == 1


@pytest.mark.anyio
async def test_rate_limit_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    response = httpx.Response(429, request=httpx.Request("POST", "https://ark.invalid"))
    fake = FakeClient(
        [
            RateLimitError("rate limited", response=response, body={}),
            FakeResponse(choices=[FakeChoice(FakeMessage("ok"))]),
        ]
    )
    result = await ArkClient(client=fake).generate(request())
    assert result.text == "ok"
    assert len(fake.completions.calls) == 2


@pytest.mark.anyio
async def test_timeout_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    fake = FakeClient(
        [
            httpx.TimeoutException("timed out"),
            FakeResponse(choices=[FakeChoice(FakeMessage("ok"))]),
        ]
    )
    result = await ArkClient(client=fake).generate(request())
    assert result.text == "ok"
    assert len(fake.completions.calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("response", [FakeResponse(choices=[]), FakeResponse(choices=[FakeChoice(FakeMessage(""))])])
async def test_empty_response_is_internal_error(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    with pytest.raises(AIError) as raised:
        await ArkClient(client=FakeClient([response])).generate(request())
    assert raised.value.code == AIErrorCode.EMPTY_RESPONSE


def test_api_status_is_read_only_and_does_not_call_model(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    response = client.get("/api/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["provider"] == "volcengine_ark"
    assert body["model"] == DEFAULT_ARK_MODEL
    assert body["baseUrlHost"] == "ark.cn-beijing.volces.com"
    assert "ARK_API_KEY" not in str(body)
