from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from benchmark.common.ark_budget import AFPBudgetGuard
from benchmark.common.models import TelemetryRecord
from core.backend.app.ai.models import ChatMessage, TextGenerationRequest


def _extract_json(text: str) -> Mapping[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("candidate did not return a JSON object") from None
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, Mapping):
        raise TypeError("candidate JSON must be an object")
    return value


class ArkBusinessDecider:
    """A0 action selector over the same public projection available to baselines."""

    def __init__(
        self,
        model: Any,
        telemetry: TelemetryRecord | None = None,
        budget_guard: AFPBudgetGuard | None = None,
    ) -> None:
        if getattr(model, "configured", False) is not True:
            raise ValueError("configured Ark candidate model is required")
        self.model = model
        self.telemetry = telemetry or TelemetryRecord()
        self.budget_guard = budget_guard

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        legal = []
        for action in state.get("legalActions", ()):
            if not isinstance(action, Mapping):
                continue
            legal.append(
                {
                    "id": str(action.get("id", action.get("actionId", ""))),
                    "kind": str(action.get("kind", "")),
                    "goalId": action.get("goalId"),
                    "targetActorId": action.get("targetActorId"),
                }
            )
        if not legal:
            raise ValueError("public state contains no legal actions")
        public = {
            key: value
            for key, value in state.items()
            if key
            not in {
                "fullPlan",
                "full_plan",
                "pendingActionIds",
                "pending_action_ids",
                "privateFacts",
                "coreSecrets",
                "memories",
                "retrieval",
            }
        }
        public["legalActions"] = legal
        request = TextGenerationRequest(
            system_prompt=(
                "你是青槐世界的行动规划器。只能根据公开状态，从 legalActions 选择一个动作。"
                "输出严格 JSON：{\"actionId\":\"...\"}，不得输出解释或不存在的动作。"
            ),
            messages=[
                ChatMessage(
                    role="user",
                    content=json.dumps(public, ensure_ascii=False, sort_keys=True),
                )
            ],
            temperature=0.1,
            max_output_tokens=80,
        )
        before_usage = self.budget_guard.check() if self.budget_guard is not None else None
        before = getattr(self.model, "metrics_snapshot", dict)()
        result = await self.model.generate(request)
        after = getattr(self.model, "metrics_snapshot", dict)()
        after_usage = self.budget_guard.check() if self.budget_guard is not None else None
        self.telemetry.candidate_calls += 1
        self.telemetry.candidate_physical_requests += int(after.get("providerAttempts", 0)) - int(
            before.get("providerAttempts", 0)
        )
        self.telemetry.candidate_retries += int(after.get("providerRetries", 0)) - int(
            before.get("providerRetries", 0)
        )
        if result.usage is not None:
            self.telemetry.prompt_tokens += int(result.usage.prompt_tokens or 0)
            self.telemetry.completion_tokens += int(result.usage.completion_tokens or 0)
        if before_usage is not None and after_usage is not None:
            self.telemetry.afp_used = (self.telemetry.afp_used or 0.0) + max(
                0.0, after_usage.used - before_usage.used
            )
        selected_id = str(_extract_json(result.text).get("actionId", ""))
        selected = next((action for action in legal if action["id"] == selected_id), None)
        if selected is None:
            raise ValueError("candidate selected an illegal action")
        return selected


__all__ = ["ArkBusinessDecider"]
