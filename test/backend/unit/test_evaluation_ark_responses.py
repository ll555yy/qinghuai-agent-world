from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from core.backend.app.ai.ark_client import ArkSettings
from core.backend.app.ai.errors import AIError, AIErrorCode
from core.backend.app.ai.models import ChatMessage, TextGenerationRequest
from core.backend.app.evaluation.ark_responses import ArkResponsesClient


class _FakeResponses:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class _FakeOpenAI:
    def __init__(self, values: list[object]) -> None:
        self.responses = _FakeResponses(values)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _response(text: str = '{"ok":true}') -> SimpleNamespace:
    return SimpleNamespace(
        id="resp-test",
        status="completed",
        output_text=text,
        output=[],
        usage=SimpleNamespace(input_tokens=60, output_tokens=44, total_tokens=104),
    )


def _request() -> TextGenerationRequest:
    return TextGenerationRequest(
        system_prompt="trusted judge instructions",
        messages=[ChatMessage(role="user", content="untrusted candidate data")],
        temperature=0,
        max_output_tokens=64,
        request_id="judge-test",
    )


@pytest.mark.anyio
async def test_responses_adapter_maps_prompt_usage_and_disables_storage() -> None:
    fake = _FakeOpenAI([_response()])
    client = ArkResponsesClient(
        ArkSettings(api_key="test-key", model="doubao-seed-2.1-turbo"),
        client=fake,
        response_schema={"type": "object", "additionalProperties": False},
    )

    assert client.native_structured_output is True

    result = await client.generate(_request())
    await client.close()

    assert result.text == '{"ok":true}'
    assert result.usage is not None
    assert result.usage.prompt_tokens == 60
    assert result.usage.completion_tokens == 44
    assert result.usage.total_tokens == 104
    assert result.provider_request_id == "resp-test"
    call = fake.responses.calls[0]
    assert call["model"] == "doubao-seed-2.1-turbo"
    assert call["instructions"] == "trusted judge instructions"
    assert call["input"] == [
        {"role": "user", "content": "untrusted candidate data"}
    ]
    assert call["temperature"] == 0
    assert call["max_output_tokens"] == 64
    assert call["store"] is False
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "judge_score_v1",
            "schema": {"type": "object", "additionalProperties": False},
            "strict": True,
        }
    }
    assert fake.closed is True


@pytest.mark.anyio
async def test_responses_adapter_retries_one_transient_timeout() -> None:
    fake = _FakeOpenAI([httpx.TimeoutException("synthetic timeout"), _response()])
    client = ArkResponsesClient(
        ArkSettings(api_key="test-key", model="doubao-seed-2.1-turbo"),
        client=fake,
    )

    result = await client.generate(_request())

    assert result.text == '{"ok":true}'
    assert len(fake.responses.calls) == 2
    assert client.metrics_snapshot()["providerRetries"] == 1


@pytest.mark.anyio
async def test_responses_adapter_rejects_empty_completed_output() -> None:
    fake = _FakeOpenAI([_response("")])
    client = ArkResponsesClient(
        ArkSettings(api_key="test-key", model="doubao-seed-2.1-turbo"),
        client=fake,
    )

    with pytest.raises(AIError) as captured:
        await client.generate(_request())

    assert captured.value.code == AIErrorCode.EMPTY_RESPONSE
    assert captured.value.details["usage"]["total_tokens"] == 104


@pytest.mark.anyio
async def test_responses_adapter_preserves_safe_usage_for_incomplete_output() -> None:
    response = _response("")
    response.status = "incomplete"
    response.incomplete_details = SimpleNamespace(reason="max_output_tokens")
    fake = _FakeOpenAI([response])
    client = ArkResponsesClient(
        ArkSettings(api_key="test-key", model="doubao-seed-2.1-turbo"),
        client=fake,
    )

    with pytest.raises(AIError) as captured:
        await client.generate(_request())

    assert captured.value.code == AIErrorCode.INVALID_RESPONSE
    assert captured.value.details == {
        "responseStatus": "incomplete",
        "incompleteReason": "max_output_tokens",
        "usage": {
            "prompt_tokens": 60,
            "completion_tokens": 44,
            "total_tokens": 104,
        },
    }
