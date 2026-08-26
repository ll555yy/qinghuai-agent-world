"""In-memory Run aggregate and its public projection."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .clock import WorldClock
from .conversation import Conversation

ConversationRoundState = dict[str, Any]


def new_conversation_round_state(
    *,
    round_id: int = 0,
    segment_id: str | None = None,
    participant_version: int = 0,
) -> ConversationRoundState:
    """Return the durable baseline for one conversation's message round.

    Round state intentionally remains JSON-shaped.  The orchestrator can add
    provider-specific recovery details to ``recovery`` or publication records
    without changing the aggregate schema, while these baseline keys provide
    a stable contract for old and new callers.  ``asyncio.Lock`` instances and
    tasks live in the separate runtime registries on :class:`Run`.
    """

    return {
        "roundId": round_id,
        "status": "idle",
        "roundVersion": 0,
        "triggerMessageIds": [],
        "queuedMessageIds": [],
        "segmentId": segment_id,
        "participantVersion": int(participant_version),
        "cooldownDueAt": None,
        "finalCheckUsed": False,
        "pendingPublications": [],
        "pendingLeaverIds": [],
        "pendingPostSpeechLeaverIds": [],
        "npcOnlyRounds": 0,
        "openerActorId": None,
        "openerKind": None,
        "awaitingPlayerOpener": False,
        "recovery": {
            "resumeStatus": None,
            "attempt": 0,
            "publishedMessageIds": [],
        },
    }


@dataclass(slots=True)
class RunEvent:
    run_id: str
    event_seq: int
    state_version: int
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "eventSeq": self.event_seq,
            "stateVersion": self.state_version,
            "eventType": self.event_type,
            "payload": deepcopy(self.payload),
        }


@dataclass(slots=True)
class CommandRecord:
    fingerprint: str
    result: dict[str, Any]


@dataclass(slots=True)
class Run:
    run_id: str
    player_agenda_id: str | None
    clock: WorldClock
    seed: int = 0
    state_version: int = 0
    event_seq: int = 0
    next_conversation_seq: int = 0
    actor_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[str, Conversation] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)
    command_records: dict[str, CommandRecord] = field(default_factory=dict)
    # The fields below are authoritative in-memory state for the playable
    # chapter.  They are intentionally not copied wholesale to clients.
    positions: dict[str, dict[str, int]] = field(default_factory=dict)
    daily_think_minutes: dict[str, int] = field(default_factory=dict)
    # The seed-derived order and the expanded per-day schedule are kept on
    # the Run.  ``daily_think_minutes`` is the schedule for the current day
    # (and is refreshed when a new world day starts) for callers that need a
    # cheap lookup; the two fields below are the durable source for replay.
    daily_think_order: list[str] = field(default_factory=list)
    daily_think_schedule: dict[int, dict[str, int]] = field(default_factory=dict)
    thought_days: dict[str, set[int]] = field(default_factory=dict)
    goals: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    memories: dict[str, dict[str, Any]] = field(default_factory=dict)
    memory_links: list[dict[str, Any]] = field(default_factory=list)
    memory_cache: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    world_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    fresh_event_context: dict[str, list[str]] = field(default_factory=dict)
    actor_world_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    scene_state: dict[str, Any] = field(default_factory=dict)
    current_world_state: dict[str, Any] = field(default_factory=dict)
    invitations: dict[str, dict[str, Any]] = field(default_factory=dict)
    join_requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    segments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conversation_drafts: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    idle_counts: dict[str, int] = field(default_factory=dict)
    consolidation_status: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    chapter_actor_stances: dict[str, str] = field(default_factory=dict)
    zhou_authorization: str = "none"
    chapter_agenda_stances: dict[tuple[str, str], str] = field(default_factory=dict)
    chapter_resolution: dict[str, Any] | None = None
    fired_event_ids: set[str] = field(default_factory=set)
    next_message_seq: int = 0
    next_segment_seq: int = 0
    next_invitation_seq: int = 0
    next_join_request_seq: int = 0
    next_memory_seq: int = 0
    run_finished: bool = False
    # Ordinary day-end handling is idempotent.  This also prevents a second
    # world command issued while the clock is parked at 18:00 from repeating
    # consolidation or emitting duplicate close events.
    closed_days: set[int] = field(default_factory=set)
    # Chat model calls temporarily release ``lock`` so a concurrent world
    # step can reach the hard day boundary.  These counters let the boundary
    # defer close/consolidate until the already-authorized chat call returns.
    active_chat_pipelines: int = 0
    in_flight_speech_calls: int = 0
    pending_day_end: tuple[int, str] | None = None
    pending_chapter_event_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Durable per-conversation round state.  Values are deliberately plain
    # JSON-shaped mappings so a round can be resumed after a process restart.
    # Runtime locks/tasks are kept in the registries below and are never part
    # of this mapping or of the storage codec.  These fields are at the end of
    # the dataclass to keep legacy positional construction stable.
    conversation_round_states: dict[str, ConversationRoundState] = field(default_factory=dict)
    conversation_round_locks: dict[str, asyncio.Lock] = field(
        default_factory=dict,
        repr=False,
    )
    conversation_round_tasks: dict[str, asyncio.Task[Any]] = field(
        default_factory=dict,
        repr=False,
    )

    def ensure_conversation_round_state(self, conversation_id: str) -> ConversationRoundState:
        """Return and lazily initialize durable state for ``conversation_id``.

        Legacy runs do not have a round-state entry.  Initializing on demand
        keeps those snapshots compatible while allowing the message-driven
        orchestrator to use one consistent state shape.
        """

        state = self.conversation_round_states.get(conversation_id)
        if state is None:
            state = new_conversation_round_state()
            self.conversation_round_states[conversation_id] = state
        return state

    def conversation_round_lock(self, conversation_id: str) -> asyncio.Lock:
        """Return the process-local lock for one conversation.

        Locks are recreated lazily after deserialization, and therefore never
        need a persistence representation.  Keeping one lock per conversation
        permits provider waits in different conversations to overlap while
        still serializing transitions within a single conversation.
        """

        lock = self.conversation_round_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self.conversation_round_locks[conversation_id] = lock
        return lock

    # A descriptive alias for callers that prefer an explicit ``get`` name.
    get_conversation_round_lock = conversation_round_lock

    def next_conversation_identity(self) -> tuple[str, int]:
        self.next_conversation_seq += 1
        return f"conv_{self.next_conversation_seq:06d}", self.next_conversation_seq

    def next_message_identity(self) -> str:
        self.next_message_seq += 1
        return f"msg_{self.next_message_seq:06d}"

    def next_segment_identity(self) -> str:
        self.next_segment_seq += 1
        return f"seg_{self.next_segment_seq:06d}"

    def next_invitation_identity(self) -> str:
        self.next_invitation_seq += 1
        return f"invite_{self.next_invitation_seq:06d}"

    def next_join_request_identity(self) -> str:
        self.next_join_request_seq += 1
        return f"join_{self.next_join_request_seq:06d}"

    def next_memory_identity(self) -> str:
        self.next_memory_seq += 1
        return f"memory_{self.next_memory_seq:06d}"

    def open_conversations(self) -> list[Conversation]:
        return [conversation for conversation in self.conversations.values() if conversation.is_open]

    def actor_open_conversation(self, actor_id: str) -> Conversation | None:
        for conversation in self.open_conversations():
            if conversation.has_participant(actor_id):
                return conversation
        return None

    def append_event(self, event_type: str, payload: dict[str, Any]) -> RunEvent:
        self.state_version += 1
        self.event_seq += 1
        event = RunEvent(
            run_id=self.run_id,
            event_seq=self.event_seq,
            state_version=self.state_version,
            event_type=event_type,
            payload=deepcopy(payload),
        )
        self.events.append(event)
        return event

    def events_after(self, after_seq: int) -> list[RunEvent]:
        return [event for event in self.events if event.event_seq > after_seq]

    def to_public_snapshot(self, registry: Any) -> dict[str, Any]:
        # Deliberately construct this projection field by field.  Copying YAML
        # records wholesale here would make it too easy to leak secrets,
        # private goals, memories, relation values, or authoring notes later.
        player_id = registry.player_actor_id
        pending_invitations = [
            {
                key: deepcopy(value)
                for key, value in invitation.items()
                if not key.startswith("_")
            }
            for invitation in self.invitations.values()
            if invitation.get("status") == "pending"
            and player_id
            in {
                invitation.get("initiatorActorId"),
                invitation.get("targetActorId"),
            }
        ]
        pending_join_requests = [
            {
                "joinRequestId": request["joinRequestId"],
                "conversationId": request["conversationId"],
                "applicantActorId": request["applicantActorId"],
                "status": request["status"],
                "requestedAt": request["requestedAt"],
                "approverActorIds": list(request["approverActorIds"]),
                "pendingPlayerDecision": (
                    request["approverDecisions"].get(player_id) == "pending"
                ),
            }
            for request in self.join_requests.values()
            if request.get("status") == "pending"
            and (
                request.get("applicantActorId") == player_id
                or player_id in request.get("approverActorIds", ())
            )
        ]
        return {
            "runId": self.run_id,
            "stateVersion": self.state_version,
            "eventSeq": self.event_seq,
            "worldTime": self.clock.as_dict(),
            "playerAgendaId": self.player_agenda_id,
            "actors": [registry.public_actor(actor_id) for actor_id in registry.actors],
            "actorStates": {
                actor_id: {
                    "status": str(state.get("status", "present")),
                    "position": deepcopy(self.positions.get(actor_id, {"x": 0, "y": 0})),
                }
                for actor_id, state in self.actor_states.items()
            },
            "conversations": [
                conversation.to_public_dict() for conversation in self.conversations.values()
            ],
            "pendingInvitations": pending_invitations,
            "pendingJoinRequests": pending_join_requests,
            "worldEvents": [
                {
                    key: deepcopy(value)
                    for key, value in event.items()
                    if key != "visibleActorIds"
                }
                for event in self.world_events.values()
                if event.get("visibility") == "public"
                or registry.player_actor_id in event.get("visibleActorIds", ())
            ],
            "currentWorldState": {
                **deepcopy(self.current_world_state),
                **deepcopy(self.actor_world_state.get(registry.player_actor_id, {})),
            },
            "chapterEnded": self.clock.is_ended,
            "chapterResolution": deepcopy(self.chapter_resolution),
        }
