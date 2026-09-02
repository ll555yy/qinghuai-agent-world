"""LangGraph runtime for the five logical NPC Agents."""

from __future__ import annotations

import inspect
import time
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from ..ai.decision_service import DecisionService
from ..ai.protocols import (
    ChatDecision,
    DailyActionDecision,
    InvitationDecision,
    SpeechGeneration,
)
from .memory_tool import RetrieveOwnedMemoriesTool
from .models import (
    AgentDecision,
    AgentEventType,
    AgentGraphState,
    AgentInvocation,
    AgentResult,
    DecisionPolicy,
    PublicDecisionContext,
    invocation_state,
)
from .trace import AgentTrace, AgentTraceSink, InMemoryAgentTraceSink


class NPCAgentRuntime:
    """Shared model adapter and one compiled StateGraph.

    Five :class:`NPCAgent` instances can use this runtime.  The graph is
    compiled in ``__init__`` exactly once and every invocation receives fresh
    state, so one Agent's private context cannot become another's state.
    """

    def __init__(
        self,
        decisions: DecisionService,
        *,
        trace_sink: AgentTraceSink | None = None,
        memory_tool_factory: Any | None = None,
        decision_policy: DecisionPolicy | None = None,
    ) -> None:
        self.decisions = decisions
        self.trace_sink = trace_sink or InMemoryAgentTraceSink()
        self._memory_tool_factory = memory_tool_factory
        self._decision_policy = decision_policy
        self.graph = self._build_graph().compile()
        # ``compiled_graph`` is an explicit alias used by diagnostics and
        # integration code; it points to the same compiled object.
        self.compiled_graph = self.graph

    def create_agent(
        self,
        actor_id: str,
        *,
        memory_tool: RetrieveOwnedMemoriesTool | None = None,
    ) -> NPCAgent:
        tool = memory_tool or self._new_memory_tool(actor_id)
        return NPCAgent(runtime=self, actor_id=actor_id, memory_tool=tool)

    def _new_memory_tool(self, actor_id: str) -> RetrieveOwnedMemoriesTool:
        if self._memory_tool_factory is not None:
            tool = self._memory_tool_factory(actor_id)
            if not isinstance(tool, RetrieveOwnedMemoriesTool):
                raise TypeError("memory_tool_factory must return RetrieveOwnedMemoriesTool")
            return tool
        return RetrieveOwnedMemoriesTool(bound_owner_npc_id=actor_id)

    async def _invoke(self, agent: NPCAgent, invocation: AgentInvocation) -> AgentResult:
        if invocation.npc_id != agent.actor_id:
            raise ValueError("invocation NPC does not match Agent binding")
        trace_id = uuid4().hex
        started = time.perf_counter()
        state = invocation_state(
            invocation,
            trace_id=trace_id,
            memory_tool=agent.memory_tool,
        )
        if agent.recall_was_used_for(invocation):
            state["recall_used"] = True
        try:
            output: dict[str, Any]
            if self._decision_policy is not None:
                projected = PublicDecisionContext(
                    run_id=invocation.run_id,
                    npc_id=invocation.npc_id,
                    event_type=invocation.event_type,
                    conversation_id=invocation.conversation_id,
                    trigger_message_id=invocation.trigger_message_id,
                    candidate_actor_ids=tuple(invocation.candidate_actor_ids),
                    visible_messages=tuple(deepcopy(item) for item in invocation.visible_messages),
                )
                policy_result = self._decision_policy(projected)
                policy_decision = (
                    await policy_result if inspect.isawaitable(policy_result) else policy_result
                )
                if not isinstance(
                    policy_decision, (DailyActionDecision, InvitationDecision, ChatDecision)
                ):
                    raise TypeError("decision_policy returned an unsupported decision")
                output = {
                    "final_output": policy_decision,
                    "decision": policy_decision,
                    "node_path": ("public_decision_policy", "finalize"),
                }
            else:
                output = dict(await self.graph.ainvoke(state))
            if output.get("tool_used", False):
                agent.mark_recall_used_for(invocation)
            decision = output.get("final_output") or output.get("decision")
            if not isinstance(decision, (DailyActionDecision, InvitationDecision, ChatDecision)):
                decision = self._fallback_for(invocation.event_type)
                output["failure_code"] = output.get("failure_code") or "model_fallback"
            result = AgentResult(
                decision=decision,
                trace_id=trace_id,
                recalled_memory_ids=tuple(output.get("recalled_memory_ids", ())),
                draft_changes=deepcopy(output.get("draft_changes", {})),
                node_path=tuple(output.get("node_path", ())),
                tool_used=bool(output.get("tool_used", False)),
                tool_result_count=int(output.get("tool_result_count", 0)),
                failure_code=output.get("failure_code"),
            )
        except Exception:
            # The graph has a narrow normal fallback so a provider or graph
            # construction error cannot corrupt the world aggregate.
            result = AgentResult(
                decision=self._fallback_for(invocation.event_type),
                trace_id=trace_id,
                node_path=("safe_wait", "finalize"),
                failure_code="model_fallback",
            )
        self.trace_sink.record(
            AgentTrace(
                trace_id=trace_id,
                run_id=invocation.run_id,
                npc_id=invocation.npc_id,
                event_type=invocation.event_type,
                conversation_id=invocation.conversation_id,
                node_path=result.node_path,
                tool_used=result.tool_used,
                tool_result_count=result.tool_result_count,
                final_action=self._final_action(result.decision),
                duration_ms=(time.perf_counter() - started) * 1000,
                failure_code=result.failure_code,
            )
        )
        return result

    async def generate_speech(self, agent: NPCAgent, prompt: str) -> SpeechGeneration:
        """Generate speech only through the Agent selected by the scheduler."""

        if not prompt.strip():
            raise ValueError("speech prompt cannot be empty")
        return await self.decisions.speech(prompt)

    @staticmethod
    def _final_action(decision: AgentDecision) -> str:
        if isinstance(decision, InvitationDecision):
            return decision.decision
        if isinstance(decision, (DailyActionDecision, ChatDecision)):
            return decision.action or decision.result
        return "wait"

    @staticmethod
    def _fallback_for(event_type: AgentEventType) -> AgentDecision:
        if event_type == "daily_tick":
            return DailyActionDecision(action="wait")
        if event_type == "invitation_received":
            return InvitationDecision(decision="refuse")
        return ChatDecision(result="decided", action="wait")

    def _build_graph(self) -> StateGraph[AgentGraphState]:
        graph: StateGraph[AgentGraphState] = StateGraph(AgentGraphState)
        graph.add_node("route_event", self._route_event)
        graph.add_node("daily_decision", self._daily_decision)
        graph.add_node("invitation_decision", self._invitation_decision)
        graph.add_node("chat_decision", self._chat_decision)
        graph.add_node("retrieve_owned_memories", self._retrieve_owned_memories)
        graph.add_node("chat_after_recall", self._chat_after_recall)
        graph.add_node("safe_wait", self._safe_wait)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "route_event")
        graph.add_conditional_edges(
            "route_event",
            self._event_route,
            {
                "daily_tick": "daily_decision",
                "invitation_received": "invitation_decision",
                "chat_message_received": "chat_decision",
            },
        )
        graph.add_edge("daily_decision", "finalize")
        graph.add_edge("invitation_decision", "finalize")
        graph.add_conditional_edges(
            "chat_decision",
            self._chat_route,
            {
                "decided": "finalize",
                "need_memory": "retrieve_owned_memories",
                "safe_wait": "safe_wait",
            },
        )
        graph.add_conditional_edges(
            "retrieve_owned_memories",
            self._recall_route,
            {
                "results": "chat_after_recall",
                "empty": "safe_wait",
                "error": "safe_wait",
            },
        )
        graph.add_conditional_edges(
            "chat_after_recall",
            self._post_recall_route,
            {"decided": "finalize", "need_memory": "safe_wait", "safe_wait": "safe_wait"},
        )
        graph.add_edge("safe_wait", "finalize")
        graph.add_edge("finalize", END)
        return graph

    @staticmethod
    def _append_path(state: AgentGraphState, node: str) -> tuple[str, ...]:
        return tuple(state.get("node_path", ())) + (node,)

    async def _route_event(self, state: AgentGraphState) -> dict[str, Any]:
        return {"node_path": self._append_path(state, "route_event")}

    @staticmethod
    def _event_route(state: AgentGraphState) -> AgentEventType:
        event_type = state.get("event_type")
        if event_type not in {"daily_tick", "invitation_received", "chat_message_received"}:
            raise ValueError("unknown Agent event")
        return event_type

    @staticmethod
    def _chat_route(state: AgentGraphState) -> Literal["decided", "need_memory", "safe_wait"]:
        decision = state.get("decision")
        if not isinstance(decision, ChatDecision):
            return "safe_wait"
        if decision.result == "need_memory":
            return "safe_wait" if state.get("recall_used", False) else "need_memory"
        if decision.result == "decided":
            return "decided"
        return "safe_wait"

    @staticmethod
    def _recall_route(state: AgentGraphState) -> Literal["results", "empty", "error"]:
        failure = state.get("failure_code")
        if failure == "tool_error":
            return "error"
        if state.get("recalled_memory_ids"):
            return "results"
        return "empty"

    @staticmethod
    def _post_recall_route(
        state: AgentGraphState,
    ) -> Literal["decided", "need_memory", "safe_wait"]:
        decision = state.get("decision")
        if isinstance(decision, ChatDecision) and decision.result == "need_memory":
            return "need_memory"
        if isinstance(decision, ChatDecision) and decision.result == "decided":
            return "decided"
        return "safe_wait"

    async def _daily_decision(self, state: AgentGraphState) -> dict[str, Any]:
        decision = await self.decisions.daily_action(state["prompt"])
        return self._decision_update(state, decision, "daily_decision", "DailyActionDecision")

    async def _invitation_decision(self, state: AgentGraphState) -> dict[str, Any]:
        decision = await self.decisions.invitation(state["prompt"])
        return self._decision_update(
            state, decision, "invitation_decision", "InvitationDecision"
        )

    async def _chat_decision(self, state: AgentGraphState) -> dict[str, Any]:
        decision = await self.decisions.chat(state["prompt"])
        return self._decision_update(state, decision, "chat_decision", "ChatDecision")

    async def _chat_after_recall(self, state: AgentGraphState) -> dict[str, Any]:
        prompt = state["prompt"]
        builder = state.get("prompt_builder")
        recalled = list(dict.fromkeys((*state.get("memory_cache", ()), *state.get("recalled_memory_ids", ()))))
        if builder is not None:
            prompt = builder(recalled)
        else:
            prompt = f"{prompt}\n已召回私有记忆标识：{', '.join(recalled)}"
        decision = await self.decisions.chat(prompt)
        return self._decision_update(
            state,
            decision,
            "chat_after_recall",
            "ChatDecision",
            prompt=prompt,
        )

    def _decision_update(
        self,
        state: AgentGraphState,
        decision: AgentDecision,
        node: str,
        protocol: str,
        *,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        failure = state.get("failure_code")
        if self.decisions.last_failed_protocol == protocol:
            failure = failure or "model_fallback"
        return {
            "decision": decision,
            "final_output": decision,
            "draft_changes": self._draft_changes(decision),
            "node_path": self._append_path(state, node),
            "failure_code": failure,
            **({"prompt": prompt} if prompt is not None else {}),
        }

    async def _retrieve_owned_memories(self, state: AgentGraphState) -> dict[str, Any]:
        path = self._append_path(state, "retrieve_owned_memories")
        if state.get("recall_used", False):
            return {"node_path": path, "failure_code": "recall_limit"}
        context = state.get("memory_tool_context")
        decision = state.get("decision")
        tool = state.get("memory_tool")
        if not isinstance(decision, ChatDecision) or decision.result != "need_memory":
            return {"node_path": path, "failure_code": "recall_limit"}
        if context is None or tool is None or decision.memory_query is None:
            return {
                "node_path": path,
                "recall_used": True,
                "tool_used": True,
                "failure_code": "tool_error",
            }
        try:
            result = await tool.ainvoke(
                decision.memory_query,
                context=context,
                agent_npc_id=state["npc_id"],
            )
        except Exception:
            return {
                "node_path": path,
                "recall_used": True,
                "tool_used": True,
                "failure_code": "tool_error",
            }
        ids = tuple(result.memory_ids)
        return {
            "node_path": path,
            "recall_used": True,
            "tool_used": True,
            "tool_result_count": len(ids),
            "recalled_memory_ids": ids,
            "failure_code": None if ids else "tool_empty",
        }

    async def _safe_wait(self, state: AgentGraphState) -> dict[str, Any]:
        decision = state.get("decision")
        failure = state.get("failure_code")
        if failure is None and isinstance(decision, ChatDecision):
            failure = "recall_limit" if decision.result == "need_memory" else "tool_empty"
        failure = failure or "tool_empty"
        decision = ChatDecision(result="decided", action="wait")
        return {
            "node_path": self._append_path(state, "safe_wait"),
            "decision": decision,
            "final_output": decision,
            "draft_changes": {},
            "failure_code": failure,
        }

    async def _finalize(self, state: AgentGraphState) -> dict[str, Any]:
        decision = state.get("decision") or state.get("final_output")
        if decision is None:
            decision = self._fallback_for(state["event_type"])
        return {
            "node_path": self._append_path(state, "finalize"),
            "decision": decision,
            "final_output": decision,
            "draft_changes": self._draft_changes(decision),
        }

    @staticmethod
    def _draft_changes(decision: AgentDecision | SpeechGeneration) -> Mapping[str, Any]:
        if not isinstance(decision, ChatDecision):
            return {}
        return {
            "goal_updates": tuple(decision.goal_updates),
            "relationship_updates": tuple(decision.relationship_updates),
            "pending_goal": decision.pending_goal,
            "chapter_effects": tuple(decision.chapter_effects),
        }


class NPCAgent:
    """One logical NPC identity backed by a shared ``NPCAgentRuntime``."""

    def __init__(
        self,
        *,
        runtime: NPCAgentRuntime,
        actor_id: str,
        memory_tool: RetrieveOwnedMemoriesTool,
    ) -> None:
        if not actor_id:
            raise ValueError("actor_id cannot be empty")
        self.runtime = runtime
        self.actor_id = actor_id
        self.memory_tool = memory_tool
        self._recalled_triggers: set[tuple[str, str | None, str]] = set()

    @staticmethod
    def _recall_key(
        invocation: AgentInvocation,
    ) -> tuple[str, str | None, str] | None:
        if invocation.trigger_message_id is None:
            return None
        return (
            invocation.run_id,
            invocation.conversation_id,
            invocation.trigger_message_id,
        )

    def recall_was_used_for(self, invocation: AgentInvocation) -> bool:
        key = self._recall_key(invocation)
        return key is not None and key in self._recalled_triggers

    def mark_recall_used_for(self, invocation: AgentInvocation) -> None:
        key = self._recall_key(invocation)
        if key is not None:
            self._recalled_triggers.add(key)

    def _check(self, invocation: AgentInvocation, event_type: AgentEventType) -> None:
        if invocation.npc_id != self.actor_id:
            raise ValueError("invocation NPC does not match Agent binding")
        if invocation.event_type != event_type:
            raise ValueError("invocation event does not match Agent entrypoint")

    async def daily_tick(self, invocation: AgentInvocation) -> AgentResult:
        self._check(invocation, "daily_tick")
        return await self.runtime._invoke(self, invocation)

    async def invitation_received(self, invocation: AgentInvocation) -> AgentResult:
        self._check(invocation, "invitation_received")
        return await self.runtime._invoke(self, invocation)

    async def chat_message_received(self, invocation: AgentInvocation) -> AgentResult:
        self._check(invocation, "chat_message_received")
        return await self.runtime._invoke(self, invocation)

    async def generate_speech(self, prompt: str) -> SpeechGeneration:
        return await self.runtime.generate_speech(self, prompt)


class NPCAgentRegistry:
    """Build five (or any scenario-sized number of) logical Agent objects."""

    def __init__(
        self,
        runtime: NPCAgentRuntime,
        actor_ids: Sequence[str],
    ) -> None:
        unique_ids = tuple(dict.fromkeys(actor_ids))
        if len(unique_ids) != len(tuple(actor_ids)):
            raise ValueError("actor_ids must be unique")
        self.runtime = runtime
        self._agents = {actor_id: runtime.create_agent(actor_id) for actor_id in unique_ids}

    @classmethod
    def from_decisions(
        cls,
        decisions: DecisionService,
        actor_ids: Sequence[str],
        *,
        trace_sink: AgentTraceSink | None = None,
    ) -> NPCAgentRegistry:
        return cls(NPCAgentRuntime(decisions, trace_sink=trace_sink), actor_ids)

    @property
    def agents(self) -> Mapping[str, NPCAgent]:
        return self._agents

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return tuple(self._agents)

    def get(self, actor_id: str) -> NPCAgent:
        try:
            return self._agents[actor_id]
        except KeyError as exc:
            raise KeyError(f"unknown NPC Agent: {actor_id}") from exc

    def __getitem__(self, actor_id: str) -> NPCAgent:
        return self.get(actor_id)

    def __iter__(self) -> Iterator[NPCAgent]:
        return iter(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)


__all__ = ["NPCAgent", "NPCAgentRegistry", "NPCAgentRuntime"]
