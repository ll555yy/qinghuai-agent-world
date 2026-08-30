"""Best-effort in-process event fan-out for WebSocket clients."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventHub:
    """Publish each Run event to currently connected subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._pending: dict[
            str,
            dict[int, tuple[dict[str, Any], tuple[asyncio.Queue[dict[str, Any]], ...]]],
        ] = defaultdict(dict)
        self._last_published_seq: dict[str, int] = defaultdict(int)

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(run_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)

    async def prime(self, run_id: str, persisted_event_seq: int) -> None:
        """Set the replay baseline when a persisted Run is loaded after restart."""

        async with self._lock:
            if self._last_published_seq[run_id] == 0 and not self._pending[run_id]:
                self._last_published_seq[run_id] = max(0, int(persisted_event_seq))

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            sequence = int(event.get("eventSeq", 0))
            if sequence <= self._last_published_seq[run_id]:
                return
            subscribers = tuple(self._subscribers.get(run_id, ()))
            self._pending[run_id][sequence] = (event, subscribers)
            next_sequence = self._last_published_seq[run_id] + 1
            while next_sequence in self._pending[run_id]:
                ordered, event_subscribers = self._pending[run_id].pop(next_sequence)
                for queue in event_subscribers:
                    queue.put_nowait(ordered)
                self._last_published_seq[run_id] = next_sequence
                next_sequence += 1
