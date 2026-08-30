from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from core.backend.app.ai.decision_service import (
    PROTOCOL_RULES,
    DecisionService,
    extract_json_object,
)
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.ai.protocols import SpeechGeneration


def test_chat_rule_distinguishes_cache_use_from_required_recall() -> None:
    rule = PROTOCOL_RULES["ChatDecision"]
    assert "必须先返回 need_memory" in rule
    assert "已有足够内容" in rule
    assert "chapterEffects" in rule
    assert "evidenceMessageIds 留空" in rule
    assert "台词真实生成后绑定" in rule
    assert "memoryQuery.actorIds 只能引用输入 candidateActorIds" in rule
    assert "memoryQuery.goalIds 只能引用输入 candidateGoalIds" in rule
    assert "不得补充隐藏或猜测的 ID" in rule


def test_action_and_consolidation_rules_expose_required_semantics() -> None:
    assert "未解旧事" in PROTOCOL_RULES["DailyActionDecision"]
    assert "newChatAllowed=false" in PROTOCOL_RULES["DailyActionDecision"]
    assert "wait 不得携带" in PROTOCOL_RULES["DailyActionDecision"]
    assert "actorState=departed" in PROTOCOL_RULES["InvitationDecision"]
    assert "participantLimitReached=true" in PROTOCOL_RULES["InvitationDecision"]
    assert "具体问题" in PROTOCOL_RULES["SpeechGeneration"]
    assert "不能绕开问题" in PROTOCOL_RULES["SpeechGeneration"]
    assert "context.recentOwnMessages" in PROTOCOL_RULES["ChatDecision"]
    assert "必须选择 wait" in PROTOCOL_RULES["ChatDecision"]
    assert "context.recentOwnMessages" in PROTOCOL_RULES["SpeechGeneration"]
    assert "不得通过替换近义词" in PROTOCOL_RULES["SpeechGeneration"]
    assert "回应对象" in PROTOCOL_RULES["ChatDecision"]
    assert "禁止只写‘自然回应’" in PROTOCOL_RULES["ChatDecision"]
    assert "context.speechExamples" in PROTOCOL_RULES["SpeechGeneration"]
    assert "不得照抄" in PROTOCOL_RULES["SpeechGeneration"]
    assert "不能作为 Memory" in PROTOCOL_RULES["SpeechGeneration"]
    assert "addressedActorIds" in PROTOCOL_RULES["SpeechGeneration"]
    assert "activeParticipants" in PROTOCOL_RULES["SpeechGeneration"]
    consolidation = PROTOCOL_RULES["ExitConsolidation"]
    assert "chapterContext" in consolidation
    assert "必须" in consolidation


def test_speech_generation_parses_and_deduplicates_addressed_actor_ids() -> None:
    empty = SpeechGeneration.model_validate(
        {"text": "方案定稿前，我们还得征求周老板的意见。"}
    )
    single = SpeechGeneration.model_validate(
        {"text": "林老师，您说得对。", "addressedActorIds": ["npc_001"]}
    )
    multiple = SpeechGeneration.model_validate(
        {
            "text": "二位都说说。",
            "addressedActorIds": ["npc_001", "npc_002", "npc_001"],
        }
    )

    assert empty.addressed_actor_ids == []
    assert single.addressed_actor_ids == ["npc_001"]
    assert multiple.addressed_actor_ids == ["npc_001", "npc_002"]


def test_extract_json_object_accepts_exact_plain_object() -> None:
    assert extract_json_object('  {"action":"wait"}\n') == {"action": "wait"}


@pytest.mark.parametrize(
    "raw",
    (
        '```json\n{"action":"wait"}\n```',
        '说明：{"action":"wait"}',
        '{"action":"wait"} 额外说明',
        '[{"action":"wait"}]',
    ),
)
def test_extract_json_object_rejects_wrappers_trailing_text_and_arrays(raw: str) -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        extract_json_object(raw)


@pytest.mark.anyio
async def test_physical_model_requests_share_global_concurrency_limit() -> None:
    class BarrierModel:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0
            self.two_started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, _request: Any) -> TextGenerationResult:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if self.active == 2:
                self.two_started.set()
            try:
                await self.release.wait()
                return TextGenerationResult(
                    text='{"text":"并发回复"}',
                    provider="test",
                    model="barrier",
                )
            finally:
                self.active -= 1

    model = BarrierModel()
    decisions = DecisionService(model, max_concurrency=2)
    tasks = [asyncio.create_task(decisions.speech("prompt")) for _ in range(6)]
    await asyncio.wait_for(model.two_started.wait(), timeout=1)
    assert model.maximum == 2
    model.release.set()
    await asyncio.gather(*tasks)
    assert model.maximum == 2
