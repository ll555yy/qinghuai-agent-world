"""The in-memory, owner-scoped Agent memory tool.

This is intentionally a small explicit tool node rather than a generic ReAct
loop.  Its input schema is the same ``MemoryQuery`` the model already uses;
runtime ownership and snapshots arrive through ``MemoryToolContext`` and are
never model-controlled.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ..ai.protocols import MemoryQuery
from .models import MemoryToolContext, MemoryToolResult


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


class RetrieveOwnedMemoriesTool:
    """Read-only retrieval over a caller-provided memory snapshot.

    ``ownerNpcId`` is deliberately absent from ``args_schema``.  The explicit
    LangGraph node passes the context separately, and the tool refuses a
    context whose owner does not match the Agent currently executing.
    """

    name: ClassVar[str] = "retrieve_owned_memories"
    description: ClassVar[str] = (
        "按当前 NPC 自己的角色、主题、目标和关键词检索私有长期记忆。"
        "只能返回当前 NPC 的记忆。"
    )
    args_schema: ClassVar[type[MemoryQuery]] = MemoryQuery

    def __init__(self, *, bound_owner_npc_id: str | None = None) -> None:
        self.bound_owner_npc_id = bound_owner_npc_id

    def invoke(
        self,
        query: MemoryQuery | Mapping[str, Any],
        *,
        context: MemoryToolContext,
        agent_npc_id: str,
    ) -> MemoryToolResult:
        """Validate and execute one read-only query.

        The separate ``agent_npc_id`` argument is another runtime-owned value;
        it prevents a graph state accidentally carrying a context for a
        different logical Agent.
        """

        if context.owner_npc_id != agent_npc_id:
            raise ValueError("memory owner does not match the bound Agent")
        if self.bound_owner_npc_id is not None and self.bound_owner_npc_id != agent_npc_id:
            raise ValueError("memory tool is bound to another Agent")
        validated = query if isinstance(query, MemoryQuery) else self.args_schema.model_validate(query)
        return self._search(validated, context)

    async def ainvoke(
        self,
        query: MemoryQuery | Mapping[str, Any],
        *,
        context: MemoryToolContext,
        agent_npc_id: str,
    ) -> MemoryToolResult:
        return self.invoke(query, context=context, agent_npc_id=agent_npc_id)

    @staticmethod
    def _search(query: MemoryQuery, context: MemoryToolContext) -> MemoryToolResult:
        query_tokens = set(query.query_text.lower().split())
        query_topic_ids = set(
            RetrieveOwnedMemoriesTool._resolve_topic_hints(query.topic_hints, context.topics)
        )
        result: list[tuple[int, int, str]] = []
        for memory_id, memory in context.memories.items():
            if _value(memory, "ownerNpcId", _value(memory, "owner_npc_id")) != context.owner_npc_id:
                continue
            actor_ids = _value(memory, "actorIds", _value(memory, "actor_ids", ()))
            topic_ids = _value(memory, "topicIds", _value(memory, "topic_ids", ()))
            goal_ids = _value(memory, "goalIds", _value(memory, "goal_ids", ()))
            content = _value(memory, "content", "")
            actor_hit = len(set(query.actor_ids) & set(actor_ids or ()))
            topic_hit = len(query_topic_ids & set(topic_ids or ()))
            goal_hit = len(set(query.goal_ids) & set(goal_ids or ()))
            text_hit = sum(
                1 for token in query_tokens if token and token in str(content).lower()
            )
            score = actor_hit * 5 + topic_hit * 4 + goal_hit * 4 + text_hit * 2
            if score or not (
                query_tokens or query.actor_ids or query.topic_hints or query.goal_ids
            ):
                importance = int(_value(memory, "importance", 1) or 1)
                result.append((score, importance, str(memory_id)))
        result.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return MemoryToolResult(tuple(item[2] for item in result[: query.limit]))

    @staticmethod
    def _resolve_topic_hints(
        hints: list[str], topics: Mapping[str, Any]
    ) -> list[str]:
        resolved: list[str] = []
        for topic_id, topic in topics.items():
            topic_name = _value(topic, "name", "")
            aliases = _value(topic, "aliases", ()) or ()
            if (
                topic_id in hints
                or topic_name in hints
                or any(alias in hints for alias in aliases)
            ):
                resolved.append(str(topic_id))
        return resolved


__all__ = ["RetrieveOwnedMemoriesTool"]
