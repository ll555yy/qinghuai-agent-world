"""An opt-in, bounded seven-day runner built on the production world engine.

This module does not implement NPC rules.  It only supplies a deterministic
driver, a model-call accounting wrapper, and a report projection.  In
particular, ``mode=real`` requires an explicit opt-in and a configured model;
missing credentials are rejected before a Run is created and therefore cannot
cause a network request.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..ai.models import TextGenerationRequest, TextGenerationResult
from ..ai.protocols import (
    ChatDecision,
    DailyActionDecision,
    ExitConsolidation,
    InvitationDecision,
    SegmentSummary,
    SpeechGeneration,
)
from ..orchestration.run_service import RunService
from ..orchestration.world_engine import WorldEngine
from ..scenario.models import ScenarioRegistry
from .manifest import AttemptLedger, ManifestStatus

SimulationRoute = Literal["observer", "pro_lin", "pro_zhao"]
SimulationMode = Literal["offline", "real"]
DEFAULT_SIMULATION_SEED = 20260819
DEFAULT_TEXT_INPUT_CNY_PER_MILLION = 0.6
DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION = 3.6
DEFAULT_EMBEDDING_CNY_PER_MILLION = 0.7
_SAFE_DATABASE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HISTORICAL_CUE = re.compile(
    r"过去|以前|旧事|上次|曾经|当年|承诺|旧怨|来历|父亲|祖父"
)

ROUTE_AGENDAS: dict[SimulationRoute, str | None] = {
    "observer": None,
    "pro_lin": "agenda_001_literary_society",
    "pro_zhao": "agenda_003_cultural_operation",
}

@dataclass(frozen=True, slots=True)
class PlayerStrategyStep:
    """One public, non-adaptive player action in a benchmark route."""

    step_id: str
    day: int
    target_actor_id: str
    message: str


# ``pro_lin`` is the reachable coalition route.  Its fixed script uses only
# public scenario facts and gives each NPC a reason, grounded in that NPC's
# public role, to consider one guarded joint proposal.  It never reads private
# memories or current stance fields.  ``pro_zhao`` intentionally remains a
# low-effort, single-person route so the benchmark also contains a real failure
# control rather than nine variants of an optimized playthrough.
ROUTE_PLAYER_STEPS: dict[SimulationRoute, tuple[PlayerStrategyStep, ...]] = {
    "observer": (),
    "pro_lin": (
        PlayerStrategyStep(
            "lin_opening",
            1,
            "npc_001",
            "林老师，我愿意帮忙把公益文化放在方案核心。如果你对以前的合作有什么顾虑，"
            "哪些底线应该写进方案？如果明确旧书保护、收支公开和各方职责，你是否愿意支持联合提交，"
            "并支持青槐文社成为核心主张？请把条件直接说清楚。",
        ),
        PlayerStrategyStep(
            "chen_safeguards",
            2,
            "npc_004",
            "陈月，我想把健康角写成公益配套：不碰病人隐私、不夸大医疗作用，物资和责任都列清楚。"
            "在这些前提下，你是否愿意参与，并支持大家提交以青槐文社为核心的联合方案？"
            "若只能附条件支持，也请直接说条件。",
        ),
        PlayerStrategyStep(
            "shen_consent",
            3,
            "npc_002",
            "星遥，联合方案可以明确插画由你独立创作，公开人物故事前逐一征得同意，也不要求你现场应酬。"
            "如果这些边界写进去，你是否愿意提供绘本和宣传插画，并支持联合提交与青槐文社主张？"
            "你可以直接说支持、附条件支持或反对。",
        ),
        PlayerStrategyStep(
            "zhao_accountability",
            4,
            "npc_003",
            "赵磊，方案需要你的运营和资源能力，但品牌授权、账目、旧书保护和商业比例都要书面透明，"
            "公益活动优先。若给你清楚的运营职责而不是排斥商业，你是否愿意接受这些约束，"
            "支持联合提交和青槐文社这项核心主张？请直说你的底线。",
        ),
        PlayerStrategyStep(
            "zhou_conditions",
            5,
            "npc_005",
            "周老板，目前能合并的方案是：青槐文社为公益核心，绘本、健康角和受约束的文创运营作为配套；"
            "书店性质、旧书安全、品牌授权和账目由书面条款保护。哪些条件满足后你愿意支持联合提交？"
            "也请明确你对青槐文社是支持、附条件支持还是反对，以及是否愿意授权提交。",
        ),
        PlayerStrategyStep(
            "lin_final_commitment",
            6,
            "npc_001",
            "林老师，提交前我再确认一次：方案保留公益文化核心，也吸收绘本、健康角和透明运营，"
            "周老板的保护条款写在前面。你是否愿意接受这份妥协并明确支持整体提交和青槐文社？"
            "还有必须补上的承诺请现在说清楚。",
        ),
        PlayerStrategyStep(
            "zhou_final_authorization",
            7,
            "npc_005",
            "今天是截止日。我准备按公益核心、旧书保护、授权边界和公开账目这些条件合并方案，"
            "请你最后明确：是否支持这份联合方案，是否支持或附条件支持青槐文社，"
            "以及对正式提交是授权、附条件授权还是拒绝。",
        ),
    ),
    "pro_zhao": (
        PlayerStrategyStep(
            "zhao_history",
            1,
            "npc_003",
            "我支持你提出可持续的商业运营。你和周老板过去是否有没说开的合作或分歧？"
            "这会怎样影响你现在推动邻里文创运营？",
        ),
        PlayerStrategyStep(
            "zhao_final_stance",
            7,
            "npc_003",
            "现在到了提交期限。你最终是否愿意支持大家提交联合方案？"
            "对于邻里文创运营这项主张，你是支持、附条件支持，还是反对？请直接表态并说清底线。",
        ),
    ),
}

# v1 remains immutable because three preregistered experiments already bind
# its canonical step digests.  v2 changes only the final public clarification:
# conditions that the fixed proposal already satisfies should not be repeated
# as unresolved conditions, while genuinely unmet conditions remain allowed.
PRO_LIN_V2_STEPS: tuple[PlayerStrategyStep, ...] = (
    *ROUTE_PLAYER_STEPS["pro_lin"][:-1],
    PlayerStrategyStep(
        "zhou_final_authorization_v2",
        7,
        "npc_005",
        "今天是截止日。公益核心、旧书保护、品牌授权边界、公开账目和各方职责都已经写入联合方案。"
        "请逐项核对：如果这些条件已经满足，请明确表示支持青槐文社并授权正式提交，不要把已经满足的条件"
        "再次列为附加条件；只有仍有具体未满足事项时，才请选择附条件支持或附条件授权，并指出缺少哪一项。"
        "如果你拒绝，也请直接说明。",
    ),
)

# v3 keeps the same public coalition work through Day6, but separates the two
# independent decisions that the chapter resolver records for Zhou.  Five v2
# holdout seeds showed that asking for both in one message collapsed into
# conditional support.  No runtime stance or private input is read here.
PRO_LIN_V3_STEPS: tuple[PlayerStrategyStep, ...] = (
    *ROUTE_PLAYER_STEPS["pro_lin"][:-1],
    PlayerStrategyStep(
        "zhou_final_agenda_support_v3",
        7,
        "npc_005",
        "今天是截止日，联合方案已经定稿并当日生效：青槐文社是公益核心；书店核心布局、旧书保存环境"
        "和贵重旧籍均不改动、不触碰；品牌授权边界、公开账目和责任人也写入正文。先只确认青槐文社"
        "议案本身。上述保护条件现在已经满足，不是未来承诺。若你没有其他具体未满足项，请明确说"
        "“我无附加条件支持青槐文社作为核心议案”，不要用“只要……就……”重新表述已经满足的条件；"
        "若仍不同意，请指出当前尚未满足的具体事实。",
    ),
    PlayerStrategyStep(
        "zhou_final_submission_authorization_v3",
        7,
        "npc_005",
        "现在单独确认截止日提交权限。联合方案的保护条款已经写入并生效，正式提交不会改变书店核心"
        "布局、旧书保存环境或贵重旧籍保护。若没有新的具体未满足事项，请明确说“我批准并授权今天"
        "正式提交联合方案”，不要再次附加已经写入的旧书保护条件；若拒绝授权，请指出当前缺少的"
        "具体事实。",
    ),
)

ROUTE_PLAYER_STRATEGIES: dict[str, tuple[PlayerStrategyStep, ...]] = {
    "strategy.observer.v1": ROUTE_PLAYER_STEPS["observer"],
    "strategy.pro_lin.v1": ROUTE_PLAYER_STEPS["pro_lin"],
    "strategy.pro_lin.v2": PRO_LIN_V2_STEPS,
    "strategy.pro_lin.v3": PRO_LIN_V3_STEPS,
    "strategy.pro_zhao.v1": ROUTE_PLAYER_STEPS["pro_zhao"],
}


def player_strategy_steps(
    route: SimulationRoute,
    strategy_id: str | None = None,
) -> tuple[PlayerStrategyStep, ...]:
    """Resolve the immutable strategy version named by a manifest attempt."""

    resolved_id = strategy_id or f"strategy.{route}.v1"
    steps = ROUTE_PLAYER_STRATEGIES.get(resolved_id)
    if steps is None or not resolved_id.startswith(f"strategy.{route}."):
        raise ValueError(f"strategy {resolved_id!r} does not match route {route!r}")
    return steps

# Kept as a compact compatibility/readability projection for tests and report
# tooling that only needs to inspect the public player text.
ROUTE_PLAYER_MESSAGES: dict[SimulationRoute, tuple[str, ...]] = {
    route: tuple(step.message for step in steps)
    for route, steps in ROUTE_PLAYER_STEPS.items()
}


class SimulationBudgetExceeded(RuntimeError):
    """Raised internally when a model provider budget is exhausted."""


def _safe_exception_label(exc: BaseException) -> str:
    """Return an error type plus an optional non-sensitive constraint name."""

    label = type(exc).__name__
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    if isinstance(constraint, str) and _SAFE_DATABASE_IDENTIFIER.fullmatch(constraint):
        return f"{label}:{constraint}"
    return label


@dataclass(frozen=True, slots=True)
class SimulationBudget:
    """Hard provider-call limits for one autonomous seven-day run."""

    daily_action: int = 35
    invitation: int = 35
    speech: int = 280
    chat_decision: int = 560
    exit_consolidation: int = 105
    segment_summary: int = 105
    max_messages: int = 280
    max_conversations: int = 35

    @property
    def total(self) -> int:
        return sum(
            getattr(self, name)
            for name in (
                "daily_action",
                "invitation",
                "speech",
                "chat_decision",
                "exit_consolidation",
                "segment_summary",
            )
        )

    def for_protocol(self, protocol: str) -> int:
        names = {
            "DailyActionDecision": "daily_action",
            "InvitationDecision": "invitation",
            "SpeechGeneration": "speech",
            "ChatDecision": "chat_decision",
            "ExitConsolidation": "exit_consolidation",
            "SegmentSummary": "segment_summary",
        }
        field_name = names.get(protocol)
        return getattr(self, field_name) if field_name else 0


@dataclass(slots=True)
class SimulationMetrics:
    """Serializable, privacy-preserving metrics for one simulation."""

    protocol_calls: Counter[str] = field(default_factory=Counter)
    provider_calls: Counter[str] = field(default_factory=Counter)
    retries: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    latency_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    tokens: Counter[str] = field(default_factory=Counter)
    events: Counter[str] = field(default_factory=Counter)
    conversations_created: int = 0
    conversations_closed: int = 0
    messages: int = 0
    memories_by_source: Counter[str] = field(default_factory=Counter)
    memories_total: int = 0
    goals_by_status: Counter[str] = field(default_factory=Counter)
    relationship_changes: int = 0
    final_day7_branch: str | None = None
    final_world_time: str | None = None
    rejected: bool = False
    rejection_reason: str | None = None
    budget_exhausted: bool = False
    abnormal_termination: str | None = None
    scripted_actions: Counter[str] = field(default_factory=Counter)
    memory_retrieval_calls: int = 0
    memory_retrieved_ids: int = 0
    memory_vector_hits: int = 0
    memory_graph_hits: int = 0
    memory_vector_status: str = "unavailable"
    memory_graph_status: str = "unavailable"
    schema_attempts: int = 0
    schema_successes: int = 0
    safe_results: int = 0
    physical_provider_requests: int | None = None
    provider_retries: int | None = None
    goal_changes: list[dict[str, Any]] = field(default_factory=list)
    relationship_change_details: list[dict[str, Any]] = field(default_factory=list)
    chapter_stances: dict[str, Any] = field(default_factory=dict)
    agenda_results: dict[str, Any] = field(default_factory=dict)
    player_result: Any = None
    npc_speech_count: int = 0
    player_speech_count: int = 0
    npc_speech_by_actor: Counter[str] = field(default_factory=Counter)
    npc_active_actions_by_actor: Counter[str] = field(default_factory=Counter)
    conversation_summaries: list[dict[str, Any]] = field(default_factory=list)
    fired_world_event_ids: list[str] = field(default_factory=list)
    skipped_world_event_ids: list[str] = field(default_factory=list)
    chapter_stance_changes: int = 0
    schema_first_attempts: int = 0
    schema_first_successes: int = 0
    quality_gate_failures: list[str] = field(default_factory=list)
    repository_recovered: bool | None = None
    temporary_run_deleted: bool | None = None
    historical_invitation_intents: int = 0
    historical_topic_messages: int = 0
    goal_completion_rate: float | None = None
    embedding_provider_requests: int = 0
    embedding_tokens: int = 0
    text_input_cny_per_million: float = DEFAULT_TEXT_INPUT_CNY_PER_MILLION
    text_output_cny_per_million: float = DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION
    embedding_cny_per_million: float = DEFAULT_EMBEDDING_CNY_PER_MILLION

    def record_protocol_call(self, protocol: str) -> None:
        self.protocol_calls[protocol] += 1

    def record_provider_result(
        self,
        protocol: str,
        duration_ms: float,
        result: TextGenerationResult | None,
        error: BaseException | None = None,
    ) -> None:
        self.provider_calls[protocol] += 1
        self.latency_ms[protocol].append(round(duration_ms, 3))
        if error is not None:
            self.failures[f"{protocol}:{type(error).__name__}"] += 1
            return
        if result is not None and result.usage is not None:
            usage = result.usage
            for key, value in (
                ("prompt", usage.prompt_tokens),
                ("completion", usage.completion_tokens),
                ("total", usage.total_tokens),
            ):
                if value is not None:
                    self.tokens[f"{protocol}:{key}"] += value

    def observe_run(
        self,
        run: Any,
        initial_relationships: dict[tuple[str, str], dict[str, Any]] | None = None,
        initial_goals: dict[str, dict[str, Any]] | None = None,
        configured_event_ids: set[str] | None = None,
    ) -> None:
        self.events = Counter(event.event_type for event in run.events)
        self.conversations_created = self.events["conversation_created"]
        self.conversations_closed = self.events["conversation_closed"]
        self.messages = sum(len(items) for items in run.messages.values())
        self.npc_speech_count = 0
        self.player_speech_count = 0
        self.npc_speech_by_actor = Counter()
        self.historical_topic_messages = 0
        for items in run.messages.values():
            for message in items:
                author_id = str(message.get("authorActorId", ""))
                if _HISTORICAL_CUE.search(str(message.get("text", ""))):
                    self.historical_topic_messages += 1
                if author_id.startswith("npc_"):
                    self.npc_speech_count += 1
                    self.npc_speech_by_actor[author_id] += 1
                elif author_id == "player_001":
                    self.player_speech_count += 1
        self.historical_invitation_intents = sum(
            1
            for invitation in run.invitations.values()
            if _HISTORICAL_CUE.search(str(invitation.get("_intent", "")))
        )
        thought_started = Counter(
            str(event.payload.get("actorId"))
            for event in run.events
            if event.event_type == "npc_thought_started"
            and str(event.payload.get("actorId", "")).startswith("npc_")
        )
        waited = Counter(
            str(event.payload.get("actorId"))
            for event in run.events
            if event.event_type == "npc_waited"
            and str(event.payload.get("actorId", "")).startswith("npc_")
        )
        self.npc_active_actions_by_actor = Counter(
            {
                actor_id: max(0, count - waited[actor_id])
                for actor_id, count in thought_started.items()
            }
        )
        self.conversation_summaries = [
            {
                "conversationId": conversation.conversation_id,
                "participants": sorted(conversation.participant_history()),
                "messageCount": len(run.messages.get(conversation.conversation_id, [])),
                "status": conversation.status,
                "closeReason": conversation.close_reason,
            }
            for conversation in sorted(
                run.conversations.values(), key=lambda item: item.creation_seq
            )
        ]
        self.fired_world_event_ids = sorted(run.fired_event_ids)
        all_event_ids = configured_event_ids or set()
        self.skipped_world_event_ids = sorted(all_event_ids - run.fired_event_ids)
        self.memories_total = len(run.memories)
        self.memories_by_source = Counter(
            str(memory.get("source", "unknown")) for memory in run.memories.values()
        )
        self.goals_by_status = Counter(
            str(goal.get("status", "unknown")) for goal in run.goals.values()
        )
        self.goal_completion_rate = (
            self.goals_by_status["achieved"] / len(run.goals)
            if run.goals
            else None
        )
        initial_relationships = initial_relationships or {}
        self.relationship_change_details = []
        for key, value in run.relationships.items():
            before = initial_relationships.get(key)
            if before != value:
                self.relationship_change_details.append(
                    {
                        "fromActorId": key[0],
                        "toActorId": key[1],
                        "before": before,
                        "after": value,
                    }
                )
        self.relationship_changes = len(self.relationship_change_details)
        initial_goals = initial_goals or {}
        self.goal_changes = []
        for goal_id, goal in run.goals.items():
            before = initial_goals.get(goal_id, {}).get("status")
            after = goal.get("status")
            if before != after:
                self.goal_changes.append(
                    {"goalId": goal_id, "before": before, "after": after}
                )
        self.chapter_stances = dict(getattr(run, "chapter_actor_stances", {}))
        self.chapter_stance_changes = sum(
            1 for stance in self.chapter_stances.values() if stance != "unknown"
        )
        self.chapter_stances["zhouAuthorization"] = getattr(
            run, "zhou_authorization", "unknown"
        )
        self.agenda_results = dict(
            run.chapter_resolution.get("agendaResults", {})
            if isinstance(run.chapter_resolution, dict)
            else {}
        )
        self.player_result = (
            run.chapter_resolution.get("playerTaskResult")
            if isinstance(run.chapter_resolution, dict)
            else None
        )
        self.final_day7_branch = (
            run.chapter_resolution.get("branch")
            if isinstance(run.chapter_resolution, dict)
            else None
        )
        self.final_world_time = run.clock.as_dict().get("label")

    def to_dict(self) -> dict[str, Any]:
        def counters(value: Counter[str]) -> dict[str, int]:
            return dict(sorted(value.items()))

        latencies = {
            protocol: {
                "count": len(values),
                "totalMs": round(sum(values), 3),
                "maxMs": round(max(values), 3) if values else 0.0,
                "avgMs": round(sum(values) / len(values), 3) if values else 0.0,
            }
            for protocol, values in sorted(self.latency_ms.items())
        }
        prompt_tokens = sum(
            count for key, count in self.tokens.items() if key.endswith(":prompt")
        )
        completion_tokens = sum(
            count for key, count in self.tokens.items() if key.endswith(":completion")
        )
        text_cost = (
            prompt_tokens * self.text_input_cny_per_million
            + completion_tokens * self.text_output_cny_per_million
        ) / 1_000_000
        embedding_cost = (
            self.embedding_tokens * self.embedding_cny_per_million / 1_000_000
        )
        return {
            "protocolCalls": counters(self.protocol_calls),
            "providerCalls": counters(self.provider_calls),
            "retries": counters(self.retries),
            "failures": counters(self.failures),
            "latencyMs": latencies,
            "tokens": counters(self.tokens),
            "events": counters(self.events),
            "worldEvents": {
                "firedIds": self.fired_world_event_ids,
                "skippedIds": self.skipped_world_event_ids,
            },
            "invitations": {
                "requested": self.events["invitation_requested"],
                "accepted": self.events["invitation_accepted"],
                "refused": self.events["invitation_refused"],
                "expired": self.events["invitation_expired"],
            },
            "conversations": {
                "created": self.conversations_created,
                "closed": self.conversations_closed,
                "items": self.conversation_summaries,
            },
            "messages": self.messages,
            "memories": {
                "total": self.memories_total,
                "bySource": counters(self.memories_by_source),
            },
            "goalsByStatus": counters(self.goals_by_status),
            "goalCompletionRate": self.goal_completion_rate,
            "relationshipChanges": self.relationship_changes,
            "day7Branch": self.final_day7_branch,
            "finalWorldTime": self.final_world_time,
            "rejected": self.rejected,
            "rejectionReason": self.rejection_reason,
            "budgetExhausted": self.budget_exhausted,
            "abnormalTermination": self.abnormal_termination,
            "scriptedActions": counters(self.scripted_actions),
            "memoryRetrieval": {
                "calls": self.memory_retrieval_calls,
                "retrievedIds": self.memory_retrieved_ids,
                "vectorHits": self.memory_vector_hits,
                "graphHits": self.memory_graph_hits,
                "vector": self.memory_vector_status,
                "graph": self.memory_graph_status,
            },
            "historicalCueCounts": {
                "invitationIntents": self.historical_invitation_intents,
                "messages": self.historical_topic_messages,
            },
            "schema": {
                "attempts": self.schema_attempts,
                "successes": self.schema_successes,
                "successRate": (
                    self.schema_successes / self.schema_attempts
                    if self.schema_attempts
                    else None
                ),
                "firstAttempts": self.schema_first_attempts,
                "firstSuccesses": self.schema_first_successes,
                "firstSuccessRate": (
                    self.schema_first_successes / self.schema_first_attempts
                    if self.schema_first_attempts
                    else None
                ),
                "p95LatencyMs": self._p95_latency(),
            },
            "safeResults": self.safe_results,
            "physicalProviderRequests": self.physical_provider_requests,
            "providerRetries": self.provider_retries,
            "costEstimate": {
                "currency": "CNY",
                "textPromptTokens": prompt_tokens,
                "textCompletionTokens": completion_tokens,
                "embeddingTokens": self.embedding_tokens,
                "embeddingProviderRequests": self.embedding_provider_requests,
                "textInputCnyPerMillion": self.text_input_cny_per_million,
                "textOutputCnyPerMillion": self.text_output_cny_per_million,
                "embeddingCnyPerMillion": self.embedding_cny_per_million,
                "textCny": round(text_cost, 6),
                "embeddingCny": round(embedding_cost, 6),
                "totalCny": round(text_cost + embedding_cost, 6),
                "basis": "token_usage_x_configured_rates",
            },
            "goalChanges": self.goal_changes,
            "goalChangeCount": len(self.goal_changes),
            "relationshipChangeDetails": self.relationship_change_details,
            "chapterStances": self.chapter_stances,
            "chapterStanceChangeCount": self.chapter_stance_changes,
            "agendaResults": self.agenda_results,
            "playerResult": self.player_result,
            "repositoryRecovered": self.repository_recovered,
            "temporaryRunDeleted": self.temporary_run_deleted,
            "speech": {
                "npc": self.npc_speech_count,
                "player": self.player_speech_count,
                "byNpc": counters(self.npc_speech_by_actor),
            },
            "npcActiveActions": {
                "total": sum(self.npc_active_actions_by_actor.values()),
                "byNpc": counters(self.npc_active_actions_by_actor),
            },
            "qualityGateFailures": list(self.quality_gate_failures),
        }

    def _p95_latency(self) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for protocol, values in self.latency_ms.items():
            if not values:
                result[protocol] = None
                continue
            ordered = sorted(values)
            index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
            result[protocol] = round(ordered[index], 3)
        return result


@dataclass(frozen=True, slots=True)
class SimulationReport:
    """Top-level report returned by the runner."""

    route: SimulationRoute
    mode: SimulationMode
    seed: int
    metrics: SimulationMetrics
    budget: SimulationBudget
    run_id: str | None = None
    attempt_id: str | None = None
    attempt_status: ManifestStatus | None = None
    manifest_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "selectedAgendaId": ROUTE_AGENDAS[self.route],
            "mode": self.mode,
            "seed": self.seed,
            "runId": self.run_id,
            "attemptId": self.attempt_id,
            "attemptStatus": self.attempt_status,
            "manifestDigest": self.manifest_digest,
            "budget": asdict(self.budget),
            "metrics": self.metrics.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        data = self.metrics.to_dict()
        lines = [
            "# Qinghuai seven-day simulation report",
            "",
            f"- Route: `{self.route}`",
            f"- Mode: `{self.mode}`",
            f"- Seed: `{self.seed}`",
            f"- Run: `{self.run_id or 'not-created'}`",
            f"- Attempt: `{self.attempt_id or 'n/a'}`",
            f"- Attempt status: `{self.attempt_status or 'n/a'}`",
            "",
            "## Outcome",
            "",
            f"- Rejected: `{data['rejected']}` ({data['rejectionReason'] or 'none'})",
            f"- Final world time: `{data['finalWorldTime'] or 'n/a'}`",
            f"- Day7 branch: `{data['day7Branch'] or 'n/a'}`",
            f"- Messages: `{data['messages']}`",
            f"- Relationship changes: `{data['relationshipChanges']}`",
            f"- Memories: `{data['memories']['total']}`",
            f"- NPC/player speech: `{data['speech']['npc']}/{data['speech']['player']}`",
            f"- Schema success rate: `{data['schema']['successRate']}`",
            f"- Safe results: `{data['safeResults']}`",
            f"- Abnormal termination: `{data['abnormalTermination'] or 'none'}`",
            f"- Player result: `{data['playerResult'] or 'n/a'}`",
            f"- Goal completion rate: `{data['goalCompletionRate']}`",
            f"- Estimated model cost (CNY): `{data['costEstimate']['totalCny']}`",
            f"- Repository recovered: `{data['repositoryRecovered']}`",
            f"- Temporary Run deleted: `{data['temporaryRunDeleted']}`",
            f"- Memory retrieval calls: `{data['memoryRetrieval']['calls']}`",
            f"- Vector/Graph hits: `{data['memoryRetrieval']['vectorHits']}/{data['memoryRetrieval']['graphHits']}`",
            f"- Fired/skipped world events: `{len(data['worldEvents']['firedIds'])}/{len(data['worldEvents']['skippedIds'])}`",
            f"- Quality gate failures: `{','.join(data['qualityGateFailures']) or 'none'}`",
            "",
            "## Protocol calls",
            "",
            "| Protocol | Logical calls | Provider calls | Retries | Failures |",
            "|---|---:|---:|---:|---:|",
        ]
        protocols = sorted(
            set(data["protocolCalls"])
            | set(data["providerCalls"])
            | {item.split(":", 1)[0] for item in data["failures"]}
        )
        for protocol in protocols:
            retries = data["retries"].get(protocol, 0)
            failures = sum(
                count
                for key, count in data["failures"].items()
                if key.startswith(f"{protocol}:")
            )
            lines.append(
                f"| `{protocol}` | {data['protocolCalls'].get(protocol, 0)} "
                f"| {data['providerCalls'].get(protocol, 0)} | {retries} | {failures} |"
            )
        lines.extend(
            [
                "",
                "## Events",
                "",
                "| Event | Count |",
                "|---|---:|",
            ]
        )
        lines.extend(
            f"| `{event}` | {count} |" for event, count in data["events"].items()
        )
        lines.extend(
            [
                "",
                "## Agenda results",
                "",
                "| Agenda | Result |",
                "|---|---|",
            ]
        )
        lines.extend(
            f"| `{agenda}` | `{result}` |"
            for agenda, result in data["agendaResults"].items()
        )
        return "\n".join(lines) + "\n"

    def write(self, directory: str | Path, stem: str = "seven_day_simulation") -> tuple[Path, Path]:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / f"{stem}.json"
        markdown_path = output / f"{stem}.md"
        json_path.write_text(self.to_json() + "\n", encoding="utf-8")
        markdown_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, markdown_path


class _CountingModel:
    """Model decorator that enforces per-protocol and records provider usage."""

    def __init__(
        self,
        model: Any,
        metrics: SimulationMetrics,
        budget: SimulationBudget,
        total_limit: int | None = None,
    ) -> None:
        self.model = model
        self._simulation_original_model = model
        self.metrics = metrics
        self.budget = budget
        self.total_limit = total_limit
        self._last_request_id: int | None = None
        # Keep request objects alive so CPython cannot reuse an id and turn a
        # later logical invocation into a false structured retry.
        self._request_objects: dict[int, TextGenerationRequest] = {}
        self._schemas: dict[str, type[Any]] = {
            "DailyActionDecision": DailyActionDecision,
            "InvitationDecision": InvitationDecision,
            "ChatDecision": ChatDecision,
            "SpeechGeneration": SpeechGeneration,
            "SegmentSummary": SegmentSummary,
            "ExitConsolidation": ExitConsolidation,
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        protocol = self._protocol(request)
        request_key = id(request)
        first_attempt = request_key != self._last_request_id
        self._request_objects.setdefault(request_key, request)
        if self.total_limit is not None and sum(self.metrics.provider_calls.values()) >= self.total_limit:
            self.metrics.budget_exhausted = True
            raise SimulationBudgetExceeded("total")
        if first_attempt:
            if protocol and self.metrics.protocol_calls[protocol] >= self.budget.for_protocol(protocol):
                self.metrics.budget_exhausted = True
                raise SimulationBudgetExceeded(protocol)
            self.metrics.record_protocol_call(protocol)
            self._last_request_id = request_key
        if protocol and self.metrics.provider_calls[protocol] >= self.budget.for_protocol(protocol):
            self.metrics.budget_exhausted = True
            raise SimulationBudgetExceeded(protocol)
        started = time.perf_counter()
        try:
            result = await self.model.generate(request)
        except BaseException as exc:
            self.metrics.record_provider_result(
                protocol, (time.perf_counter() - started) * 1000, None, exc
            )
            self.metrics.retries[protocol] = max(
                0,
                self.metrics.provider_calls[protocol]
                - self.metrics.protocol_calls[protocol],
            )
            raise
        self.metrics.schema_attempts += 1
        if first_attempt:
            self.metrics.schema_first_attempts += 1
        schema = self._schemas.get(protocol)
        schema_ok = False
        if schema is not None:
            try:
                from ..ai.decision_service import extract_json_object

                schema.model_validate(extract_json_object(result.text))
                schema_ok = True
            except Exception:
                self.metrics.safe_results += 1
        if schema_ok:
            self.metrics.schema_successes += 1
            if first_attempt:
                self.metrics.schema_first_successes += 1
        self.metrics.record_provider_result(
            protocol, (time.perf_counter() - started) * 1000, result
        )
        # DecisionService reuses the request object for its one structured retry.
        # The provider count therefore reveals the retry count exactly.
        if self.metrics.provider_calls[protocol] > self.metrics.protocol_calls[protocol]:
            self.metrics.retries[protocol] = (
                self.metrics.provider_calls[protocol] - self.metrics.protocol_calls[protocol]
            )
        return result

    @staticmethod
    def _protocol(request: TextGenerationRequest) -> str:
        marker = "协议="
        for line in request.system_prompt.splitlines():
            if line.startswith(marker):
                return line[len(marker) :].strip()
        return "unknown"


class _CountingRetriever:
    """Observe retrieval counts without changing owner-safe retrieval."""

    def __init__(self, retriever: Any, metrics: SimulationMetrics) -> None:
        self.retriever = retriever
        self.metrics = metrics
        if getattr(retriever, "_embedding_port", None) is not None:
            metrics.memory_vector_status = "enabled"
        elif retriever.__class__.__name__ == "DatabaseMemoryRetriever":
            metrics.memory_vector_status = "not_configured"
        if retriever.__class__.__name__ == "DatabaseMemoryRetriever":
            metrics.memory_graph_status = "enabled"

    async def search(self, **kwargs: Any) -> Any:
        self.metrics.memory_retrieval_calls += 1
        try:
            result = await self.retriever.search(**kwargs)
        except Exception:
            self.metrics.safe_results += 1
            raise
        self.metrics.memory_retrieved_ids += len(getattr(result, "memory_ids", ()))
        self.metrics.memory_vector_hits += int(getattr(result, "vector_hits", 0))
        self.metrics.memory_graph_hits += int(getattr(result, "graph_hits", 0))
        return result


class SevenDaySimulationRunner:
    """Run one bounded simulation using an existing :class:`RunService`."""

    def __init__(
        self,
        registry: ScenarioRegistry,
        *,
        seed: int = DEFAULT_SIMULATION_SEED,
        budget: SimulationBudget | None = None,
        step_timeout_seconds: float = 120.0,
        run_timeout_seconds: float = 600.0,
        max_calls_per_run: int | None = None,
    ) -> None:
        self.registry = registry
        self.seed = seed
        self.budget = budget or SimulationBudget()
        self.step_timeout_seconds = step_timeout_seconds
        self.run_timeout_seconds = run_timeout_seconds
        self.max_calls_per_run = max_calls_per_run

    async def run(
        self,
        *,
        route: SimulationRoute = "observer",
        mode: SimulationMode = "offline",
        service: RunService | None = None,
        text_model: Any | None = None,
        allow_network: bool = False,
        memory_retriever: Any | None = None,
        attempt_ledger: AttemptLedger | None = None,
        attempt: dict[str, Any] | str | None = None,
        manifest_digest: str | None = None,
    ) -> SimulationReport:
        if route not in ROUTE_AGENDAS:
            raise ValueError(f"unknown simulation route: {route}")
        metrics = SimulationMetrics()
        attempt_id = (
            str(attempt.get("attemptId"))
            if isinstance(attempt, dict)
            else attempt
        )
        strategy_id = (
            str(attempt.get("strategyId"))
            if isinstance(attempt, dict) and attempt.get("strategyId") is not None
            else None
        )
        strategy_steps = player_strategy_steps(route, strategy_id)
        if attempt_ledger is not None and attempt_id is None:
            raise ValueError("attempt_ledger requires a planned attempt")
        if manifest_digest is None and attempt_ledger is not None:
            manifest_digest = attempt_ledger.manifest_digest
        if mode == "real":
            # Require both an explicit caller opt-in and a configured provider.
            # This check happens before creating a Run or calling the model.
            if not allow_network:
                return self._rejected(
                    route,
                    mode,
                    metrics,
                    "network_opt_in_required",
                    attempt_id=attempt_id,
                    manifest_digest=manifest_digest,
                    attempt_ledger=attempt_ledger,
                    attempt=attempt,
                )
            if text_model is None or getattr(text_model, "configured", False) is not True:
                return self._rejected(
                    route,
                    mode,
                    metrics,
                    "model_not_configured",
                    attempt_id=attempt_id,
                    manifest_digest=manifest_digest,
                    attempt_ledger=attempt_ledger,
                    attempt=attempt,
                )
        elif mode != "offline":
            raise ValueError(f"unknown simulation mode: {mode}")

        # The durable transition is intentionally before service construction
        # and before the first provider call.  A database/bootstrap failure
        # after this point is therefore still represented by a terminal row.
        if attempt_ledger is not None and attempt is not None:
            attempt_ledger.start(attempt)

        try:
            embedding_port = getattr(memory_retriever, "_embedding_port", None)
            embedding_snapshot = getattr(embedding_port, "metrics_snapshot", None)
            before_embedding_metrics = (
                embedding_snapshot()
                if callable(embedding_snapshot)
                else None
            )

            run_service = service or RunService(
                self.registry,
                text_model=text_model,
                memory_retriever=_CountingRetriever(memory_retriever, metrics)
                if memory_retriever is not None
                else None,
                seed=self.seed,
            )
            if service is not None and memory_retriever is not None:
                for agent in run_service.agents.agents.values():
                    tool = agent.memory_tool
                    original = (
                        tool.retriever.retriever
                        if isinstance(tool.retriever, _CountingRetriever)
                        else tool.retriever
                    )
                    tool.retriever = _CountingRetriever(original, metrics)
        except Exception as exc:
            safe_label = _safe_exception_label(exc)
            metrics.failures[f"runner:{safe_label}"] += 1
            metrics.abnormal_termination = safe_label
            if attempt_ledger is not None and attempt is not None:
                attempt_ledger.finish(
                    attempt,
                    "runner_failed",
                    reason=safe_label,
                    infra_valid=False,
                )
            return SimulationReport(
                route,
                mode,
                self.seed,
                metrics,
                self.budget,
                None,
                attempt_id,
                "runner_failed" if attempt_ledger is not None and attempt is not None else None,
                manifest_digest,
            )
        initial_relationships: dict[tuple[str, str], dict[str, Any]] = {}
        initial_goals: dict[str, dict[str, Any]] = {}
        original_model = None
        before_provider_metrics: dict[str, int] | None = None
        run: Any | None = None
        run_id: str | None = None
        terminal_status: ManifestStatus = "runner_failed"
        try:
            if run_service.decisions.model is not None:
                original_model = getattr(
                    run_service.decisions.model,
                    "_simulation_original_model",
                    run_service.decisions.model,
                )
                snapshot = getattr(original_model, "metrics_snapshot", None)
                if callable(snapshot):
                    before_provider_metrics = snapshot()
                counting_model = _CountingModel(
                    original_model,
                    metrics,
                    self.budget,
                    self.max_calls_per_run,
                )
                counting_model._simulation_original_model = original_model
                run_service.decisions.model = counting_model
            engine = WorldEngine(run_service)
            deadline = time.perf_counter() + self.run_timeout_seconds
            created = await asyncio.wait_for(
                run_service.create_run(ROUTE_AGENDAS[route], seed=self.seed),
                timeout=self._remaining_timeout(deadline),
            )
            run_id = str(created["runId"])
            if attempt_ledger is not None and attempt is not None:
                attempt_ledger.attach_run(attempt, run_id)
            run = await run_service.get_run_entity(run_id)
            self._check_world_budget(run, metrics)
            initial_relationships = {
                key: dict(value) for key, value in run.relationships.items()
            }
            initial_goals = {key: dict(value) for key, value in run.goals.items()}
            completed_strategy_steps: set[str] = set()
            await asyncio.wait_for(
                self._run_player_strategy_for_day(
                    run_service,
                    run,
                    route,
                    metrics,
                    day=1,
                    completed_step_ids=completed_strategy_steps,
                    strategy_steps=strategy_steps,
                ),
                timeout=self._remaining_timeout(deadline),
            )
            self._check_world_budget(run, metrics)
            await asyncio.wait_for(
                engine.step(
                    created["runId"],
                    int(540 * self.registry.real_seconds_per_virtual_minute),
                    command_id="simulation_day1_end",
                ),
                timeout=self._remaining_timeout(deadline),
            )
            self._check_world_budget(run, metrics)
            for day in range(2, self.registry.end_day + 1):
                await asyncio.wait_for(
                    engine.step(
                        created["runId"],
                        int(self.registry.real_seconds_per_virtual_minute),
                        command_id=f"simulation_day{day}_start",
                    ),
                    timeout=self._remaining_timeout(deadline),
                )
                self._check_world_budget(run, metrics)
                await asyncio.wait_for(
                    self._run_player_strategy_for_day(
                        run_service,
                        run,
                        route,
                        metrics,
                        day=day,
                        completed_step_ids=completed_strategy_steps,
                        strategy_steps=strategy_steps,
                    ),
                    timeout=self._remaining_timeout(deadline),
                )
                self._check_world_budget(run, metrics)
                await asyncio.wait_for(
                    engine.step(
                        created["runId"],
                        int(599 * self.registry.real_seconds_per_virtual_minute),
                        command_id=f"simulation_day{day}_end",
                    ),
                    timeout=self._remaining_timeout(deadline),
                )
                self._check_world_budget(run, metrics)
            terminal_status = "completed"
        except SimulationBudgetExceeded:
            metrics.budget_exhausted = True
            metrics.abnormal_termination = "budget_exhausted"
            terminal_status = "budget_exhausted"
        except TimeoutError:
            metrics.failures["runner:timeout"] += 1
            metrics.abnormal_termination = "timeout"
            terminal_status = "timeout"
        except Exception as exc:
            safe_label = _safe_exception_label(exc)
            # Provider failures are recorded by _CountingModel with a
            # protocol-qualified key.  Keep them distinct from a local
            # runner/database error for the preregistration denominator.
            provider_failure = any(
                not key.startswith(("runner:", "script:"))
                for key in metrics.failures
            )
            if provider_failure:
                terminal_status = "provider_failed"
            else:
                terminal_status = "runner_failed"
            metrics.failures[f"runner:{safe_label}"] += 1
            metrics.abnormal_termination = safe_label
        finally:
            if run is not None:
                metrics.observe_run(
                    run,
                    initial_relationships,
                    initial_goals,
                    {event.event_id for event in self.registry.events},
                )
            snapshot = getattr(original_model, "metrics_snapshot", None)
            if before_provider_metrics is not None and callable(snapshot):
                after = snapshot()
                metrics.physical_provider_requests = (
                    after["providerAttempts"]
                    - before_provider_metrics["providerAttempts"]
                )
                metrics.provider_retries = (
                    after["providerRetries"]
                    - before_provider_metrics["providerRetries"]
                )
            if before_embedding_metrics is not None and callable(embedding_snapshot):
                after_embedding = embedding_snapshot()
                metrics.embedding_provider_requests = (
                    after_embedding["completedRequests"]
                    - before_embedding_metrics["completedRequests"]
                )
                metrics.embedding_tokens = (
                    after_embedding["totalTokens"]
                    - before_embedding_metrics["totalTokens"]
                )
            if attempt_ledger is not None and attempt is not None:
                attempt_ledger.finish(
                    attempt,
                    terminal_status,
                    run_id=run_id,
                    reason=metrics.abnormal_termination or metrics.rejection_reason,
                    infra_valid=terminal_status == "completed",
                )
        return SimulationReport(
            route,
            mode,
            self.seed,
            metrics,
            self.budget,
            run_id,
            attempt_id,
            terminal_status if attempt_ledger is not None and attempt is not None else None,
            manifest_digest,
        )

    def _check_world_budget(self, run: Any, metrics: SimulationMetrics) -> None:
        message_count = sum(len(items) for items in run.messages.values())
        if message_count > self.budget.max_messages:
            metrics.budget_exhausted = True
            raise SimulationBudgetExceeded("messages")
        if len(run.conversations) > self.budget.max_conversations:
            metrics.budget_exhausted = True
            raise SimulationBudgetExceeded("conversations")

    def _remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("simulation run timeout")
        return min(self.step_timeout_seconds, remaining)

    async def _run_player_strategy_for_day(
        self,
        service: RunService,
        run: Any,
        route: SimulationRoute,
        metrics: SimulationMetrics,
        *,
        day: int,
        completed_step_ids: set[str],
        strategy_steps: tuple[PlayerStrategyStep, ...],
    ) -> None:
        for step in strategy_steps:
            if step.day != day or step.step_id in completed_step_ids:
                continue
            sent = await self._script_player_action(
                service,
                run,
                route,
                metrics,
                step=step,
            )
            if sent:
                completed_step_ids.add(step.step_id)

    async def _script_player_action(
        self,
        service: RunService,
        run: Any,
        route: SimulationRoute,
        metrics: SimulationMetrics,
        *,
        step: PlayerStrategyStep,
    ) -> bool:
        target_id = step.target_actor_id
        if run.clock.new_chat_allowed is False:
            metrics.scripted_actions["message_not_sent"] += 1
            metrics.scripted_actions[f"step_{step.step_id}_not_sent"] += 1
            return False
        conversation = next(
            (
                item
                for item in run.open_conversations()
                if target_id in item.participants
                and self.registry.player_actor_id not in item.participants
                and len(item.participants) < 3
            ),
            None,
        )
        try:
            if conversation is not None:
                result = await service.player_join(
                    run.run_id,
                    conversation.conversation_id,
                    command_id=(
                        f"simulation_player_join_{route}_{step.step_id}"
                    ),
                )
                status = result.get("joinRequest", {}).get("status")
                metrics.scripted_actions[
                    "join_sent" if status == "accepted" else "join_not_accepted"
                ] += 1
                if status != "accepted":
                    metrics.scripted_actions["message_not_sent"] += 1
                    metrics.scripted_actions[f"step_{step.step_id}_not_sent"] += 1
                    return False
                conversation_id = conversation.conversation_id
            else:
                result = await service.player_invite(
                    run.run_id,
                    target_id,
                    command_id=(
                        f"simulation_player_invite_{route}_{step.step_id}"
                    ),
                )
                invitation = result.get("invitation", {})
                if invitation.get("status") != "accepted":
                    metrics.scripted_actions["invite_not_accepted"] += 1
                    metrics.scripted_actions["message_not_sent"] += 1
                    metrics.scripted_actions[f"step_{step.step_id}_not_sent"] += 1
                    return False
                metrics.scripted_actions["invite_sent"] += 1
                conversation_id = str(invitation["conversationId"])
            await service.player_message(
                run.run_id,
                conversation_id,
                step.message,
                command_id=(
                    f"simulation_player_message_{route}_{step.step_id}"
                ),
            )
            metrics.scripted_actions["message_sent"] += 1
            metrics.scripted_actions["strategy_step_sent"] += 1
            metrics.scripted_actions[f"step_{step.step_id}_sent"] += 1
            current = run.conversations.get(conversation_id)
            if (
                current is not None
                and current.is_open
                and self.registry.player_actor_id in current.participants
            ):
                await service.remove_participant(
                    run.run_id,
                    conversation_id,
                    self.registry.player_actor_id,
                    command_id=f"simulation_player_leave_{route}_{step.step_id}",
                )
                metrics.scripted_actions["player_left"] += 1
            return True
        except Exception as exc:
            metrics.scripted_actions["message_not_sent"] += 1
            metrics.scripted_actions[f"step_{step.step_id}_not_sent"] += 1
            metrics.failures[f"script:{type(exc).__name__}"] += 1
            return False

    def _rejected(
        self,
        route: SimulationRoute,
        mode: SimulationMode,
        metrics: SimulationMetrics,
        reason: str,
        *,
        attempt_id: str | None = None,
        manifest_digest: str | None = None,
        attempt_ledger: AttemptLedger | None = None,
        attempt: dict[str, Any] | str | None = None,
    ) -> SimulationReport:
        metrics.rejected = True
        metrics.rejection_reason = reason
        if attempt_ledger is not None and attempt is not None:
            attempt_ledger.finish(
                attempt,
                "not_started",
                reason=reason,
                infra_valid=False,
            )
        return SimulationReport(
            route,
            mode,
            self.seed,
            metrics,
            self.budget,
            None,
            attempt_id,
            "not_started" if attempt_ledger is not None and attempt is not None else None,
            manifest_digest,
        )


def real_quality_gate_failures(report: SimulationReport) -> list[str]:
    """Return evidence gaps that prevent a real run from counting as accepted."""

    metrics = report.metrics
    failures: list[str] = []
    if metrics.rejected:
        failures.append("run_rejected")
    if metrics.abnormal_termination is not None:
        failures.append("abnormal_termination")
    if metrics.budget_exhausted:
        failures.append("budget_exhausted")
    if metrics.final_world_time != "Day7 18:00":
        failures.append("day7_not_reached")
    if metrics.final_day7_branch is None:
        failures.append("day7_not_resolved")
    # Private/scene events are intentionally absent from the public event
    # stream when the player did not witness them.  The authoritative
    # completion evidence is the world engine's fired/skipped event set.
    if metrics.skipped_world_event_ids:
        failures.append("world_events_incomplete")
    if metrics.conversations_created < 1:
        failures.append("no_conversation")
    if metrics.conversations_closed < 1:
        failures.append("no_closed_conversation")
    if metrics.messages < 1:
        failures.append("no_messages")
    if metrics.protocol_calls["ExitConsolidation"] < 1:
        failures.append("no_exit_consolidation")
    if metrics.memory_vector_status != "enabled":
        failures.append("embedding_not_enabled")
    if metrics.repository_recovered is not True:
        failures.append("repository_not_recovered")
    if report.mode == "real" and metrics.temporary_run_deleted is not True:
        failures.append("temporary_run_not_deleted")
    if report.route != "observer":
        if metrics.player_speech_count < 1:
            failures.append("player_message_not_sent")
        if metrics.chapter_stance_changes < 1:
            failures.append("no_chapter_stance_change")
        if metrics.player_result is None:
            failures.append("player_result_missing")
    if report.route == "pro_lin":
        if metrics.player_speech_count < 5:
            failures.append("coalition_strategy_incomplete")
        if metrics.chapter_stance_changes < 3:
            failures.append("coalition_not_formed")
        if metrics.final_day7_branch not in {
            "compromise_submitted",
            "consensus_submitted",
        }:
            failures.append("success_branch_not_reached")
        if metrics.player_result not in {"completed", "partial"}:
            failures.append("support_task_not_completed")
    elif report.route == "pro_zhao":
        if metrics.final_day7_branch != "no_submission":
            failures.append("failure_control_changed_branch")
        if metrics.player_result != "failed":
            failures.append("failure_control_not_failed")
    return failures


__all__ = [
    "DEFAULT_EMBEDDING_CNY_PER_MILLION",
    "DEFAULT_SIMULATION_SEED",
    "DEFAULT_TEXT_INPUT_CNY_PER_MILLION",
    "DEFAULT_TEXT_OUTPUT_CNY_PER_MILLION",
    "PlayerStrategyStep",
    "ROUTE_PLAYER_MESSAGES",
    "ROUTE_PLAYER_STEPS",
    "SimulationBudget",
    "SimulationMetrics",
    "SimulationMode",
    "SimulationReport",
    "SimulationRoute",
    "SevenDaySimulationRunner",
    "real_quality_gate_failures",
]
