"""Provider-neutral state and value objects for NPC Agent execution.

The objects in this module deliberately describe one invocation only.  They do
not contain a ``Run`` reference and are therefore not able to mutate world
state.  ``RunService`` creates an invocation from its read-only projections and
applies the resulting semantic decision after the graph has finished.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from ..ai.protocols import (
    ChatDecision,
    DailyActionDecision,
    InvitationDecision,
    SpeechGeneration,
)

AgentEventType = Literal[
    "daily_tick",
    "invitation_received",
    "chat_message_received",
]

AgentDecision = DailyActionDecision | InvitationDecision | ChatDecision
PromptBuilder = Callable[[list[str]], str]


@dataclass(frozen=True, slots=True)
class MemoryToolContext:
    """Runtime-only data injected into a private memory tool call.

    ``owner_npc_id`` is intentionally not part of :class:`MemoryQuery`.  It
    comes from the Agent binding and cannot be supplied by model output.  The
    snapshots are copied by the runtime before entering Graph State, so a tool
    cannot mutate the authoritative run dictionaries.
    """

    owner_npc_id: str
    run_id: str
    conversation_id: str | None = None
    memories: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    topics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryToolResult:
    """The small, serializable result carried through Graph State."""

    memory_ids: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.memory_ids)


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    """Input for one logical NPC Agent event."""

    run_id: str
    npc_id: str
    event_type: AgentEventType
    prompt: str
    conversation_id: str | None = None
    trigger_message_id: str | None = None
    candidate_actor_ids: tuple[str, ...] = ()
    visible_messages: tuple[Mapping[str, Any], ...] = ()
    memory_cache: tuple[str, ...] = ()
    memory_context: MemoryToolContext | None = None
    prompt_builder: PromptBuilder | None = None


class AgentGraphState(TypedDict, total=False):
    """Single-invocation state shared by the compiled LangGraph.

    The graph only returns partial updates to these fields.  It is never
    persisted with a checkpointer and is never exposed through a public API.
    ``decision`` and ``final_output`` are protocol objects, while
    ``draft_changes`` is an opaque semantic copy for the world layer.
    """

    trace_id: str
    run_id: str
    conversation_id: str | None
    npc_id: str
    event_type: AgentEventType
    trigger_message_id: str | None
    candidate_actor_ids: tuple[str, ...]
    visible_messages: tuple[Mapping[str, Any], ...]
    memory_cache: tuple[str, ...]
    recall_used: bool
    prompt: str
    prompt_builder: PromptBuilder | None
    memory_tool_context: MemoryToolContext | None
    memory_tool: Any
    decision: AgentDecision | None
    draft_changes: Mapping[str, Any]
    recalled_memory_ids: tuple[str, ...]
    final_output: AgentDecision | SpeechGeneration | None
    node_path: tuple[str, ...]
    tool_used: bool
    tool_result_count: int
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Public-to-RunService result of a graph invocation."""

    decision: AgentDecision
    trace_id: str
    recalled_memory_ids: tuple[str, ...] = ()
    draft_changes: Mapping[str, Any] = field(default_factory=dict)
    node_path: tuple[str, ...] = ()
    tool_used: bool = False
    tool_result_count: int = 0
    failure_code: str | None = None

    @property
    def final_output(self) -> AgentDecision:
        return self.decision


def invocation_state(
    invocation: AgentInvocation,
    *,
    trace_id: str,
    memory_tool: Any,
) -> AgentGraphState:
    """Make an isolated Graph State snapshot from an invocation.

    ``deepcopy`` is intentionally used for visible messages and memory
    context.  The graph is then free to pass these values between nodes while
    retaining the world aggregate as the sole source of truth.
    """

    from copy import deepcopy

    context = deepcopy(invocation.memory_context)
    return {
        "trace_id": trace_id,
        "run_id": invocation.run_id,
        "conversation_id": invocation.conversation_id,
        "npc_id": invocation.npc_id,
        "event_type": invocation.event_type,
        "trigger_message_id": invocation.trigger_message_id,
        "candidate_actor_ids": tuple(invocation.candidate_actor_ids),
        "visible_messages": tuple(deepcopy(item) for item in invocation.visible_messages),
        "memory_cache": tuple(invocation.memory_cache),
        "recall_used": False,
        "prompt": invocation.prompt,
        "prompt_builder": invocation.prompt_builder,
        "memory_tool_context": context,
        "memory_tool": memory_tool,
        "decision": None,
        "draft_changes": {},
        "recalled_memory_ids": (),
        "final_output": None,
        "node_path": (),
        "tool_used": False,
        "tool_result_count": 0,
        "failure_code": None,
    }


__all__ = [
    "AgentDecision",
    "AgentEventType",
    "AgentGraphState",
    "AgentInvocation",
    "AgentResult",
    "MemoryToolContext",
    "MemoryToolResult",
    "PromptBuilder",
    "invocation_state",
]
