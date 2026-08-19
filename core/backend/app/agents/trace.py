"""Private, in-process traces for Agent Graph diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from .models import AgentEventType


@dataclass(frozen=True, slots=True)
class AgentTrace:
    trace_id: str
    run_id: str
    npc_id: str
    event_type: AgentEventType
    conversation_id: str | None
    node_path: tuple[str, ...]
    tool_used: bool
    tool_result_count: int
    final_action: str | None
    duration_ms: float
    failure_code: str | None = None


class AgentTraceSink(Protocol):
    """Sink used by the runtime; implementations must not publish traces."""

    def record(self, trace: AgentTrace) -> None:
        ...


class InMemoryAgentTraceSink:
    """A small test/diagnostic sink with a snapshot-only read API."""

    def __init__(self) -> None:
        self._traces: list[AgentTrace] = []
        self._lock = Lock()

    def record(self, trace: AgentTrace) -> None:
        with self._lock:
            self._traces.append(trace)

    def snapshot(self) -> tuple[AgentTrace, ...]:
        with self._lock:
            return tuple(self._traces)

    def __iter__(self) -> Iterable[AgentTrace]:
        return iter(self.snapshot())


__all__ = ["AgentTrace", "AgentTraceSink", "InMemoryAgentTraceSink"]
