from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pytest
from core.backend.app.ai.errors import AIError, AIErrorCode
from core.backend.app.ai.models import TextGenerationResult, TokenUsage
from core.backend.scripts import check_ark_connection as checker


class FakeArkClient:
    def __init__(self, *, fail_protocol: str | None = None) -> None:
        self.requests: list[Any] = []
        self.responses: dict[str, list[str]] = defaultdict(list)
        self.fail_protocol = fail_protocol
        self.closed = False

    async def generate(self, request: Any) -> TextGenerationResult:
        self.requests.append(request)
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        if protocol == self.fail_protocol:
            raise AIError(AIErrorCode.RATE_LIMITED, "safe test error")
        if not self.responses[protocol]:
            values = {
                "DailyActionDecision": {"action": "wait"},
                "InvitationDecision": {"decision": "refuse"},
                "ChatDecision": {"result": "decided", "action": "wait"},
                "SpeechGeneration": {"text": "收到。"},
                "SegmentSummary": {},
                "ExitConsolidation": {},
            }
            self.responses[protocol].append(json.dumps(values[protocol], ensure_ascii=False))
        return TextGenerationResult(
            text=self.responses[protocol].pop(0),
            provider="fake",
            model="fake",
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )

    async def close(self) -> None:
        self.closed = True


def test_live_flag_is_explicit() -> None:
    assert checker._parse_args([]).live is False
    assert checker._parse_args(["--live"]).live is True


@pytest.mark.anyio
async def test_dry_run_never_constructs_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")

    def fail_constructor() -> None:
        raise AssertionError("dry-run must not construct or call ArkClient")

    monkeypatch.setattr(checker, "ArkClient", fail_constructor)
    report, exit_code = await checker.run_check(live=False)

    assert exit_code == 0
    assert report == {
        "tool": "ark_six_protocol_acceptance",
        "live": False,
        "requestSent": False,
        "success": True,
        "status": "dry_run",
        "checks": [],
    }


@pytest.mark.anyio
async def test_live_check_validates_six_protocols_and_only_reports_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    fake = FakeArkClient()
    monkeypatch.setattr(checker, "ArkClient", lambda: fake)
    fake.responses["SpeechGeneration"].append("not-json")

    report, exit_code = await checker.run_check(live=True)

    assert exit_code == 0
    assert report["success"] is True
    assert report["requestSent"] is True
    assert [item["protocol"] for item in report["checks"]] == [
        "DailyActionDecision",
        "InvitationDecision",
        "ChatDecision",
        "SpeechGeneration",
        "SegmentSummary",
        "ExitConsolidation",
    ]
    speech = next(item for item in report["checks"] if item["protocol"] == "SpeechGeneration")
    assert speech["formatRetries"] == 1
    assert speech["attemptCount"] == 2
    assert speech["usage"] == {
        "promptTokens": 4,
        "completionTokens": 6,
        "totalTokens": 10,
    }
    assert fake.closed is True
    assert all(item["errorCode"] is None for item in report["checks"])
    serialized = json.dumps(report, ensure_ascii=False)
    assert "test-only-key" not in serialized
    assert "not-json" not in serialized
    assert all("prompt" not in item for item in report["checks"])


@pytest.mark.anyio
async def test_live_check_reports_safe_error_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")
    fake = FakeArkClient(fail_protocol="SegmentSummary")
    monkeypatch.setattr(checker, "ArkClient", lambda: fake)

    report, exit_code = await checker.run_check(live=True)

    assert exit_code == 1
    assert report["success"] is False
    failed = next(item for item in report["checks"] if item["protocol"] == "SegmentSummary")
    assert failed["errorCode"] == "ai_rate_limited"
    assert fake.closed is True


@pytest.mark.anyio
async def test_live_check_without_key_sends_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    def fail_constructor() -> None:
        raise AssertionError("missing key must stop before client construction")

    monkeypatch.setattr(checker, "ArkClient", fail_constructor)
    report, exit_code = await checker.run_check(live=True)

    assert exit_code == 2
    assert report["status"] == "not_configured"
    assert report["requestSent"] is False
    assert report["checks"] == []


@pytest.mark.anyio
async def test_unexpected_provider_exception_stays_safe_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-only-key")

    class ExplodingClient:
        async def generate(self, _request: Any) -> TextGenerationResult:
            raise RuntimeError("must not be printed")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(checker, "ArkClient", ExplodingClient)
    report, exit_code = await checker.run_check(live=True)

    assert exit_code == 1
    assert report["success"] is False
    assert report["checks"][0]["errorCode"] == "unexpected_provider_error"
    assert "must not be printed" not in json.dumps(report)
