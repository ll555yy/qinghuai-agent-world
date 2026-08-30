"""Bounded, opt-in orchestration for semantic agent evaluations.

The semantic evaluator is intentionally separate from the production world
runner.  It accepts versioned case objects and small adapter objects, and it
never constructs a provider client in ``dry-run`` mode.  The implementation is
deliberately tolerant of the shared evaluation contracts: models may be
Pydantic models, dataclasses, or dictionaries while the parallel case/rule and
judge components are being integrated.

``offline`` is the normal development mode.  Its three local adapters are
deterministic and do not import or contact the production network clients.
``live`` is an explicit opt-in and requires an adapter to be supplied by the
caller; this module never reads an API key or silently constructs one.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from .models import CandidateObservation

EvaluationMode = Literal["dry-run", "offline", "live"]

DEFAULT_CANDIDATE_MODEL = "doubao-seed-2.0-lite"
DEFAULT_JUDGE_MODEL = "doubao-seed-2.1-turbo"
DEFAULT_TEXT_INPUT_CNY_PER_MILLION = 0.6
DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION = 3.6
DEFAULT_JUDGE_INPUT_CNY_PER_MILLION = 3.0
DEFAULT_JUDGE_OUTPUT_CNY_PER_MILLION = 15.0
DEFAULT_EMBEDDING_CNY_PER_MILLION = 0.7
DEFAULT_CANDIDATE_MAX_INPUT_TOKENS = 3_000
DEFAULT_CANDIDATE_MAX_OUTPUT_TOKENS = 900
DEFAULT_JUDGE_MAX_INPUT_TOKENS = 2_500
DEFAULT_JUDGE_MAX_OUTPUT_TOKENS = 384
DEFAULT_EMBEDDING_MAX_INPUT_TOKENS = 128
DEFAULT_TIMEOUT_SECONDS = 1_200.0

_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|database[_-]?url|password|secret)"
)
_URL = re.compile(r"(?i)(?:postgres(?:ql)?|https?|mysql)://[^\s\"']+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TOKEN = re.compile(r"\b(?:sk|ark|token)[_-][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    """Hard per-kind call and cost limits for one evaluation run."""

    max_candidate_calls: int = 10_000
    max_judge_calls: int = 10_000
    max_embedding_calls: int = 10_000
    max_cost_cny: float | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    candidate_repetitions: int | None = None
    judge_repetitions: int | None = None
    text_input_cny_per_million: float = DEFAULT_TEXT_INPUT_CNY_PER_MILLION
    text_output_cny_per_million: float = DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION
    judge_input_cny_per_million: float = DEFAULT_JUDGE_INPUT_CNY_PER_MILLION
    judge_output_cny_per_million: float = DEFAULT_JUDGE_OUTPUT_CNY_PER_MILLION
    embedding_cny_per_million: float = DEFAULT_EMBEDDING_CNY_PER_MILLION
    max_candidate_input_tokens: int = DEFAULT_CANDIDATE_MAX_INPUT_TOKENS
    max_candidate_output_tokens: int = DEFAULT_CANDIDATE_MAX_OUTPUT_TOKENS
    max_judge_input_tokens: int = DEFAULT_JUDGE_MAX_INPUT_TOKENS
    max_judge_output_tokens: int = DEFAULT_JUDGE_MAX_OUTPUT_TOKENS
    max_embedding_input_tokens: int = DEFAULT_EMBEDDING_MAX_INPUT_TOKENS

    def __post_init__(self) -> None:
        for name in (
            "max_candidate_calls",
            "max_judge_calls",
            "max_embedding_calls",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.max_cost_cny is not None and self.max_cost_cny < 0:
            raise ValueError("max_cost_cny must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        for name in (
            "text_input_cny_per_million",
            "text_output_cny_per_million",
            "judge_input_cny_per_million",
            "judge_output_cny_per_million",
            "embedding_cny_per_million",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "max_candidate_input_tokens",
            "max_candidate_output_tokens",
            "max_judge_input_tokens",
            "max_judge_output_tokens",
            "max_embedding_input_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("candidate_repetitions", "judge_repetitions"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be positive when provided")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> EvaluationBudget:
        """Build a budget from either snake_case or CLI-style camelCase keys."""

        if not values:
            return cls()
        aliases = {
            "candidateCalls": "max_candidate_calls",
            "maxCandidateCalls": "max_candidate_calls",
            "judgeCalls": "max_judge_calls",
            "maxJudgeCalls": "max_judge_calls",
            "embeddingCalls": "max_embedding_calls",
            "maxEmbeddingCalls": "max_embedding_calls",
            "maxCostCny": "max_cost_cny",
            "timeoutSeconds": "timeout_seconds",
            "candidateRepetitions": "candidate_repetitions",
            "judgeRepetitions": "judge_repetitions",
            "textInputCnyPerMillion": "text_input_cny_per_million",
            "textOutputCnyPerMillion": "text_output_cny_per_million",
            "judgeInputCnyPerMillion": "judge_input_cny_per_million",
            "judgeOutputCnyPerMillion": "judge_output_cny_per_million",
            "embeddingCnyPerMillion": "embedding_cny_per_million",
            "maxCandidateInputTokens": "max_candidate_input_tokens",
            "maxCandidateOutputTokens": "max_candidate_output_tokens",
            "maxJudgeInputTokens": "max_judge_input_tokens",
            "maxJudgeOutputTokens": "max_judge_output_tokens",
            "maxEmbeddingInputTokens": "max_embedding_input_tokens",
        }
        normalized = {aliases.get(key, key): value for key, value in values.items()}
        allowed = {field.name for field in EvaluationBudget.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in normalized.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxCandidateCalls": self.max_candidate_calls,
            "maxJudgeCalls": self.max_judge_calls,
            "maxEmbeddingCalls": self.max_embedding_calls,
            "maxCostCny": self.max_cost_cny,
            "timeoutSeconds": self.timeout_seconds,
            "candidateRepetitions": self.candidate_repetitions,
            "judgeRepetitions": self.judge_repetitions,
            "textInputCnyPerMillion": self.text_input_cny_per_million,
            "textOutputCnyPerMillion": self.text_output_cny_per_million,
            "judgeInputCnyPerMillion": self.judge_input_cny_per_million,
            "judgeOutputCnyPerMillion": self.judge_output_cny_per_million,
            "embeddingCnyPerMillion": self.embedding_cny_per_million,
            "maxCandidateInputTokens": self.max_candidate_input_tokens,
            "maxCandidateOutputTokens": self.max_candidate_output_tokens,
            "maxJudgeInputTokens": self.max_judge_input_tokens,
            "maxJudgeOutputTokens": self.max_judge_output_tokens,
            "maxEmbeddingInputTokens": self.max_embedding_input_tokens,
        }


@dataclass(slots=True)
class EvaluationExecution:
    """Content-free counters collected while the evaluator is running."""

    mode: EvaluationMode
    selected_cases: int = 0
    completed_cases: int = 0
    candidate_calls: int = 0
    judge_calls: int = 0
    embedding_calls: int = 0
    candidate_tokens: int = 0
    judge_tokens: int = 0
    embedding_tokens: int = 0
    estimated_cost_cny: float = 0.0
    complete: bool = True
    budget_exhausted: bool = False
    budget_reason: str | None = None
    timed_out: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    elapsed_ms: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        return self.candidate_calls + self.judge_calls + self.embedding_calls

    def to_dict(self, budget: EvaluationBudget) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selectedCases": self.selected_cases,
            "completedCases": self.completed_cases,
            "candidateCalls": self.candidate_calls,
            "judgeCalls": self.judge_calls,
            "embeddingCalls": self.embedding_calls,
            "candidateTokens": self.candidate_tokens,
            "judgeTokens": self.judge_tokens,
            "embeddingTokens": self.embedding_tokens,
            "totalCalls": self.total_calls,
            "estimatedCostCny": round(self.estimated_cost_cny, 6),
            "complete": self.complete,
            "budgetExhausted": self.budget_exhausted,
            "budgetReason": self.budget_reason,
            "timedOut": self.timed_out,
            "timeoutSeconds": self.timeout_seconds,
            "elapsedMs": self.elapsed_ms,
            "errors": sorted(set(self.errors)),
            "budget": budget.to_dict(),
        }


class EvaluationBudgetExceeded(RuntimeError):
    """Internal signal used to stop starting new provider calls."""


class EvaluationTimeout(TimeoutError):
    """Internal signal used to preserve a partial report on deadline."""


def _value(item: Any, *names: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        for name in names:
            if name in item:
                return item[name]
    else:
        for name in names:
            if hasattr(item, name):
                return getattr(item, name)
    return default


def _case_id(case: Any) -> str:
    return str(_value(case, "case_id", "caseId", "id", default=""))


def _case_category(case: Any) -> str:
    return str(_value(case, "category", default=""))


def _case_protocol(case: Any) -> str:
    return str(_value(case, "protocol", default=""))


def _case_context(case: Any) -> Mapping[str, Any]:
    context = _value(case, "input_context", "inputContext", default={})
    return context if isinstance(context, Mapping) else {}


def _as_plain(value: Any) -> Any:
    """Convert Pydantic/dataclass/enum values without retaining provider data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _as_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_as_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _as_plain(model_dump(mode="json"))
        except TypeError:
            return _as_plain(model_dump())
    as_dict = getattr(value, "_asdict", None)
    if callable(as_dict):
        return _as_plain(as_dict())
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return _as_plain(asdict(value))
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return value.value
    return str(value)


def _usage(result: Any) -> tuple[int, int, int]:
    usage = _value(result, "usage", default=None)
    if usage is None:
        usage = _value(result, "metrics", default=None)
    if usage is None and isinstance(result, Mapping):
        usage = result.get("tokenUsage")
    prompt = _value(usage, "prompt_tokens", "promptTokens", "input_tokens", "inputTokens", default=0)
    completion = _value(
        usage,
        "completion_tokens",
        "completionTokens",
        "output_tokens",
        "outputTokens",
        default=0,
    )
    total = _value(usage, "total_tokens", "totalTokens", default=None)
    prompt_i = int(prompt) if isinstance(prompt, int) and not isinstance(prompt, bool) else 0
    completion_i = (
        int(completion)
        if isinstance(completion, int) and not isinstance(completion, bool)
        else 0
    )
    total_i = int(total) if isinstance(total, int) and not isinstance(total, bool) else 0
    if total_i <= 0:
        total_i = prompt_i + completion_i
    return prompt_i, completion_i, total_i


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    value = _value(result, "text", "content", "output", "candidate_text", default="")
    return value if isinstance(value, str) else json.dumps(_as_plain(value), ensure_ascii=False)


def _result_structured(result: Any, text: str) -> Any:
    value = _value(result, "structured_output", "structuredOutput", "output_json", default=None)
    if value is not None:
        return _as_plain(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _safe_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only synthetic, non-secret context in provider requests."""

    output: dict[str, Any] = {}
    for key, value in context.items():
        key_text = str(key)
        if _SECRET_KEY.search(key_text):
            continue
        if isinstance(value, Mapping):
            output[key_text] = _safe_context(value)
        elif isinstance(value, (list, tuple)):
            output[key_text] = [
                _safe_context(item) if isinstance(item, Mapping) else item for item in value
            ]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            output[key_text] = value
    return output


@lru_cache(maxsize=5)
def _scenario_projection(npc_id: str) -> dict[str, Any]:
    """Load the same versioned Persona/Goal source used by production Runs."""

    from ..scenario.loader import ScenarioLoader

    scenario_dir = Path(__file__).resolve().parents[3] / "scenario"
    registry = ScenarioLoader(scenario_dir).load()
    persona = registry.npc_personas[npc_id]
    actor = registry.actor(npc_id)
    goals = [goal for goal in registry.goals.values() if goal.owner_npc_id == npc_id]
    return {
        "actor": {"actorId": npc_id, "name": actor.name if actor else npc_id},
        "persona": {
            "summary": persona.persona_summary,
            "traits": list(persona.traits),
            "values": list(persona.values),
            "socialStyle": {
                "initiative": persona.initiative,
                "directness": persona.directness,
                "openness": persona.openness,
                "conflictStyle": persona.conflict_style,
            },
            "speechStyle": {
                "tone": persona.speech_tone,
                "length": persona.speech_length,
                "habits": list(persona.speech_habits),
            },
            "boundaries": list(persona.boundaries),
        },
        "goals": [
            {
                "goalId": goal.goal_id,
                "description": goal.description,
                "status": goal.status,
                "targetActorIds": list(goal.target_actor_ids),
                "topicIds": list(goal.topic_ids),
            }
            for goal in goals
        ],
    }


def _candidate_prompt_payload(case: Any) -> dict[str, Any]:
    projection = _scenario_projection(str(_value(case, "npc_id", "npcId")))
    context = _safe_context(_case_context(case))
    world_time = context.get("world_time", context.get("worldTime"))
    minute: int | None = None
    if isinstance(world_time, str):
        match = re.search(r"\b(\d{1,2}):(\d{2})\b", world_time)
        if match is not None:
            hour, minute_part = int(match.group(1)), int(match.group(2))
            if hour <= 23 and minute_part <= 59:
                minute = hour * 60 + minute_part
    supplied_policy = context.get("time_policy", context.get("timePolicy"))
    if isinstance(supplied_policy, Mapping):
        time_policy = dict(supplied_policy)
    else:
        time_policy = {
            "remainingMinutes": max(0, 18 * 60 - minute) if minute is not None else 420,
            "newChatAllowed": minute is None or minute < 17 * 60,
            "closingSoon": minute is not None and minute >= 17 * 60,
        }
    time_policy.setdefault("dayEnd", "18:00")
    time_policy.setdefault("newChatCutoff", "17:00")
    return {
        "protocol": _case_protocol(case),
        "worldTime": world_time or {"day": 1, "hour": 10, "minute": 0},
        "timePolicy": time_policy,
        "candidateActorIds": _allowed_context(
            case,
            "allowed_actor_ids",
            "allowedActorIds",
            "visible_actor_ids",
            "visibleActorIds",
        ),
        "candidateGoalIds": _allowed_context(
            case,
            "allowed_goal_ids",
            "allowedGoalIds",
            "visible_goal_ids",
            "visibleGoalIds",
        ),
        "actorState": "departed" if context.get("departed") is True else "present",
        "participantLimitReached": context.get(
            "participant_limit_reached",
            context.get("participantLimitReached", False),
        ),
        **projection,
        "context": context,
    }


def _judge_case(case: Any) -> dict[str, Any]:
    payload = _as_plain(case)
    if not isinstance(payload, Mapping):
        payload = {}
    result = {str(key): value for key, value in payload.items()}
    context = dict(_safe_context(_case_context(case)))
    context["persona"] = _scenario_projection(
        str(_value(case, "npc_id", "npcId"))
    )["persona"]
    result["input_context"] = context
    return result


def _allowed_context(case: Any, *names: str) -> list[str]:
    context = _case_context(case)
    values: Any = None
    for name in names:
        if name in context:
            values = context[name]
            break
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return [str(item) for item in values if isinstance(item, (str, int))]


class FakeCandidate:
    """Deterministic local candidate; it intentionally makes no network calls."""

    configured = True
    model = "offline-fake-candidate"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, case: Any, run_index: int = 0) -> dict[str, Any]:
        self.calls += 1
        context = _case_context(case)
        configured = context.get("offline_candidate")
        if configured is None:
            configured = context.get("offlineCandidate")
        if isinstance(configured, Mapping):
            return {"output": _as_plain(configured), "text": json.dumps(configured, ensure_ascii=False)}
        if isinstance(configured, str):
            return {"text": configured}
        allowed = _value(case, "allowed_outcomes", "allowedOutcomes", default=[])
        outcome = str(allowed[0]) if isinstance(allowed, list) and allowed else "wait"
        protocol = _case_protocol(case)
        if protocol == "invitation":
            output: dict[str, Any] = {"decision": outcome if outcome in {"accept", "refuse"} else "refuse"}
        elif protocol == "chat_decision":
            output = {"result": "decided", "action": outcome}
        elif protocol == "daily_action":
            if outcome == "seek_chat":
                actors = _allowed_context(case, "allowed_actor_ids", "allowedActorIds")
                goals = _allowed_context(case, "allowed_goal_ids", "allowedGoalIds")
                output = {
                    "action": "seek_chat",
                    "goalId": goals[0] if goals else "goal_offline_synthetic",
                    "targetActorId": actors[0] if actors else "npc_001",
                    "intent": "offline deterministic candidate",
                }
            else:
                output = {"action": "wait"}
        elif protocol == "segment_summary":
            output = {
                "claims": ["离线摘要只保留当前可见事实。"],
                "commitments": [],
                "revealedFacts": [],
                "openQuestions": [],
                "actorIds": [],
                "topicHints": [],
            }
        elif protocol == "exit_consolidation":
            output = {
                "memories": [],
                "goalUpdates": [],
                "relationshipUpdates": [],
                "newShortGoals": [],
                "chapterEffects": [],
            }
        else:
            output = {"text": "收到，我会先确认公开条件。"}
        return {"output": output, "text": json.dumps(output, ensure_ascii=False, sort_keys=True)}


class FakeJudge:
    """Deterministic local judge used only when offline mode is selected."""

    configured = True
    model = "offline-fake-judge"

    def __init__(self) -> None:
        self.calls = 0

    async def score(self, case: Any, observation: Any, *, attempt: int = 0) -> dict[str, Any]:
        self.calls += 1
        return {
            "persona_consistency": 3,
            "context_faithfulness": 3,
            "response_relevance": 3,
            "naturalness": 3,
            "goal_progress": 3,
            "player_agency": 3,
            "contradiction_detected": False,
            "unsupported_claim_detected": False,
            "direct_question_answered": True,
            "major_issues": [],
            "evidence": {
                "persona_consistency": "offline deterministic judge",
                "context_faithfulness": "offline deterministic judge",
                "response_relevance": "offline deterministic judge",
                "naturalness": "offline deterministic judge",
                "goal_progress": "offline deterministic judge",
                "player_agency": "offline deterministic judge",
            },
            "confidence": "medium",
        }


class FakeEmbedding:
    """Deterministic embedding port with no dependency on a vector service."""

    configured = True
    model = "offline-fake-embedding"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [round((byte / 255.0) * 2 - 1, 6) for byte in digest[:8]]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


class _CapturingTextModel:
    """Capture token usage without changing the production protocol code."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.results: list[Any] = []

    @property
    def configured(self) -> bool:
        return bool(getattr(self.client, "configured", True))

    def reset(self) -> None:
        self.results.clear()

    async def generate(self, request: Any) -> Any:
        result = await self.client.generate(request)
        self.results.append(result)
        return result


class ArkCandidateAdapter:
    """Candidate adapter that reuses the production structured protocol port.

    The evaluator does not invent a second candidate prompt or temperature.
    ``DecisionService`` supplies the existing protocol rules, JSON schema,
    retry policy, and ``temperature=0.2``.  The only caller-owned value is the
    case's synthetic ``input_context`` JSON, which is passed as the protocol
    prompt.  This keeps live semantic measurements comparable to production
    behavior without importing RunService or mutating world state.
    """

    def __init__(self, client: Any) -> None:
        from ..ai.decision_service import DecisionService

        self.client = client
        self._meter = _CapturingTextModel(client)
        self.decisions = DecisionService(self._meter)
        self.model = getattr(client, "settings", None)

    async def generate(self, case: Any, run_index: int = 0) -> Any:
        self._meter.reset()
        metrics_before = (
            dict(self.client.metrics_snapshot())
            if callable(getattr(self.client, "metrics_snapshot", None))
            else {}
        )
        protocol = _case_protocol(case)
        prompt = json.dumps(
            _candidate_prompt_payload(case),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        methods = {
            "daily_action": self.decisions.daily_action,
            "invitation": self.decisions.invitation,
            "chat_decision": self.decisions.chat,
            "speech_generation": self.decisions.speech,
            "segment_summary": self.decisions.segment_summary,
            "exit_consolidation": self.decisions.exit_consolidation,
        }
        method = methods.get(protocol)
        if method is None:
            return {
                "text": "{}",
                "output": {},
                "failure_code": "unsupported_candidate_protocol",
            }
        output = await method(prompt)
        plain = output.model_dump(mode="json", by_alias=True)
        usages = [_usage(result) for result in self._meter.results]
        metrics_after = (
            dict(self.client.metrics_snapshot())
            if callable(getattr(self.client, "metrics_snapshot", None))
            else {}
        )
        provider_retries = max(
            0,
            int(metrics_after.get("providerRetries", 0))
            - int(metrics_before.get("providerRetries", 0)),
        )
        return {
            "text": json.dumps(plain, ensure_ascii=False, sort_keys=True),
            "output": plain,
            "usage": {
                "prompt_tokens": sum(item[0] for item in usages),
                "completion_tokens": sum(item[1] for item in usages),
                "total_tokens": sum(item[2] for item in usages),
            },
            "retries": provider_retries + max(0, len(self._meter.results) - 1),
            "provider_calls": max(
                len(self._meter.results),
                int(metrics_after.get("providerAttempts", 0))
                - int(metrics_before.get("providerAttempts", 0)),
            ),
            "failure_code": (
                "candidate_protocol_fallback"
                if self.decisions.last_failed_protocol is not None
                else None
            ),
        }


class ArkJudgeAdapter:
    """Compatibility wrapper around the independent JudgeAdapter contract.

    The implementation lives in ``evaluation.judge`` (owned by the Judge
    sub-agent); this wrapper intentionally adds no second prompt or schema.
    """

    def __init__(self, client: Any) -> None:
        from .judge import JudgeAdapter

        self._adapter = JudgeAdapter(model=client)

    async def score(self, case: Any, observation: Any, *, attempt: int = 0) -> Any:
        return await self._adapter.score(case, observation, duplicate=False)


def _method_for(adapter: Any, names: Sequence[str]) -> Any:
    for name in names:
        method = getattr(adapter, name, None)
        if callable(method):
            return method
    if callable(adapter):
        return adapter
    raise TypeError("evaluation adapter has no callable method")


def _first_parameter(method: Any) -> inspect.Parameter | None:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return None
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            return parameter
    return None


async def _invoke_candidate(adapter: Any, case: Any, run_index: int) -> Any:
    method = _method_for(adapter, ("generate", "run", "complete", "predict"))
    try:
        return await method(case, run_index)
    except TypeError:
        # A one-argument fake is common in unit tests.  Only retry when the
        # inspected signature confirms that the second argument is absent.
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            raise
        positional = [
            item
            for item in parameters.values()
            if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) <= 1:
            return await method(case)
        raise


async def _invoke_judge(adapter: Any, case: Any, observation: Any, attempt: int) -> Any:
    method = _method_for(adapter, ("score", "judge", "evaluate", "run"))
    first = _first_parameter(method)
    name = first.name.lower() if first is not None else ""
    parameters: Mapping[str, inspect.Parameter]
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {}
    # The shared JudgeAdapter exposes ``duplicate``/``repeat``.  We call it
    # once per observation here so runner-level budgets count every request;
    # its own format retry remains inside the adapter.  Small fakes often use
    # ``attempt`` instead, which is also supported without relying on a
    # TypeError raised from inside the adapter body.
    if "duplicate" in parameters:
        kwargs["duplicate"] = False
    elif "repeat" in parameters:
        kwargs["repeat"] = False
    elif "attempt" in parameters:
        kwargs["attempt"] = attempt
    if name in {"observation", "candidate", "candidate_observation"}:
        return await method(observation, **kwargs)
    positional = [
        item
        for item in parameters.values()
        if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) <= 1:
        return await method(observation, **kwargs)
    return await method(case, observation, **kwargs)


async def _invoke_embedding(adapter: Any, text: str) -> Any:
    method = _method_for(adapter, ("embed", "encode", "vectorize"))
    snapshot = getattr(adapter, "metrics_snapshot", None)
    before = dict(snapshot()) if callable(snapshot) else {}
    vector = await method(text)
    after = dict(snapshot()) if callable(snapshot) else {}
    total_tokens = max(
        0,
        int(after.get("totalTokens", 0)) - int(before.get("totalTokens", 0)),
    )
    return {"vectorDimensions": len(vector), "usage": {"total_tokens": total_tokens}}


def _build_observation(case: Any, run_index: int, result: Any, *, latency_ms: float, error: str | None = None) -> Any:
    text = _result_text(result)
    structured = _result_structured(result, text)
    prompt_tokens, completion_tokens, total_tokens = _usage(result)
    action = _value(structured, "action", "actual_action", "actualAction", default=None)
    target_actor = _value(structured, "target_actor_id", "targetActorId", default=None)
    goal_id = _value(structured, "goal_id", "goalId", default=None)
    actor_ids = _value(structured, "actor_ids", "actorIds", default=[])
    goal_ids = _value(structured, "goal_ids", "goalIds", default=[])
    evidence_ids = _value(
        structured,
        "evidence",
        "evidence_message_ids",
        "evidenceMessageIds",
        default=[],
    )
    memory_ids = _value(
        structured,
        "retrieved_memory_ids",
        "retrievedMemoryIds",
        "memory_ids",
        "memoryIds",
        default=[],
    )
    memory_query = _value(structured, "memory_query", "memoryQuery", default={})
    if not isinstance(memory_query, Mapping):
        memory_query = {}
    query_actor_ids = _value(memory_query, "actor_ids", "actorIds", default=[])
    query_goal_ids = _value(memory_query, "goal_ids", "goalIds", default=[])
    query_topic_hints = _value(memory_query, "topic_hints", "topicHints", default=[])
    query_text = _value(memory_query, "query_text", "queryText", default="")
    vector_hits = _value(structured, "vector_hits", "vectorHits", default=0)
    graph_hits = _value(structured, "graph_hits", "graphHits", default=0)
    if target_actor is not None:
        actor_ids = [*actor_ids, target_actor] if isinstance(actor_ids, (list, tuple)) else [target_actor]
    if goal_id is not None:
        goal_ids = [*goal_ids, goal_id] if isinstance(goal_ids, (list, tuple)) else [goal_id]
    context_allowed_memory = _allowed_context(
        case,
        "allowed_memory_ids",
        "allowedMemoryIds",
        "owner_memory_ids",
        "ownerMemoryIds",
    )
    owner_memory = _allowed_context(case, "owner_memory_ids", "ownerMemoryIds")
    payload = {
        "case_id": _case_id(case),
        "run_index": run_index,
        "protocol": _case_protocol(case),
        "candidate_text": text,
        "structured_output": structured,
        "schema_valid": structured is not None and error is None,
        "actual_action": str(action) if action is not None else None,
        "goal_id": str(goal_id) if goal_id is not None else None,
        "target_actor_id": str(target_actor) if target_actor is not None else None,
        "actor_ids": [str(item) for item in actor_ids] if isinstance(actor_ids, (list, tuple)) else [],
        "goal_ids": [str(item) for item in goal_ids] if isinstance(goal_ids, (list, tuple)) else [],
        "evidence_message_ids": [str(item) for item in evidence_ids] if isinstance(evidence_ids, (list, tuple)) else [],
        "retrieved_memory_ids": [str(item) for item in memory_ids] if isinstance(memory_ids, (list, tuple)) else [],
        "retrieval_source": _value(
            _case_context(case), "retrieval_source", "retrievalSource", default="fixture"
        ),
        "memory_query_text": str(query_text) if isinstance(query_text, str) else "",
        "memory_query_actor_ids": [str(item) for item in query_actor_ids]
        if isinstance(query_actor_ids, (list, tuple))
        else [],
        "memory_query_goal_ids": [str(item) for item in query_goal_ids]
        if isinstance(query_goal_ids, (list, tuple))
        else [],
        "memory_query_topic_hints": [str(item) for item in query_topic_hints]
        if isinstance(query_topic_hints, (list, tuple))
        else [],
        "vector_hits": int(vector_hits) if isinstance(vector_hits, int) else 0,
        "graph_hits": int(graph_hits) if isinstance(graph_hits, int) else 0,
        "allowed_actor_ids": _allowed_context(case, "allowed_actor_ids", "allowedActorIds", "allowed_actors"),
        "allowed_goal_ids": _allowed_context(case, "allowed_goal_ids", "allowedGoalIds", "allowed_goals"),
        "allowed_evidence_message_ids": _allowed_context(
            case,
            "allowed_evidence_message_ids",
            "allowedEvidenceMessageIds",
            "allowed_evidence",
        ),
        "allowed_memory_ids": context_allowed_memory,
        "owner_memory_ids": owner_memory,
        "latency_ms": round(latency_ms, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_cny": round(
            (
                prompt_tokens * DEFAULT_TEXT_INPUT_CNY_PER_MILLION
                + completion_tokens * DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION
            )
            / 1_000_000,
            6,
        ),
        "retries": int(_value(result, "retries", "retry_count", "retryCount", default=0) or 0),
        "failure_code": error or _value(result, "failure_code", "failureCode", default=None),
        "system_blocked": _value(result, "system_blocked", "systemBlocked", default=None),
        "end_to_end_safety_failure": _value(
            result,
            "end_to_end_safety_failure",
            "endToEndSafetyFailure",
            default=None,
        ),
    }
    return CandidateObservation.model_validate(payload)


def _fallback_rule_score(case: Any, observation: Any) -> dict[str, Any]:
    failure = _value(observation, "failure_code", "failureCode", default=None)
    text = _result_text(observation)
    forbidden = _value(case, "forbidden_signals", "forbiddenSignals", default=[])
    leaked = [str(item) for item in forbidden if str(item) and str(item) in text]
    failures: list[str] = []
    if failure:
        failures.append(str(failure))
    if leaked:
        failures.append("forbidden_signal_leak")
    return {
        "hard_failure": bool(failures),
        "failures": sorted(set(failures)),
        "schema_valid": bool(text.strip()),
        "action_valid": True,
        "id_valid": True,
        "evidence_valid": True,
        "owner_leak_count": 0,
        "canary_leak_count": len(leaked),
        "internal_field_leak_count": 0,
        "precision_at_k": None,
        "recall_at_k": None,
        "mrr": None,
        "direct_question_pass": None,
        "repetition": False,
        "prompt_tokens": _value(observation, "prompt_tokens", "promptTokens", default=0),
        "completion_tokens": _value(observation, "completion_tokens", "completionTokens", default=0),
        "total_tokens": _value(observation, "total_tokens", "totalTokens", default=0),
        "latency_ms": _value(observation, "latency_ms", "latencyMs", default=0.0),
        "retries": _value(observation, "retries", default=0),
        "estimated_cost_cny": 0.0,
    }


def _score_rule(
    scorer: Any,
    case: Any,
    observation: Any,
    *,
    previous_observations: Iterable[Any] = (),
) -> Any:
    if scorer is None:
        return _fallback_rule_score(case, observation)
    method = _method_for(scorer, ("score", "score_case", "evaluate", "run"))
    first = _first_parameter(method)
    name = first.name.lower() if first is not None else ""
    if name in {"observation", "candidate", "candidate_observation"}:
        return method(observation, case)
    parameters: Mapping[str, inspect.Parameter]
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {}
    if "previous_observations" in parameters:
        kwargs["previous_observations"] = previous_observations
    if "previous_candidate_texts" in parameters:
        kwargs["previous_candidate_texts"] = [
            _result_text(item) for item in previous_observations
        ]
    positional = [
        item
        for item in parameters.values()
        if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) <= 1:
        return method(observation, **kwargs)
    return method(case, observation, **kwargs)


def _normalize_judge(score: Any) -> dict[str, Any]:
    plain = _as_plain(score)
    if not isinstance(plain, Mapping):
        raise ValueError("judge score must be an object")
    # B's independent JudgeAdapter returns JudgeEvaluation, whose validated
    # score lives under ``score``.  Keep only that strict score here; the
    # runner owns repeat/disagreement accounting and report projection.
    outer = dict(plain)
    nested = outer.get("score")
    if isinstance(nested, Mapping):
        plain = nested
    elif "score" in outer and nested is None:
        # A strict Judge deliberately returns no score after both schema
        # attempts fail.  Preserve that validated failure envelope so the
        # report can count it and route it to human review; never manufacture
        # six dimension values merely to keep aggregation running.
        plain = {}
    result = {str(key): value for key, value in plain.items()}
    metrics = outer.get("metrics")
    if isinstance(metrics, Mapping):
        result["judgeMetrics"] = {str(key): value for key, value in metrics.items()}
    review_reasons = outer.get("review_reasons", outer.get("reviewReasons"))
    if isinstance(review_reasons, list):
        result["adapterReviewReasons"] = [str(item) for item in review_reasons]
    error_code = outer.get("error_code", outer.get("errorCode"))
    if error_code is not None:
        result["judgeErrorCode"] = str(error_code)
    dimensions = (
        "persona_consistency",
        "context_faithfulness",
        "response_relevance",
        "naturalness",
        "goal_progress",
        "player_agency",
    )
    values: list[float] = []
    for name in dimensions:
        value = result.get(name, result.get(_camel(name)))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[name] = max(1, min(5, float(value)))
            values.append(float(result[name]))
    if len(values) != len(dimensions):
        if result.get("judgeErrorCode"):
            return result
        raise ValueError("judge score has incomplete dimensions")
    total = round(sum(values) / len(values), 6)
    result["total_score"] = total
    result["totalScore"] = total
    return result


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _judge_reasons(first: Mapping[str, Any], second: Mapping[str, Any] | None) -> list[str]:
    reasons: set[str] = set()
    adapter_reasons = first.get("adapterReviewReasons", [])
    if isinstance(adapter_reasons, list):
        reasons.update(f"judge_{str(item)}" for item in adapter_reasons)
    if first.get("judgeErrorCode"):
        reasons.add("judge_schema_failure")
    if str(first.get("confidence", "")).lower() == "low":
        reasons.add("judge_low_confidence")
    if first.get("contradiction_detected", first.get("contradictionDetected", False)):
        reasons.add("judge_contradiction")
    if first.get("unsupported_claim_detected", first.get("unsupportedClaimDetected", False)):
        reasons.add("judge_unsupported_claim")
    issues = first.get("major_issues", first.get("majorIssues", []))
    material_issues = (
        [item for item in issues if str(item).strip().lower() != "none"]
        if isinstance(issues, (list, tuple))
        else []
    )
    if material_issues:
        reasons.add("judge_major_issue")
    if second is not None:
        dimensions = (
            "persona_consistency",
            "context_faithfulness",
            "response_relevance",
            "naturalness",
            "goal_progress",
            "player_agency",
        )
        for name in dimensions:
            left = first.get(name, first.get(_camel(name)))
            right = second.get(name, second.get(_camel(name)))
            if isinstance(left, (int, float)) and isinstance(right, (int, float)) and abs(left - right) > 1:
                reasons.add("judge_disagreement")
                break
    return sorted(reasons)


def _fraction_sample(ordinal: int, rate: float) -> bool:
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return math.ceil((ordinal + 1) * rate) > math.ceil(ordinal * rate)


def _hard_failure(score: Any) -> bool:
    return bool(_value(score, "hard_failure", "hardFailure", default=False))


def _failures(score: Any) -> list[str]:
    value = _value(score, "failures", default=[])
    return sorted({str(item) for item in value}) if isinstance(value, (list, tuple, set)) else []


def _is_async(value: Any) -> bool:
    return inspect.isawaitable(value)


async def _maybe_await(value: Any) -> Any:
    return await value if _is_async(value) else value


class EvaluationRunner:
    """Run semantic cases with explicit mode, filters, budgets, and deadline."""

    def __init__(
        self,
        cases: Iterable[Any],
        *,
        mode: EvaluationMode = "dry-run",
        candidate: Any | None = None,
        judge: Any | None = None,
        embedding: Any | None = None,
        rule_scorer: Any | None = None,
        budget: EvaluationBudget | Mapping[str, Any] | None = None,
        case_ids: Iterable[str] | None = None,
        categories: Iterable[str] | None = None,
        enable_judge: bool = False,
        judge_sample_rate: float = 1.0,
        judge_repeat_sample_rate: float = 0.2,
        candidate_repetitions: int | None = None,
        judge_repetitions: int | None = None,
        postgres_available: bool = False,
    ) -> None:
        if mode not in {"dry-run", "offline", "live"}:
            raise ValueError("mode must be dry-run, offline, or live")
        self.mode = mode
        self._all_cases = list(cases)
        self.case_ids = {str(item) for item in case_ids} if case_ids else None
        self.categories = {str(item) for item in categories} if categories else None
        self.enable_judge = enable_judge
        if not 0 <= judge_sample_rate <= 1:
            raise ValueError("judge_sample_rate must be between 0 and 1")
        if not 0 <= judge_repeat_sample_rate <= 1:
            raise ValueError("judge_repeat_sample_rate must be between 0 and 1")
        self.judge_sample_rate = judge_sample_rate
        self.judge_repeat_sample_rate = judge_repeat_sample_rate
        self.postgres_available = postgres_available
        self.budget = budget if isinstance(budget, EvaluationBudget) else EvaluationBudget.from_mapping(budget)
        self.candidate_repetitions = (
            candidate_repetitions
            if candidate_repetitions is not None
            else self.budget.candidate_repetitions
            or (2 if mode == "live" else 1)
        )
        self.judge_repetitions = (
            judge_repetitions
            if judge_repetitions is not None
            else self.budget.judge_repetitions
            or 1
        )
        if self.candidate_repetitions < 1 or self.judge_repetitions < 1:
            raise ValueError("repetition counts must be positive")
        if mode == "live" and candidate is None:
            raise ValueError("live mode requires an explicit candidate adapter")
        if enable_judge and mode == "live" and judge is None:
            raise ValueError("--enable-judge with live mode requires an explicit judge adapter")
        if mode == "dry-run":
            # Supplied adapters are never touched in this mode.  Keeping this
            # assertion in the constructor makes accidental provider creation
            # visible to callers while still allowing dependency injection.
            self.candidate = None
            self.judge = None
            self.embedding = None
        elif mode == "offline":
            self.candidate = candidate if candidate is not None else FakeCandidate()
            self.judge = judge if judge is not None else (FakeJudge() if enable_judge else None)
            self.embedding = embedding if embedding is not None else FakeEmbedding()
        else:
            self.candidate = candidate
            self.judge = judge
            self.embedding = embedding
            needs_embedding = any(
                (
                    bool(
                        _value(
                            case,
                            "requires_live_embedding",
                            "requiresLiveEmbedding",
                            default=False,
                        )
                    )
                    or _case_protocol(case) == "memory_retrieval"
                )
                and _case_context(case).get(
                    "embeddingAvailable",
                    _case_context(case).get("embedding_available", True),
                )
                is not False
                for case in self.selected_cases
            )
            if needs_embedding and self.embedding is None:
                raise ValueError(
                    "live embedding cases require an explicit embedding adapter"
                )
        if rule_scorer is None:
            try:
                from .rule_scorer import RuleScorer

                rule_scorer = RuleScorer()
            except (ImportError, TypeError):
                rule_scorer = None
        self.rule_scorer = rule_scorer

    @property
    def selected_cases(self) -> list[Any]:
        selected = []
        for case in self._all_cases:
            if self.case_ids is not None and _case_id(case) not in self.case_ids:
                continue
            if self.categories is not None and _case_category(case) not in self.categories:
                continue
            selected.append(case)
        return sorted(selected, key=lambda item: (_case_id(item), _case_category(item), _case_protocol(item)))

    def planned_calls(self) -> dict[str, int | float]:
        """Return calls that would be attempted, before mode/requirements skip."""

        cases = self.selected_cases
        eligible = [case for case in cases if self._requirements_ok(case) is None]
        candidate_cases = [case for case in eligible if _case_protocol(case) != "memory_retrieval"]
        candidate = len(candidate_cases) * self.candidate_repetitions
        observation_count = candidate
        if self.enable_judge:
            primary = math.ceil(observation_count * self.judge_sample_rate)
            judge = primary * self.judge_repetitions
            if self.mode in {"dry-run", "live"} and self.judge_repetitions == 1:
                judge += math.ceil(primary * self.judge_repeat_sample_rate)
        else:
            judge = 0
        embedding = sum(
            1
            for case in eligible
            if (
                _value(case, "requires_live_embedding", "requiresLiveEmbedding", default=False)
                or _case_protocol(case) == "memory_retrieval"
            )
            and _case_context(case).get(
                "embeddingAvailable",
                _case_context(case).get("embedding_available", True),
            )
            is not False
        )
        text_cost = candidate * (
            self.budget.max_candidate_input_tokens * self.budget.text_input_cny_per_million
            + self.budget.max_candidate_output_tokens * self.budget.text_output_cny_per_million
        ) / 1_000_000
        judge_cost = judge * (
            self.budget.max_judge_input_tokens * self.budget.judge_input_cny_per_million
            + self.budget.max_judge_output_tokens * self.budget.judge_output_cny_per_million
        ) / 1_000_000
        embedding_cost = (
            embedding
            * self.budget.max_embedding_input_tokens
            * self.budget.embedding_cny_per_million
            / 1_000_000
        )
        return {
            "cases": len(cases),
            "candidate": candidate,
            "judge": judge,
            "embedding": embedding,
            "worstRequests": candidate + judge + embedding,
            "worstCaseEstimatedCostCny": round(
                text_cost + judge_cost + embedding_cost,
                6,
            ),
        }

    def _requirements_ok(self, case: Any) -> str | None:
        if bool(_value(case, "requires_postgres", "requiresPostgres", default=False)) and not self.postgres_available:
            return "postgres_required"
        if bool(_value(case, "requires_live_candidate", "requiresLiveCandidate", default=False)) and self.mode != "live":
            return "live_candidate_required"
        if bool(_value(case, "requires_live_embedding", "requiresLiveEmbedding", default=False)) and self.mode != "live":
            return "live_embedding_required"
        return None

    def _reserved_cost(self, kind: str) -> float:
        if kind == "candidate":
            return (
                self.budget.max_candidate_input_tokens * self.budget.text_input_cny_per_million
                + self.budget.max_candidate_output_tokens * self.budget.text_output_cny_per_million
            ) / 1_000_000
        if kind == "judge":
            return (
                self.budget.max_judge_input_tokens * self.budget.judge_input_cny_per_million
                + self.budget.max_judge_output_tokens * self.budget.judge_output_cny_per_million
            ) / 1_000_000
        return (
            self.budget.max_embedding_input_tokens
            * self.budget.embedding_cny_per_million
            / 1_000_000
        )

    def _check_budget(self, execution: EvaluationExecution, kind: str) -> None:
        current = {
            "candidate": execution.candidate_calls,
            "judge": execution.judge_calls,
            "embedding": execution.embedding_calls,
        }[kind]
        maximum = {
            "candidate": self.budget.max_candidate_calls,
            "judge": self.budget.max_judge_calls,
            "embedding": self.budget.max_embedding_calls,
        }[kind]
        if current >= maximum:
            raise EvaluationBudgetExceeded(f"{kind}_calls")
        if self.budget.max_cost_cny is not None:
            reserved = self._reserved_cost(kind)
            if execution.estimated_cost_cny + reserved > self.budget.max_cost_cny:
                raise EvaluationBudgetExceeded("cost_cny")

    def _record_usage(self, execution: EvaluationExecution, kind: str, result: Any) -> None:
        prompt, completion, total = _usage(result)
        if kind == "candidate":
            execution.candidate_tokens += total
        elif kind == "judge":
            execution.judge_tokens += total
        else:
            execution.embedding_tokens += total
        if kind == "embedding":
            execution.estimated_cost_cny += total * self.budget.embedding_cny_per_million / 1_000_000
        elif kind == "candidate":
            execution.estimated_cost_cny += (
                prompt * self.budget.text_input_cny_per_million
                + completion * self.budget.text_output_cny_per_million
            ) / 1_000_000
        else:
            execution.estimated_cost_cny += (
                prompt * self.budget.judge_input_cny_per_million
                + completion * self.budget.judge_output_cny_per_million
            ) / 1_000_000

    async def _call(
        self,
        adapter: Any,
        kind: str,
        case: Any,
        execution: EvaluationExecution,
        deadline: float,
        *,
        run_index: int = 0,
        observation: Any | None = None,
        attempt: int = 0,
    ) -> Any:
        self._check_budget(execution, kind)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise EvaluationTimeout("evaluation timeout")
        if kind == "candidate":
            execution.candidate_calls += 1
            action = _invoke_candidate(adapter, case, run_index)
        elif kind == "judge":
            execution.judge_calls += 1
            action = _invoke_judge(adapter, _judge_case(case), observation, attempt)
        else:
            execution.embedding_calls += 1
            context = _case_context(case)
            query_text = context.get("queryText", context.get("query_text"))
            embedding_text = (
                query_text.strip()
                if isinstance(query_text, str) and query_text.strip()
                else f"semantic-evaluation:{_case_id(case)}"
            )
            action = _invoke_embedding(
                adapter,
                embedding_text,
            )
        try:
            result = await asyncio.wait_for(_maybe_await(action), timeout=remaining)
        except TimeoutError as exc:
            raise EvaluationTimeout("evaluation timeout") from exc
        if kind == "candidate":
            actual_calls = int(_value(result, "provider_calls", "providerCalls", default=1) or 1)
            execution.candidate_calls += max(0, actual_calls - 1)
        elif kind == "judge":
            metrics = _value(result, "metrics", default=None)
            logical_calls = int(_value(metrics, "calls", default=1) or 1)
            provider_retries = int(
                _value(metrics, "provider_retries", "providerRetries", default=0) or 0
            )
            actual_calls = logical_calls + max(0, provider_retries)
            execution.judge_calls += max(0, actual_calls - 1)
        self._record_usage(execution, kind, result)
        maximum = {
            "candidate": self.budget.max_candidate_calls,
            "judge": self.budget.max_judge_calls,
            "embedding": self.budget.max_embedding_calls,
        }[kind]
        current = {
            "candidate": execution.candidate_calls,
            "judge": execution.judge_calls,
            "embedding": execution.embedding_calls,
        }[kind]
        if current > maximum:
            execution.budget_exhausted = True
            execution.budget_reason = f"{kind}_calls"
        if self.budget.max_cost_cny is not None and execution.estimated_cost_cny >= self.budget.max_cost_cny:
            execution.budget_exhausted = True
            execution.budget_reason = "cost_cny"
        return result

    async def run(self) -> dict[str, Any]:
        """Execute selected cases and return a stable safe report dictionary."""

        from .report import build_report

        started = time.perf_counter()
        cases = self.selected_cases
        execution = EvaluationExecution(
            mode=self.mode,
            selected_cases=len(cases),
            timeout_seconds=self.budget.timeout_seconds,
        )
        case_results: list[dict[str, Any]] = []
        if self.mode == "dry-run":
            execution.complete = True
            execution.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return build_report(
                cases=case_results,
                execution=execution,
                budget=self.budget,
                selected_cases=cases,
                enable_judge=self.enable_judge,
                planned_calls=self.planned_calls(),
            )

        deadline = started + self.budget.timeout_seconds
        observation_ordinal = 0
        for case in cases:
            if execution.budget_exhausted or execution.timed_out:
                execution.complete = False
                break
            result: dict[str, Any] = {
                "caseId": _case_id(case),
                "category": _case_category(case),
                "protocol": _case_protocol(case),
                "caseVersion": _value(case, "case_version", "caseVersion", default=None),
                "runs": [],
                "ruleScores": [],
                "judgeScores": [],
                "reviewReasons": [],
            }
            requirement = self._requirements_ok(case)
            if requirement is not None:
                result["status"] = "skipped"
                result["reviewReasons"] = [requirement]
                case_results.append(result)
                continue
            observations: list[Any] = []
            embedding_done = False
            if _case_protocol(case) == "memory_retrieval":
                # Retrieval cases have no text Candidate protocol.  Exercise
                # the explicit embedding port and score the versioned,
                # deterministic retrieval fixture carried by the Case.
                try:
                    context = _case_context(case)
                    embedding_started = time.perf_counter()
                    embedding_available = context.get(
                        "embeddingAvailable",
                        context.get("embedding_available", True),
                    )
                    if embedding_available is not False:
                        await self._call(
                            self.embedding,
                            "embedding",
                            case,
                            execution,
                            deadline,
                        )
                        embedding_done = True
                    retrieved = context.get("retrieved_memory_ids", context.get("retrievedMemoryIds"))
                    if not isinstance(retrieved, list):
                        retrieved = []
                    vector_hits = context.get("vector_hits", context.get("vectorHits", 0))
                    graph_hits = context.get("graph_hits", context.get("graphHits", 0))
                    raw_memory = {
                        "text": json.dumps(
                            {
                                "retrievedMemoryIds": retrieved,
                                "vectorHits": vector_hits,
                                "graphHits": graph_hits,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    }
                    observation = _build_observation(
                        case,
                        0,
                        raw_memory,
                        latency_ms=(time.perf_counter() - embedding_started) * 1000,
                    )
                    observations.append(observation)
                    rule_score = await _maybe_await(
                        _score_rule(
                            self.rule_scorer,
                            case,
                            observation,
                            previous_observations=observations[:-1],
                        )
                    )
                    result["ruleScores"].append(_as_plain(rule_score))
                    result["runs"].append(
                        {
                            "runIndex": 0,
                            "candidateSummary": "retrieval-only observation",
                            "observation": _as_plain(observation),
                            "ruleScore": _as_plain(rule_score),
                        }
                    )
                    observation_ordinal += 1
                except EvaluationBudgetExceeded as exc:
                    execution.complete = False
                    execution.budget_exhausted = True
                    execution.budget_reason = str(exc)
                except EvaluationTimeout:
                    execution.complete = False
                    execution.timed_out = True
                    execution.errors.append("evaluation_timeout")
                except Exception as exc:
                    execution.complete = False
                    execution.errors.append(f"embedding:{type(exc).__name__}")
                    result["reviewReasons"].append("embedding_failed")
            for run_index in range(self.candidate_repetitions) if _case_protocol(case) != "memory_retrieval" else ():
                if execution.budget_exhausted:
                    execution.complete = False
                    break
                try:
                    before = time.perf_counter()
                    raw = await self._call(
                        self.candidate,
                        "candidate",
                        case,
                        execution,
                        deadline,
                        run_index=run_index,
                    )
                    observation = _build_observation(
                        case,
                        run_index,
                        raw,
                        latency_ms=(time.perf_counter() - before) * 1000,
                    )
                    observations.append(observation)
                    rule_score = await _maybe_await(
                        _score_rule(
                            self.rule_scorer,
                            case,
                            observation,
                            previous_observations=observations[:-1],
                        )
                    )
                    result["ruleScores"].append(_as_plain(rule_score))
                    result["runs"].append(
                        {
                            "runIndex": run_index,
                            "candidateSummary": _result_text(raw)[:2_000],
                            "observation": _as_plain(observation),
                            "ruleScore": _as_plain(rule_score),
                        }
                    )
                    observation_ordinal += 1
                except EvaluationBudgetExceeded as exc:
                    execution.complete = False
                    execution.budget_exhausted = True
                    execution.budget_reason = str(exc)
                    break
                except EvaluationTimeout:
                    execution.complete = False
                    execution.timed_out = True
                    execution.errors.append("evaluation_timeout")
                    break
                except Exception as exc:  # preserve a safe partial result
                    execution.complete = False
                    execution.errors.append(f"candidate:{type(exc).__name__}")
                    observation = _build_observation(
                        case,
                        run_index,
                        {"text": ""},
                        latency_ms=0,
                        error=type(exc).__name__,
                    )
                    observations.append(observation)
                    rule_score = await _maybe_await(
                        _score_rule(
                            self.rule_scorer,
                            case,
                            observation,
                            previous_observations=observations[:-1],
                        )
                    )
                    result["ruleScores"].append(_as_plain(rule_score))
                    result["runs"].append(
                        {
                            "runIndex": run_index,
                            "candidateSummary": "",
                            "observation": _as_plain(observation),
                            "ruleScore": _as_plain(rule_score),
                            "errorCode": type(exc).__name__,
                        }
                    )
                    observation_ordinal += 1
            if (
                observations
                and _case_protocol(case) != "memory_retrieval"
                and self.enable_judge
                and self.judge is not None
                and not execution.timed_out
            ):
                # Judge every real observation.  A deterministic fifth of the
                # observation stream receives an independent second score;
                # this is keyed by observation order, not case order, so both
                # Candidate repetitions are covered.
                first_ordinal = observation_ordinal - len(observations)
                for run_position, observation in enumerate(observations):
                    ordinal = first_ordinal + run_position
                    if not _fraction_sample(ordinal, self.judge_sample_rate):
                        continue
                    if execution.budget_exhausted:
                        execution.complete = False
                        break
                    judge_count = self.judge_repetitions
                    if self.mode == "live" and self.judge_repetitions == 1:
                        judge_count = 1 + int(
                            _fraction_sample(ordinal, self.judge_repeat_sample_rate)
                        )
                    for attempt in range(judge_count):
                        try:
                            raw_judge = await self._call(
                                self.judge,
                                "judge",
                                case,
                                execution,
                                deadline,
                                observation=observation,
                                attempt=attempt,
                            )
                            normalized_judge = _normalize_judge(raw_judge)
                            result["judgeScores"].append(normalized_judge)
                            result["runs"][run_position].setdefault("judgeScores", []).append(normalized_judge)
                            if normalized_judge.get("judgeErrorCode"):
                                execution.complete = False
                                execution.errors.append(
                                    f"judge:{normalized_judge['judgeErrorCode']}"
                                )
                        except EvaluationBudgetExceeded as exc:
                            execution.complete = False
                            execution.budget_exhausted = True
                            execution.budget_reason = str(exc)
                            break
                        except EvaluationTimeout:
                            execution.complete = False
                            execution.timed_out = True
                            execution.errors.append("evaluation_timeout")
                            break
                        except Exception as exc:
                            execution.complete = False
                            execution.errors.append(f"judge:{type(exc).__name__}")
                            result["reviewReasons"].append("judge_failed")
                            break
                    if execution.budget_exhausted or execution.timed_out:
                        break
            if (
                bool(_value(case, "requires_live_embedding", "requiresLiveEmbedding", default=False))
                or _case_protocol(case) == "memory_retrieval"
            ) and _case_protocol(case) != "memory_retrieval" and not embedding_done and not execution.timed_out:
                try:
                    await self._call(
                        self.embedding,
                        "embedding",
                        case,
                        execution,
                        deadline,
                    )
                except EvaluationBudgetExceeded as exc:
                    execution.complete = False
                    execution.budget_exhausted = True
                    execution.budget_reason = str(exc)
                except EvaluationTimeout:
                    execution.complete = False
                    execution.timed_out = True
                    execution.errors.append("evaluation_timeout")
                except Exception as exc:
                    execution.complete = False
                    execution.errors.append(f"embedding:{type(exc).__name__}")
                    result["reviewReasons"].append("embedding_failed")
            rule_scores = result["ruleScores"]
            result["ruleScore"] = _aggregate_rule_scores(rule_scores)
            if _hard_failure(result["ruleScore"]):
                result["reviewReasons"].append("rule_hard_failure")
            judge_scores = result["judgeScores"]
            if judge_scores:
                for run in result["runs"]:
                    run_scores = run.get("judgeScores", [])
                    if run_scores:
                        result["reviewReasons"].extend(
                            _judge_reasons(
                                run_scores[0],
                                run_scores[1] if len(run_scores) > 1 else None,
                            )
                        )
                result["judgeScore"] = judge_scores[0]
            if _hard_failure(result["ruleScore"]) and any(
                isinstance(score.get("total_score"), (int, float))
                and float(score["total_score"]) >= 4.0
                for score in judge_scores
            ):
                result["reviewReasons"].append("rule_judge_conflict")
            result["reviewReasons"] = sorted(set(result["reviewReasons"]))
            result["candidateSummary"] = (
                str(result["runs"][0].get("candidateSummary", ""))
                if result["runs"]
                else ""
            )
            result["judgeDisagreement"] = (
                "judge_disagreement" in result["reviewReasons"]
            )
            first_confidence = (
                str(judge_scores[0].get("confidence", "unknown"))
                if judge_scores
                else "unknown"
            )
            result["judgeEffectiveConfidence"] = (
                "low" if result["judgeDisagreement"] else first_confidence
            )
            result["status"] = (
                "completed"
                if result["runs"]
                and not any(run.get("errorCode") for run in result["runs"])
                and "judge_failed" not in result["reviewReasons"]
                and "judge_schema_failure" not in result["reviewReasons"]
                and "embedding_failed" not in result["reviewReasons"]
                else "partial"
            )
            case_results.append(result)
            execution.completed_cases += 1
            if execution.budget_exhausted or execution.timed_out:
                execution.complete = False
                break
        if execution.completed_cases < len(cases):
            execution.complete = False
        execution.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return build_report(
            cases=case_results,
            execution=execution,
            budget=self.budget,
            selected_cases=cases,
            enable_judge=self.enable_judge,
            planned_calls=self.planned_calls(),
        )


def _aggregate_rule_scores(scores: Sequence[Any]) -> dict[str, Any]:
    if not scores:
        return {"hard_failure": True, "failures": ["no_candidate_result"]}
    plain = [_as_plain(score) for score in scores]
    failures: set[str] = set()
    for score in plain:
        failures.update(_failures(score))
    result: dict[str, Any] = dict(plain[0]) if isinstance(plain[0], Mapping) else {}
    result["hard_failure"] = any(_hard_failure(score) for score in plain)
    result["hardFailure"] = result["hard_failure"]
    result["failures"] = sorted(failures)
    return result


def select_cases(
    cases: Iterable[Any],
    *,
    case_ids: Iterable[str] | None = None,
    categories: Iterable[str] | None = None,
) -> list[Any]:
    """Stable case/category filtering helper used by the CLI and tests."""

    ids = {str(item) for item in case_ids} if case_ids else None
    category_set = {str(item) for item in categories} if categories else None
    selected = [
        case
        for case in cases
        if (ids is None or _case_id(case) in ids)
        and (category_set is None or _case_category(case) in category_set)
    ]
    return sorted(selected, key=lambda item: (_case_id(item), _case_category(item), _case_protocol(item)))


__all__ = [
    "ArkCandidateAdapter",
    "ArkJudgeAdapter",
    "DEFAULT_CANDIDATE_MODEL",
    "DEFAULT_CANDIDATE_MAX_INPUT_TOKENS",
    "DEFAULT_CANDIDATE_MAX_OUTPUT_TOKENS",
    "DEFAULT_EMBEDDING_CNY_PER_MILLION",
    "DEFAULT_EMBEDDING_MAX_INPUT_TOKENS",
    "DEFAULT_JUDGE_INPUT_CNY_PER_MILLION",
    "DEFAULT_JUDGE_MAX_INPUT_TOKENS",
    "DEFAULT_JUDGE_MAX_OUTPUT_TOKENS",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_JUDGE_OUTPUT_CNY_PER_MILLION",
    "DEFAULT_TEXT_INPUT_CNY_PER_MILLION",
    "DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION",
    "EvaluationBudget",
    "EvaluationBudgetExceeded",
    "EvaluationExecution",
    "EvaluationMode",
    "EvaluationRunner",
    "EvaluationTimeout",
    "FakeCandidate",
    "FakeEmbedding",
    "FakeJudge",
    "select_cases",
]
