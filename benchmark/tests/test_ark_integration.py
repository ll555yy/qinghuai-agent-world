from __future__ import annotations

import json

import pytest

from benchmark.integrations.ark import ArkBusinessDecider
from core.backend.app.ai.models import TextGenerationResult, TokenUsage


class FakeArk:
    configured = True

    def __init__(self) -> None:
        self.attempts = 0

    def metrics_snapshot(self):
        return {
            "providerAttempts": self.attempts,
            "providerRetries": 0,
        }

    async def generate(self, request):
        public = json.loads(request.messages[0].content)
        assert "fullPlan" not in public
        assert "effects" not in public["legalActions"][0]
        self.attempts += 1
        return TextGenerationResult(
            text='{"actionId":"speak"}',
            provider="fake",
            model="fake",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        )


@pytest.mark.anyio
async def test_ark_business_decider_only_sends_public_projection() -> None:
    decider = ArkBusinessDecider(FakeArk())
    action = await decider(
        {
            "fullPlan": ["speak"],
            "privateFacts": ["secret"],
            "legalActions": [
                {"id": "speak", "kind": "speak", "effects": {"secret": True}},
                {"id": "wait", "kind": "wait"},
            ],
        }
    )
    assert action["id"] == "speak"
    assert decider.telemetry.candidate_calls == 1
    assert decider.telemetry.candidate_physical_requests == 1
    assert decider.telemetry.total_tokens == 12
