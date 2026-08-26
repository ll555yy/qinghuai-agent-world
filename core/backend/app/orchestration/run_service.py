"""Application service for durable Runs and public events."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import math
import random
import time
import uuid
from copy import deepcopy
from typing import Any, cast

from ..agents.memory_tool import RetrieveOwnedMemoriesTool
from ..agents.models import AgentInvocation, MemoryToolContext
from ..agents.runtime import NPCAgent, NPCAgentRegistry, NPCAgentRuntime
from ..ai.decision_service import DecisionService, StructuredCallFailed
from ..ai.protocols import (
    ChatDecision,
    DailyActionDecision,
    ExitConsolidation,
    InvitationDecision,
    RelationshipUpdate,
)
from ..domain.clock import WorldClock, WorldTime
from ..domain.conversation import Conversation
from ..domain.errors import (
    ActorAlreadyInConversationError,
    ActorNotFoundError,
    ActorNotInConversationError,
    AgendaNotFoundError,
    ChapterAlreadyEndedError,
    ConsolidationNotFoundError,
    ConversationFullError,
    ConversationLimitReachedError,
    ConversationNotFoundError,
    DuplicateCommandError,
    InvalidConversationParticipantsError,
    InvalidInvitationError,
    InvalidJoinRequestError,
    InvalidMessageError,
    InvitationNotFoundError,
    JoinRequestNotFoundError,
    PlayerAccessDeniedError,
    RunNotFoundError,
    WorldStepError,
)
from ..domain.run import CommandRecord, Run
from ..persistence.in_memory import InMemoryRunRepository
from ..persistence.memory_retriever import MemoryRetriever
from ..persistence.run_repository import RunRepository
from ..scenario.models import ScenarioRegistry
from .event_hub import EventHub

# Keep the first rolling-summary policy deliberately small and deterministic.
# The source messages remain authoritative; these values only control what is
# supplied to the next private model prompt.
SEGMENT_SUMMARY_TRIGGER_MESSAGES = 20
SEGMENT_SUMMARY_TRIGGER_TOKENS = 2400
SEGMENT_SUMMARY_RECENT_MESSAGES = 8
SEGMENT_BOUNDARY_CARRYOVER_MESSAGES = 4

INITIAL_MEMORY_CACHE_LIMIT = 1
CHAT_COOLDOWN_SECONDS = 12.0
CHAT_PUBLISH_DELAY_MIN_SECONDS = 1.2
CHAT_PUBLISH_DELAY_MAX_SECONDS = 3.0
CHAT_NPC_ONLY_SAFETY_ROUNDS = 8


class RunService:
    """Coordinate repository, domain rules, per-Run locks, and event fan-out."""

    def __init__(
        self,
        registry: ScenarioRegistry,
        repository: RunRepository | None = None,
        event_hub: EventHub | None = None,
        text_model: Any | None = None,
        memory_retriever: MemoryRetriever | None = None,
        seed: int = 1,
        segment_summary_trigger_messages: int = SEGMENT_SUMMARY_TRIGGER_MESSAGES,
        segment_summary_trigger_tokens: int = SEGMENT_SUMMARY_TRIGGER_TOKENS,
        segment_summary_recent_messages: int = SEGMENT_SUMMARY_RECENT_MESSAGES,
        segment_boundary_carryover_messages: int = SEGMENT_BOUNDARY_CARRYOVER_MESSAGES,
        model_max_concurrency: int = 6,
        chat_cooldown_seconds: float = CHAT_COOLDOWN_SECONDS,
        chat_publish_delay_min_seconds: float = 0.0,
        chat_publish_delay_max_seconds: float = 0.0,
        chat_model_call_timeout_seconds: float = 45.0,
        chat_npc_only_safety_rounds: int = CHAT_NPC_ONLY_SAFETY_ROUNDS,
    ) -> None:
        if segment_summary_recent_messages >= segment_summary_trigger_messages:
            raise ValueError(
                "segment_summary_recent_messages must be less than "
                "segment_summary_trigger_messages"
            )
        if min(
            segment_summary_trigger_messages,
            segment_summary_trigger_tokens,
            segment_summary_recent_messages,
            segment_boundary_carryover_messages,
        ) <= 0:
            raise ValueError("segment context limits must be positive")
        if segment_boundary_carryover_messages > segment_summary_recent_messages:
            raise ValueError(
                "segment_boundary_carryover_messages must not exceed "
                "segment_summary_recent_messages"
            )
        if chat_cooldown_seconds < 0:
            raise ValueError("chat_cooldown_seconds must not be negative")
        if (
            chat_publish_delay_min_seconds < 0
            or chat_publish_delay_max_seconds < chat_publish_delay_min_seconds
        ):
            raise ValueError("chat publish delays must be ordered non-negative values")
        if chat_npc_only_safety_rounds <= 0:
            raise ValueError("chat_npc_only_safety_rounds must be greater than zero")
        if chat_model_call_timeout_seconds <= 0:
            raise ValueError("chat_model_call_timeout_seconds must be greater than zero")
        self.registry = registry
        self.repository = repository or InMemoryRunRepository()
        self.event_hub = event_hub or EventHub()
        self.text_model = text_model
        self.decisions = DecisionService(
            text_model,
            max_concurrency=model_max_concurrency,
        )
        # Five logical NPC Agents share this DecisionService and one compiled
        # LangGraph.  They receive only invocation snapshots; RunService
        # remains the authority that applies their semantic decisions.
        self.agent_runtime = NPCAgentRuntime(
            self.decisions,
            memory_tool_factory=(
                lambda actor_id: RetrieveOwnedMemoriesTool(
                    bound_owner_npc_id=actor_id,
                    retriever=memory_retriever,
                )
            ),
        )
        self.agents = NPCAgentRegistry(
            self.agent_runtime,
            [npc.actor_id for npc in self.registry.npcs],
        )
        self.seed = seed
        self.segment_summary_trigger_messages = segment_summary_trigger_messages
        self.segment_summary_trigger_tokens = segment_summary_trigger_tokens
        self.segment_summary_recent_messages = segment_summary_recent_messages
        self.segment_boundary_carryover_messages = (
            segment_boundary_carryover_messages
        )
        self.chat_cooldown_seconds = float(chat_cooldown_seconds)
        self.chat_publish_delay_min_seconds = float(chat_publish_delay_min_seconds)
        self.chat_publish_delay_max_seconds = float(chat_publish_delay_max_seconds)
        self.chat_npc_only_safety_rounds = chat_npc_only_safety_rounds
        self.chat_model_call_timeout_seconds = float(chat_model_call_timeout_seconds)
        self._chat_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._chat_wake_events: dict[tuple[str, str], asyncio.Event] = {}
        self._maintenance_tasks: set[asyncio.Task[None]] = set()
        self._maintenance_keys: set[tuple[str, str]] = set()

    async def create_run(self, agenda_id: str | None = None, seed: int | None = None) -> dict[str, Any]:
        if agenda_id is not None and self.registry.agenda(agenda_id) is None:
            raise AgendaNotFoundError(details={"agendaId": agenda_id})
        run_id = f"run_{uuid.uuid4().hex}"
        run_seed = self.seed if seed is None else seed
        clock = WorldClock(
            current=WorldTime(
                day=self.registry.start_day,
                hour=self.registry.start_hour,
                minute=self.registry.start_minute,
            ),
            active_start_minutes=self.registry.active_start_minutes,
            active_end_minutes=self.registry.active_end_minutes,
            new_chat_cutoff_minutes=17 * 60,
            final_day=self.registry.end_day,
        )
        run = Run(
            run_id=run_id,
            player_agenda_id=agenda_id,
            clock=clock,
            seed=run_seed,
            actor_states={
                actor_id: {"status": "present"} for actor_id in self.registry.actors
            },
        )
        run.positions = self._default_positions()
        run.daily_think_order = self._daily_order(run_seed)
        run.daily_think_schedule = self._build_daily_schedules(run.daily_think_order)
        run.daily_think_minutes = deepcopy(
            run.daily_think_schedule.get(run.clock.current.day, {})
        )
        run.thought_days = {npc.actor_id: set() for npc in self.registry.npcs}
        run.goals = {
            goal_id: {
                "goalId": goal.goal_id,
                "ownerNpcId": goal.owner_npc_id,
                "horizon": goal.horizon,
                "disclosure": goal.disclosure,
                "description": goal.description,
                "targetActorIds": list(goal.target_actor_ids),
                "topicIds": list(goal.topic_ids),
                "importance": goal.importance,
                "status": goal.status,
            }
            for goal_id, goal in self.registry.goals.items()
        }
        run.relationships = {
            key: {
                "fromActorId": value.from_actor_id,
                "toActorId": value.to_actor_id,
                "socialRoles": list(value.social_roles),
                "familiarity": value.familiarity,
                "trust": value.trust,
                "affinity": value.affinity,
                "tension": value.tension,
                "interactionCount": value.interaction_count,
            }
            for key, value in self.registry.relationships.items()
        }
        run.memories = {
            memory_id: {
                "memoryId": memory.memory_id,
                "ownerNpcId": memory.owner_npc_id,
                "type": memory.memory_type,
                "content": memory.content,
                "actorIds": list(memory.actor_ids),
                "topicIds": list(memory.topic_ids),
                "importance": memory.importance,
                "confidence": memory.confidence,
                "source": memory.source,
                "evidenceMessageIds": list(memory.evidence_message_ids),
                "createdAt": run.clock.as_dict()["label"],
            }
            for memory_id, memory in self.registry.memories.items()
        }
        run.fresh_event_context = {npc.actor_id: [] for npc in self.registry.npcs}
        run.actor_world_state = {actor_id: {} for actor_id in self.registry.actors}
        run.chapter_actor_stances = {npc.actor_id: "unknown" for npc in self.registry.npcs}
        run.chapter_agenda_stances = {
            (agenda.agenda_id, npc.actor_id): "unknown"
            for agenda in self.registry.public_agendas
            for npc in self.registry.npcs
        }
        async with run.lock:
            event = run.append_event(
                "run_created",
                {
                    "worldTime": run.clock.as_dict(),
                    "playerAgendaId": agenda_id,
                },
            )
            # Register the aggregate before Day1's due actions.  Those
            # actions may release the Run lock for a model call, and that
            # boundary checkpoints through ``repository.save``.
            await self.repository.add(run)
            # Day1's public notice is authoritative and is always visible
            # before the first scheduled NPC thought.
            initial_events = [event]
            await self._process_due_locked(run)
            initial_events.extend(run.events[len(initial_events):])
            snapshot = run.to_public_snapshot(self.registry)
            await self.repository.save(run)
        for initial_event in initial_events:
            await self.event_hub.publish(run_id, initial_event.to_dict())
        return snapshot

    async def get_run_entity(self, run_id: str) -> Run:
        run = await self.repository.get(run_id)
        if run is None:
            raise RunNotFoundError(details={"runId": run_id})
        await self.event_hub.prime(run_id, run.event_seq)
        self._resume_chat_tasks(run)
        self._resume_maintenance_tasks(run)
        return run

    @staticmethod
    def _default_round_state(segment_id: str | None) -> dict[str, Any]:
        return {
            "roundId": 0,
            "roundVersion": 0,
            "status": "idle",
            "triggerMessageIds": [],
            "queuedMessageIds": [],
            "segmentId": segment_id,
            "participantVersion": 1,
            "cooldownDueAt": None,
            "finalCheckUsed": False,
            "pendingPublications": [],
            "pendingLeaverIds": [],
            "pendingPostSpeechLeaverIds": [],
            "npcOnlyRounds": 0,
            "openerActorId": None,
            "openerKind": None,
            "awaitingPlayerOpener": False,
        }

    def _round_state_locked(
        self,
        run: Run,
        conversation: Conversation,
    ) -> dict[str, Any]:
        segments = run.segments.get(conversation.conversation_id, [])
        segment_id = segments[-1].get("segmentId") if segments else None
        state = run.ensure_conversation_round_state(conversation.conversation_id)
        defaults = self._default_round_state(segment_id)
        for key, value in defaults.items():
            state.setdefault(key, deepcopy(value))
        if not isinstance(state.get("roundId"), int):
            state["roundId"] = 0
        if state.get("segmentId") is None:
            state["segmentId"] = segment_id
        return state

    def _reset_round_for_participant_change_locked(
        self,
        run: Run,
        conversation: Conversation,
        *,
        status: str = "idle",
        opener_actor_id: str | None = None,
        opener_kind: str | None = None,
        awaiting_player_opener: bool = False,
    ) -> dict[str, Any]:
        state = self._round_state_locked(run, conversation)
        segments = run.segments.get(conversation.conversation_id, [])
        state["roundVersion"] = int(state.get("roundVersion", 0)) + 1
        state["participantVersion"] = int(state.get("participantVersion", 0)) + 1
        state["status"] = status
        state["triggerMessageIds"] = []
        state["queuedMessageIds"] = []
        state["segmentId"] = segments[-1].get("segmentId") if segments else None
        state["cooldownDueAt"] = None
        state["finalCheckUsed"] = False
        state["pendingPublications"] = []
        state["pendingLeaverIds"] = []
        state["pendingPostSpeechLeaverIds"] = []
        state["recovery"] = {
            "resumeStatus": None,
            "attempt": 0,
            "publishedMessageIds": [],
        }
        state["npcOnlyRounds"] = 0
        state["openerActorId"] = opener_actor_id
        state["openerKind"] = opener_kind
        state["awaitingPlayerOpener"] = awaiting_player_opener
        self._wake_chat_worker(run.run_id, conversation.conversation_id)
        return state

    def _queue_message_round_locked(
        self,
        run: Run,
        conversation: Conversation,
        message_ids: list[str],
    ) -> None:
        state = self._round_state_locked(run, conversation)
        unique_ids = list(dict.fromkeys(str(item) for item in message_ids if item))
        if not unique_ids:
            return
        status = str(state.get("status", "idle"))
        if status in {"deciding", "generating", "publishing"}:
            queued = list(state.get("queuedMessageIds", []))
            state["queuedMessageIds"] = list(dict.fromkeys([*queued, *unique_ids]))
        else:
            state["roundVersion"] = int(state.get("roundVersion", 0)) + 1
            state["status"] = "queued"
            state["triggerMessageIds"] = unique_ids
            state["queuedMessageIds"] = []
            state["cooldownDueAt"] = None
            state["finalCheckUsed"] = False
            state["awaitingPlayerOpener"] = False
        if any(
            message.get("messageId") in unique_ids
            and message.get("authorActorId") == self.registry.player_actor_id
            for message in run.messages.get(conversation.conversation_id, [])
        ):
            state["npcOnlyRounds"] = 0
        self._wake_chat_worker(run.run_id, conversation.conversation_id)

    def _resume_chat_tasks(self, run: Run) -> None:
        for conversation_id, state in run.conversation_round_states.items():
            if state.get("status") in {
                "queued",
                "deciding",
                "generating",
                "publishing",
                "cooldown",
                "final_check",
                "opener",
            }:
                self._ensure_chat_task(run, conversation_id)

    def _resume_maintenance_tasks(self, run: Run) -> None:
        conversation_ids = {
            conversation_id
            for conversation_id, state in run.conversation_round_states.items()
            if state.get("pendingSummarySegmentIds")
        }
        conversation_ids.update(
            conversation_id
            for (conversation_id, _npc_id), status in run.consolidation_status.items()
            if status.get("status") == "pending"
        )
        for conversation_id in conversation_ids:
            state = run.conversation_round_states.get(conversation_id, {})
            segment_ids = list(state.get("pendingSummarySegmentIds", []))
            npc_ids = [
                npc_id
                for (candidate_id, npc_id), status in run.consolidation_status.items()
                if candidate_id == conversation_id and status.get("status") == "pending"
            ]
            self._schedule_player_leave_maintenance(
                run,
                conversation_id,
                segment_ids[0] if segment_ids else None,
                npc_ids,
            )

    def _ensure_chat_task(self, run: Run, conversation_id: str) -> None:
        key = (run.run_id, conversation_id)
        existing = self._chat_tasks.get(key)
        if existing is not None and not existing.done():
            self._wake_chat_worker(*key)
            return
        task = asyncio.create_task(
            self._chat_worker(run.run_id, conversation_id),
            name=f"chat-round:{run.run_id}:{conversation_id}",
        )
        self._chat_tasks[key] = task
        run.conversation_round_tasks[conversation_id] = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._chat_tasks.get(key) is done:
                self._chat_tasks.pop(key, None)
            if run.conversation_round_tasks.get(conversation_id) is done:
                run.conversation_round_tasks.pop(conversation_id, None)
            # Retrieving the exception prevents an unobserved-task warning;
            # the worker itself converts failures into durable activity state.
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    asyncio.create_task(
                        self._record_chat_worker_failure(
                            run,
                            conversation_id,
                            type(error).__name__,
                        )
                    )

        task.add_done_callback(finished)

    async def _record_chat_worker_failure(
        self,
        run: Run,
        conversation_id: str,
        error_type: str,
    ) -> None:
        restart = False
        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None or not conversation.is_open:
                return
            before_seq = run.event_seq
            state = self._round_state_locked(run, conversation)
            previous_status = str(state.get("status", "idle"))
            recovery = state.setdefault("recovery", {})
            attempt = int(recovery.get("attempt", 0)) + 1
            recovery["attempt"] = attempt
            recovery["resumeStatus"] = previous_status
            if attempt <= 1:
                if previous_status == "publishing":
                    state["status"] = "publishing"
                elif state.get("triggerMessageIds") or state.get("queuedMessageIds"):
                    state["triggerMessageIds"] = self._ordered_message_ids_locked(
                        run,
                        conversation_id,
                        [
                            *state.get("triggerMessageIds", []),
                            *state.get("queuedMessageIds", []),
                        ],
                    )
                    state["queuedMessageIds"] = []
                    state["status"] = "queued"
                elif state.get("openerActorId"):
                    state["status"] = "opener"
                else:
                    state["status"] = "idle"
                restart = state["status"] in {"publishing", "queued", "opener"}
            else:
                state["pendingPublications"] = []
                state["pendingLeaverIds"] = []
                state["pendingPostSpeechLeaverIds"] = []
                self._enter_cooldown_locked(run, conversation, state)
            run.append_event(
                "conversation_activity",
                {
                    "conversationId": conversation_id,
                    "reason": (
                        "round_worker_retry_scheduled"
                        if restart
                        else "round_worker_failed"
                    ),
                    "errorType": error_type,
                },
            )
            events = [event.to_dict() for event in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in events:
            await self.event_hub.publish(run.run_id, event)
        if restart:
            self._ensure_chat_task(run, conversation_id)

    async def close(self) -> None:
        """Cancel process-local round workers during application shutdown."""

        tasks = [task for task in self._chat_tasks.values() if not task.done()]
        tasks.extend(task for task in self._maintenance_tasks if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._chat_tasks.clear()
        self._chat_wake_events.clear()
        self._maintenance_tasks.clear()
        self._maintenance_keys.clear()

    def _wake_chat_worker(self, run_id: str, conversation_id: str) -> None:
        key = (run_id, conversation_id)
        self._chat_wake_events.setdefault(key, asyncio.Event()).set()

    async def wait_for_chat_idle(
        self,
        run_id: str,
        conversation_id: str,
        *,
        timeout: float = 30.0,
        include_cooldown: bool = False,
    ) -> None:
        """Testing/simulation hook; production commands never wait here."""

        deadline = time.monotonic() + timeout
        while True:
            run = await self.get_run_entity(run_id)
            async with run.lock:
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not conversation.is_open:
                    return
                state = self._round_state_locked(run, conversation)
                status = str(state.get("status", "idle"))
                if status in {"idle", "awaiting_player_opener"}:
                    return
                if status == "cooldown" and not include_cooldown:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"chat round did not settle: {conversation_id}")
            task = self._chat_tasks.get((run_id, conversation_id))
            if task is None:
                self._ensure_chat_task(run, conversation_id)
            # A worker deliberately remains alive while it waits through the
            # cooldown. Poll the durable state instead of awaiting task
            # completion so callers can choose whether cooldown counts as
            # settled.
            await asyncio.sleep(min(0.01, remaining))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        async with run.lock:
            return deepcopy(run.to_public_snapshot(self.registry))

    async def get_events(self, run_id: str, after_seq: int = 0) -> dict[str, Any]:
        await self.get_run_entity(run_id)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("afterSeq must be a non-negative integer")
        events = [event.to_dict() for event in await self.repository.events_after(run_id, after_seq)]
        return {"runId": run_id, "afterSeq": after_seq, "events": events}

    async def create_conversation(
        self,
        run_id: str,
        participant_ids: list[str],
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("create_conversation", {"participantIds": participant_ids})
        event_dict: dict[str, Any] | None = None
        conversation_to_start: str | None = None
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            self._validate_new_chat_window_locked(run)
            if len(participant_ids) != 2 or len(set(participant_ids)) != 2:
                raise InvalidConversationParticipantsError(
                    "A new conversation must start with exactly two participants; "
                    "a third participant must use a join request."
                )
            for actor_id in participant_ids:
                self._require_actor(actor_id)
                if run.actor_states.get(actor_id, {}).get("status") == "departed":
                    raise InvalidConversationParticipantsError(
                        "A departed actor cannot start a conversation."
                    )
                if any(
                    invitation.get("status") == "pending"
                    and actor_id
                    in {
                        invitation.get("initiatorActorId"),
                        invitation.get("targetActorId"),
                    }
                    for invitation in run.invitations.values()
                ):
                    raise InvalidInvitationError(
                        "The actor must answer or finish the pending invitation first."
                    )
                if self._actor_has_pending_join_request(run, actor_id):
                    raise InvalidJoinRequestError(
                        "The actor must finish the pending join request first."
                    )
            if len(run.open_conversations()) >= 2:
                raise ConversationLimitReachedError()
            for actor_id in participant_ids:
                if run.actor_open_conversation(actor_id) is not None:
                    raise ActorAlreadyInConversationError(details={"actorId": actor_id})
            conversation_id, creation_seq = run.next_conversation_identity()
            conversation = Conversation(
                conversation_id=conversation_id,
                creation_seq=creation_seq,
                participants=list(participant_ids),
            )
            run.conversations[conversation_id] = conversation
            run.messages[conversation_id] = []
            run.segments[conversation_id] = [{"segmentId": run.next_segment_identity(), "participants": list(participant_ids), "startedAt": run.clock.as_dict()["label"], "summary": None, "summaryThroughMessageId": None}]
            run.conversation_drafts[conversation_id] = {
                actor_id: {
                    "goalUpdates": {},
                    "relationshipUpdates": [],
                    "pendingGoals": [],
                    "chapterEffects": [],
                }
                for actor_id in participant_ids
                if self.registry.actor(actor_id) is not None
                and self.registry.actor(actor_id).kind == "npc"  # type: ignore[union-attr]
            }
            for actor_id in participant_ids:
                run.actor_states[actor_id]["status"] = "chatting"
                actor = self.registry.actor(actor_id)
                run.memory_cache[(conversation_id, actor_id)] = (
                    set(self._initial_memory_ids(run, actor_id, participant_ids))
                    if actor is not None and actor.kind == "npc"
                    else set()
                )
            initiator_id = participant_ids[0]
            initiator = self.registry.actor(initiator_id)
            round_state = self._round_state_locked(run, conversation)
            if initiator is not None and initiator.kind == "npc":
                round_state.update(
                    {
                        "status": "opener",
                        "openerActorId": initiator_id,
                        "openerKind": "conversation_opener",
                    }
                )
                conversation_to_start = conversation_id
            else:
                round_state.update(
                    {
                        "status": "awaiting_player_opener",
                        "awaitingPlayerOpener": True,
                    }
                )
            event = run.append_event("conversation_created", {"conversation": conversation.to_public_dict()})
            event_dict = event.to_dict()
            result: dict[str, Any] = {
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        assert event_dict is not None
        await self.event_hub.publish(run_id, event_dict)
        if conversation_to_start is not None:
            self._ensure_chat_task(run, conversation_to_start)
        return result

    async def add_participant(
        self,
        run_id: str,
        conversation_id: str,
        actor_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request_join_command(
            run_id,
            conversation_id,
            actor_id,
            command_id,
            command_name="add_participant",
            include_player_history=actor_id == self.registry.player_actor_id,
        )

    async def remove_participant(
        self,
        run_id: str,
        conversation_id: str,
        actor_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint(
            "remove_participant", {"conversationId": conversation_id, "actorId": actor_id}
        )
        events_to_publish: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            conversation = run.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(details={"conversationId": conversation_id})
            if not conversation.is_open:
                raise InvalidConversationParticipantsError("Conversation is already closed.")
            self._require_chat_open_locked(run, operation="leave")
            self._require_actor(actor_id)
            before_seq = run.event_seq
            actor = self.registry.actor(actor_id)
            if actor is not None and actor.kind == "npc":
                await self._leave_and_consolidate_locked(run, conversation, actor_id, "api_leave")
            else:
                await self._remove_player_locked(run, conversation, actor_id)
            events_to_publish = [event.to_dict() for event in run.events_after(before_seq)]
            result: dict[str, Any] = {
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for event_dict in events_to_publish:
            await self.event_hub.publish(run_id, event_dict)
        return result

    async def advance_time(
        self,
        run_id: str,
        virtual_minutes: int,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("advance_time", {"virtualMinutes": virtual_minutes})
        event_dict: dict[str, Any] | None = None
        events_to_publish: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            before_seq = run.event_seq
            await self._advance_virtual_locked(run, virtual_minutes)
            new_time = run.clock.current
            events_to_publish = [item.to_dict() for item in run.events_after(before_seq)]
            result = {
                "worldTime": run.clock.as_dict(),
                "run": run.to_public_snapshot(self.registry),
                "advancedTo": new_time.label,
            }
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for event_dict in events_to_publish:
            await self.event_hub.publish(run_id, event_dict)
        return result

    async def world_step(
        self,
        run_id: str,
        real_seconds: int,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert active foreground seconds into authoritative world minutes."""

        if isinstance(real_seconds, bool) or not isinstance(real_seconds, int) or real_seconds <= 0:
            raise WorldStepError(
                "realSeconds must be a positive integer.",
                details={"realSeconds": real_seconds},
            )
        seconds_per_minute = self.registry.real_seconds_per_virtual_minute
        virtual_minutes_float = real_seconds / seconds_per_minute
        if not virtual_minutes_float.is_integer():
            raise WorldStepError(
                f"realSeconds must be a multiple of {seconds_per_minute:g}.",
                details={
                    "realSeconds": real_seconds,
                    "realSecondsPerVirtualMinute": seconds_per_minute,
                },
            )
        virtual_minutes = int(virtual_minutes_float)
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("world_step", {"realSeconds": real_seconds})
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            before_seq = run.event_seq
            await self._advance_virtual_locked(run, virtual_minutes)
            run.append_event("world_stepped", {"worldTime": run.clock.as_dict(), "realSeconds": real_seconds})
            published = [event_item.to_dict() for event_item in run.events_after(before_seq)]
            result = {
                "worldTime": run.clock.as_dict(),
                "run": run.to_public_snapshot(self.registry),
                "advancedMinutes": virtual_minutes,
            }
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for event_dict in published:
            await self.event_hub.publish(run_id, event_dict)
        return result

    async def get_actor_public(self, run_id: str, actor_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        self._require_actor(actor_id)
        async with run.lock:
            actor = self.registry.public_actor(actor_id)
            actor["position"] = deepcopy(run.positions.get(actor_id, {"x": 0, "y": 0}))
            actor["status"] = str(run.actor_states.get(actor_id, {}).get("status", "present"))
            return actor

    async def get_public_agendas(self, run_id: str) -> list[dict[str, str]]:
        await self.get_run_entity(run_id)
        return [self.registry.public_agenda(item.agenda_id) for item in self.registry.public_agendas]

    async def get_messages(self, run_id: str, conversation_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        async with run.lock:
            conversation = self._conversation(run, conversation_id)
            if not self._player_can_read_conversation(run, conversation):
                raise PlayerAccessDeniedError(details={"conversationId": conversation_id})
            return {
                "conversationId": conversation_id,
                "messages": self._public_messages(run.messages.get(conversation_id, [])),
            }

    async def player_invite(
        self,
        run_id: str,
        target_actor_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        self._require_actor(target_actor_id)
        if self.registry.actor(target_actor_id).kind != "npc":  # type: ignore[union-attr]
            raise InvalidInvitationError("Players can only invite NPCs.")
        fingerprint = self._fingerprint("player_invite", {"targetActorId": target_actor_id})
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            self._validate_new_chat_window_locked(run)
            before_seq = run.event_seq
            open_target = run.actor_open_conversation(target_actor_id)
            if open_target is not None:
                raise InvalidInvitationError(
                    "The target is already in a conversation; use the join command."
                )
            self._validate_new_invitation_locked(
                run,
                self.registry.player_actor_id,
                target_actor_id,
            )
            run.actor_states[self.registry.player_actor_id]["status"] = "approaching"
            run.append_event("actor_movement_started", {"actorId": self.registry.player_actor_id, "targetActorId": target_actor_id})
            run.positions[self.registry.player_actor_id] = deepcopy(run.positions.get(target_actor_id, {"x": 0, "y": 0}))
            run.append_event("actor_movement_completed", {"actorId": self.registry.player_actor_id, "targetActorId": target_actor_id, "position": deepcopy(run.positions[self.registry.player_actor_id])})
            invitation = await self._request_invitation_locked(run, self.registry.player_actor_id, target_actor_id)
            published = [item.to_dict() for item in run.events_after(before_seq)]
            result = {"invitation": self._public_invitation(invitation), "run": run.to_public_snapshot(self.registry)}
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for published_event in published:
            await self.event_hub.publish(run_id, published_event)
        return result

    async def respond_invitation(
        self,
        run_id: str,
        invitation_id: str,
        accepted: bool,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("respond_invitation", {"invitationId": invitation_id, "accepted": accepted})
        published: list[dict[str, Any]] = []
        async with run.lock:
            before_seq = run.event_seq
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            invitation = run.invitations.get(invitation_id)
            if invitation is None:
                raise InvitationNotFoundError(details={"invitationId": invitation_id})
            if invitation.get("targetActorId") != self.registry.player_actor_id:
                raise InvalidInvitationError("This invitation is not waiting for the player.")
            if invitation.get("status") != "pending":
                raise InvalidInvitationError("The invitation has already been answered.")
            if not accepted:
                invitation["status"] = "refused"
                invitation["respondedAt"] = run.clock.as_dict()["label"]
                run.actor_states[invitation["initiatorActorId"]]["status"] = "waiting"
                run.append_event("invitation_request_cleared", {"invitationId": invitation_id})
                run.append_event("invitation_refused", {"invitationId": invitation_id, "targetActorId": self.registry.player_actor_id})
                published = [item.to_dict() for item in run.events_after(before_seq)]
                result = {"invitation": self._public_invitation(invitation), "run": run.to_public_snapshot(self.registry)}
            else:
                self._validate_conversation_start_locked(
                    run,
                    [invitation["initiatorActorId"], self.registry.player_actor_id],
                )
                invitation["status"] = "accepted"
                invitation["respondedAt"] = run.clock.as_dict()["label"]
                run.append_event("invitation_request_cleared", {"invitationId": invitation_id})
                conversation = self._open_conversation_locked(
                    run,
                    [invitation["initiatorActorId"], self.registry.player_actor_id],
                    opening_speech=False,
                )
                invitation["conversationId"] = conversation.conversation_id
                run.append_event("invitation_accepted", {"invitationId": invitation_id, "conversationId": conversation.conversation_id})
                initiator = invitation["initiatorActorId"]
                if initiator != self.registry.player_actor_id:
                    await self._generate_opening_speech_locked(
                        run,
                        conversation,
                        initiator,
                        goal_id=invitation.get("_goalId"),
                        intent=invitation.get("_intent"),
                    )
                published = [item.to_dict() for item in run.events_after(before_seq)]
                result = {"invitation": self._public_invitation(invitation), "conversation": conversation.to_public_dict(), "run": run.to_public_snapshot(self.registry)}
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for published_event in published:
            await self.event_hub.publish(run_id, published_event)
        return result

    async def player_join(
        self,
        run_id: str,
        conversation_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request_join_command(
            run_id,
            conversation_id,
            self.registry.player_actor_id,
            command_id,
            command_name="player_join",
            include_player_history=True,
        )

    async def _request_join_command(
        self,
        run_id: str,
        conversation_id: str,
        applicant_actor_id: str,
        command_id: str | None,
        *,
        command_name: str,
        include_player_history: bool,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint(
            command_name,
            {
                "conversationId": conversation_id,
                "applicantActorId": applicant_actor_id,
            },
        )
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            self._validate_new_chat_window_locked(run)
            conversation = self._conversation(run, conversation_id)
            self._require_actor(applicant_actor_id)
            before_seq = run.event_seq
            join_request = await self._request_join_locked(
                run,
                conversation,
                applicant_actor_id,
            )
            published = [event.to_dict() for event in run.events_after(before_seq)]
            result: dict[str, Any] = {
                "joinRequest": self._public_join_request(join_request),
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            if include_player_history and join_request["status"] == "accepted":
                result["messages"] = self._public_messages(
                    run.messages.get(conversation_id, [])
                )
            self._record_command(run, command_id, fingerprint, result)
            await self.repository.save(run)
        for event in published:
            await self.event_hub.publish(run_id, event)
        return result

    async def get_join_request(
        self,
        run_id: str,
        join_request_id: str,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        async with run.lock:
            before_seq = run.event_seq
            self._expire_pending_requests_locked(run)
            join_request = run.join_requests.get(join_request_id)
            if join_request is None:
                raise JoinRequestNotFoundError(
                    details={"joinRequestId": join_request_id}
                )
            result = {
                "joinRequest": self._public_join_request(join_request),
                "run": run.to_public_snapshot(self.registry),
            }
            if run.event_seq != before_seq:
                await self.repository.save(run)
            return result

    async def respond_join_request(
        self,
        run_id: str,
        join_request_id: str,
        accepted: bool,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint(
            "respond_join_request",
            {"joinRequestId": join_request_id, "accepted": accepted},
        )
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            join_request = run.join_requests.get(join_request_id)
            if join_request is None:
                raise JoinRequestNotFoundError(
                    details={"joinRequestId": join_request_id}
                )
            player_id = self.registry.player_actor_id
            decisions = join_request["approverDecisions"]
            if (
                join_request.get("status") != "pending"
                or player_id not in join_request.get("approverActorIds", [])
                or decisions.get(player_id) != "pending"
            ):
                raise InvalidJoinRequestError(
                    "This join request is not waiting for the player."
                )
            before_seq = run.event_seq
            decisions[player_id] = "accept" if accepted else "refuse"
            if accepted:
                await self._resolve_join_request_locked(run, join_request)
            else:
                self._refuse_join_request_locked(run, join_request)
            conversation = self._conversation(
                run,
                str(join_request["conversationId"]),
            )
            result: dict[str, Any] = {
                "joinRequest": self._public_join_request(join_request),
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            if (
                join_request["status"] == "accepted"
                and join_request["applicantActorId"] == player_id
            ):
                result["messages"] = self._public_messages(
                    run.messages.get(conversation.conversation_id, [])
                )
            self._record_command(run, command_id, fingerprint, result)
            published = [event.to_dict() for event in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in published:
            await self.event_hub.publish(run_id, event)
        return result

    async def player_message(
        self,
        run_id: str,
        conversation_id: str,
        text: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InvalidMessageError("text must contain 1 to 2000 characters.")
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("player_message", {"conversationId": conversation_id, "text": text})
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            self._expire_pending_requests_locked(run)
            conversation = self._conversation(run, conversation_id)
            if (
                not conversation.is_open
                or not conversation.has_participant(self.registry.player_actor_id)
            ):
                raise PlayerAccessDeniedError(details={"conversationId": conversation_id})
            self._require_chat_open_locked(run, operation="message")
            before_seq = run.event_seq
            message = self._write_message_locked(
                run,
                conversation,
                self.registry.player_actor_id,
                text,
            )
            self._queue_message_round_locked(
                run,
                conversation,
                [message["messageId"]],
            )
            result = {
                "acceptedMessageId": message["messageId"],
                "conversation": conversation.to_public_dict(),
                "messages": self._public_messages(run.messages.get(conversation_id, [])),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
            events = [item.to_dict() for item in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in events:
            await self.event_hub.publish(run_id, event)
        self._ensure_chat_task(run, conversation_id)
        return result

    async def conversation_idle(self, run_id: str, conversation_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        async with run.lock:
            conversation = self._conversation(run, conversation_id)
            if not conversation.has_participant(self.registry.player_actor_id):
                raise PlayerAccessDeniedError(details={"conversationId": conversation_id})
            before_seq = run.event_seq
            if not conversation.is_open:
                return {"conversation": conversation.to_public_dict(), "run": run.to_public_snapshot(self.registry)}
            self._require_chat_open_locked(run, operation="idle")
            state = self._round_state_locked(run, conversation)
            if state.get("status") not in {
                "deciding",
                "generating",
                "publishing",
                "queued",
                "final_check",
                "cooldown",
            }:
                self._enter_cooldown_locked(run, conversation, state)
                self._ensure_chat_task(run, conversation_id)
            result = {"conversation": conversation.to_public_dict(), "run": run.to_public_snapshot(self.registry)}
            events = [item.to_dict() for item in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in events:
            await self.event_hub.publish(run_id, event)
        return result

    async def retry_consolidation(self, run_id: str, npc_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        self._require_actor(npc_id)
        published: list[dict[str, Any]] = []
        async with run.lock:
            candidates = [
                (key, value) for key, value in run.consolidation_status.items()
                if key[1] == npc_id
                and value.get("status") == "failed"
                and int(value.get("attempts", 0)) < 2
            ]
            if not candidates:
                raise ConsolidationNotFoundError(details={"npcId": npc_id})
            (conversation_id, _), _status = candidates[-1]
            conversation = self._conversation(run, conversation_id)
            before_seq = run.event_seq
            await self._consolidate_locked(run, conversation, npc_id, "retry")
            published = [event.to_dict() for event in run.events_after(before_seq)]
            result = {"npcId": npc_id, "conversationId": conversation_id, "status": run.consolidation_status[(conversation_id, npc_id)].get("status"), "run": run.to_public_snapshot(self.registry)}
            await self.repository.save(run)
        for event in published:
            await self.event_hub.publish(run_id, event)
        return result

    # ------------------------------------------------------------------
    # World and conversation internals.  These methods run while the Run
    # lock is held.  They append only player-safe public events.

    async def _await_model_without_run_lock(self, run: Run, awaitable: Any) -> Any:
        """Await one model call without pinning the Run lock.

        The orchestration lock protects the in-memory aggregate, but it must
        not also become a model/network lock.  Chat calls use this helper so a
        concurrent ``world_step`` can advance the authoritative clock and
        install the day-end barrier while the provider is still waiting.
        Callers enter with ``run.lock`` held and resume with it held again.
        """

        # Any state produced before the provider wait (for example an
        # accepted message or pending invitation) must survive a process
        # failure while the network call is in flight.  This save completes
        # before releasing the aggregate lock and does not keep a database
        # transaction open across the model call.
        await self.repository.save(run)
        run.lock.release()
        try:
            return await awaitable
        finally:
            await run.lock.acquire()

    def _default_positions(self) -> dict[str, dict[str, int]]:
        return {
            "npc_001": {"x": 0, "y": 0},
            "npc_002": {"x": 2, "y": 0},
            "npc_003": {"x": 4, "y": 0},
            "npc_004": {"x": 6, "y": 0},
            "npc_005": {"x": 8, "y": 0},
            "player_001": {"x": 1, "y": 2},
        }

    def _daily_order(self, seed: int) -> list[str]:
        """Return the replayable seed-derived baseline NPC order."""

        ids = [npc.actor_id for npc in self.registry.npcs]
        random.Random(seed).shuffle(ids)
        return ids

    def _daily_schedule(self, seed: int) -> dict[str, int]:
        """Return Day1's five compressed action slots.

        Kept as a small compatibility helper for callers that used the
        original service implementation.  Rotation is applied by
        ``_build_daily_schedules`` rather than by the model or the clock.
        """

        return {
            actor_id: (9 + index) * 60
            for index, actor_id in enumerate(self._daily_order(seed))
        }

    def _build_daily_schedules(self, baseline_order: list[str]) -> dict[int, dict[str, int]]:
        """Expand the baseline into a fixed, left-rotated schedule per day."""

        if not baseline_order:
            return {}
        schedules: dict[int, dict[str, int]] = {}
        for day in range(self.registry.start_day, self.registry.end_day + 1):
            shift = (day - self.registry.start_day) % len(baseline_order)
            order = baseline_order[shift:] + baseline_order[:shift]
            schedules[day] = {
                actor_id: (9 + index) * 60
                for index, actor_id in enumerate(order)
            }
        return schedules

    def _schedule_for_day(self, run: Run, day: int) -> dict[str, int]:
        """Return and cache the authoritative schedule for ``day``."""

        schedule = run.daily_think_schedule.get(day)
        if schedule is None:
            baseline = run.daily_think_order or [npc.actor_id for npc in self.registry.npcs]
            if not baseline:
                return {}
            shift = (day - self.registry.start_day) % len(baseline)
            order = baseline[shift:] + baseline[:shift]
            schedule = {
                actor_id: (9 + index) * 60
                for index, actor_id in enumerate(order)
            }
            run.daily_think_schedule[day] = schedule
        return schedule

    def _refresh_daily_schedule_locked(self, run: Run) -> dict[str, int]:
        schedule = self._schedule_for_day(run, run.clock.current.day)
        run.daily_think_minutes = deepcopy(schedule)
        return schedule

    @staticmethod
    def _absolute(day: int, hour: int, minute: int) -> int:
        return day * 24 * 60 + hour * 60 + minute

    @staticmethod
    def _time_absolute(run: Run) -> int:
        return RunService._absolute(run.clock.current.day, run.clock.current.hour, run.clock.current.minute)

    @staticmethod
    def _parse_event_time(value: str) -> tuple[int, int]:
        hour, minute = value.split(":")
        return int(hour), int(minute)

    def _event_absolute(self, event: Any) -> int:
        hour, minute = self._parse_event_time(event.at)
        return self._absolute(event.world_day, hour, minute)

    async def _advance_virtual_locked(self, run: Run, virtual_minutes: int) -> None:
        if virtual_minutes <= 0:
            raise WorldStepError("The world step must be positive.")
        if run.clock.is_ended:
            raise WorldStepError("The chapter has already ended.")
        await self._process_due_locked(run)
        # A day-end close is deliberately deferred while a pre-boundary chat
        # call is in flight.  Do not consume the same command's remaining
        # minutes into the next day before that chat has been settled.
        if run.pending_day_end is not None:
            return
        remaining = virtual_minutes
        while remaining and not run.clock.is_ended:
            if run.clock.current.clock_minutes == self.registry.end_hour * 60 + self.registry.end_minute and run.clock.current.day < self.registry.end_day:
                run.clock.current = WorldTime(day=run.clock.current.day + 1, hour=self.registry.active_start_minutes // 60, minute=self.registry.active_start_minutes % 60)
                self._refresh_daily_schedule_locked(run)
                run.append_event("world_day_started", {"worldTime": run.clock.as_dict()})
                for npc in self.registry.npcs:
                    run.fresh_event_context[npc.actor_id] = []
                await self._process_due_locked(run)
                continue
            current = self._time_absolute(run)
            candidates: list[int] = []
            for event in self.registry.events:
                event_abs = self._event_absolute(event)
                if event.event_id not in run.fired_event_ids and event_abs > current:
                    candidates.append(event_abs)
            for npc in self.registry.npcs:
                if run.clock.current.day not in run.thought_days.get(npc.actor_id, set()):
                    schedule = self._schedule_for_day(run, run.clock.current.day)
                    thought_minute = schedule[npc.actor_id]
                    thought_abs = self._absolute(run.clock.current.day, thought_minute // 60, thought_minute % 60)
                    if thought_abs > current:
                        candidates.append(thought_abs)
            cutoff = self._absolute(
                run.clock.current.day,
                run.clock.new_chat_cutoff_minutes // 60,
                run.clock.new_chat_cutoff_minutes % 60,
            )
            if cutoff > current:
                candidates.append(cutoff)
            day_end = self._absolute(run.clock.current.day, self.registry.end_hour, self.registry.end_minute)
            if day_end > current:
                candidates.append(day_end)
            if not candidates:
                break
            next_point = min(candidates)
            delta = next_point - current
            if delta <= 0:
                await self._process_due_locked(run)
                continue
            consumed = min(remaining, delta)
            if consumed < delta:
                run.clock.advance(consumed)
                run.append_event("time_advanced", {"worldTime": run.clock.as_dict()})
                remaining = 0
                await self._process_due_locked(run)
                break
            # Arrive exactly at a scheduled action or the day boundary.
            run.clock.advance(consumed)
            run.append_event("time_advanced", {"worldTime": run.clock.as_dict()})
            remaining -= consumed
            await self._process_due_locked(run)
            if remaining and run.pending_day_end is not None:
                break
            if remaining and run.clock.current.clock_minutes == self.registry.end_hour * 60 + self.registry.end_minute and not run.clock.is_ended:
                if run.clock.current.day < self.registry.end_day:
                    run.clock.current = WorldTime(day=run.clock.current.day + 1, hour=self.registry.active_start_minutes // 60, minute=self.registry.active_start_minutes % 60)
                    self._refresh_daily_schedule_locked(run)
                    run.append_event("world_day_started", {"worldTime": run.clock.as_dict()})
                    for npc in self.registry.npcs:
                        run.fresh_event_context[npc.actor_id] = []
                    await self._process_due_locked(run)

    async def _process_due_locked(self, run: Run) -> None:
        current = self._time_absolute(run)
        # Request expiration is a deterministic boundary action.  It runs
        # before any operation at/after 17:00, including a response command
        # issued without a world step in between.
        self._expire_pending_requests_locked(run)

        # Process the due timeline in chronological order.  The priority of a
        # script event is lower than an NPC thought at the same minute, so a
        # world event always updates visibility and fresh context first.  This
        # also preserves the intuitive order when a caller resumes a run with
        # several due points already accumulated.
        schedule = self._schedule_for_day(run, run.clock.current.day)
        due_items: list[tuple[int, int, str, Any]] = []
        for event in self.registry.events:
            event_abs = self._event_absolute(event)
            if event.event_id not in run.fired_event_ids and event_abs <= current:
                due_items.append((event_abs, 0, "event", event))
        for npc in self.registry.npcs:
            thought_days = run.thought_days.get(npc.actor_id, set())
            if run.clock.current.day in thought_days:
                continue
            thought_minute = schedule.get(npc.actor_id)
            if thought_minute is None:
                continue
            thought_abs = self._absolute(
                run.clock.current.day,
                thought_minute // 60,
                thought_minute % 60,
            )
            if thought_abs <= current:
                due_items.append((thought_abs, 1, "thought", npc))

        for _scheduled_abs, _priority, kind, item in sorted(
            due_items,
            key=lambda entry: (entry[0], entry[1], getattr(entry[3], "actor_id", "")),
        ):
            if run.run_finished:
                return
            if kind == "event":
                event = item
                if event.world_day == self.registry.end_day and event.at == f"{self.registry.end_hour:02d}:{self.registry.end_minute:02d}":
                    await self._finish_chapter_locked(run, event)
                    return
                if not self._event_condition_met(run, event):
                    run.fired_event_ids.add(event.event_id)
                    continue
                await self._apply_world_event_locked(run, event)
                continue

            npc = item
            run.thought_days.setdefault(npc.actor_id, set()).add(run.clock.current.day)
            if run.actor_states.get(npc.actor_id, {}).get("status") == "departed":
                continue
            if run.actor_open_conversation(npc.actor_id) is not None:
                run.append_event(
                    "npc_thought_skipped",
                    {
                        "actorId": npc.actor_id,
                        "reason": "already_in_conversation",
                        "worldTime": run.clock.as_dict(),
                    },
                )
                continue
            if (
                run.actor_states.get(npc.actor_id, {}).get("status") in {"chatting", "approaching", "inviting"}
                or self._actor_has_pending_request(run, npc.actor_id)
            ):
                run.append_event(
                    "npc_thought_skipped",
                    {
                        "actorId": npc.actor_id,
                        "reason": "invitation_pending",
                        "worldTime": run.clock.as_dict(),
                    },
                )
                continue
            run.actor_states[npc.actor_id]["status"] = "waiting"
            run.append_event("npc_thought_started", {"actorId": npc.actor_id, "worldTime": run.clock.as_dict()})
            await self._daily_action_locked(run, npc.actor_id)

        # Ordinary days stop at 18:00 as well.  Day7's deadline event takes
        # the branch above and performs its own close/consolidate-before-
        # resolution sequence.
        if (
            not run.run_finished
            and run.clock.current.clock_minutes >= self.registry.end_hour * 60 + self.registry.end_minute
        ):
            if run.active_chat_pipelines:
                # A ChatDecision/Speech already granted before 18:00 owns the
                # short grace period.  Do not hold world_step on the rest of
                # its recursive chain; the root pipeline will close and
                # consolidate once that one in-flight call returns.
                run.pending_day_end = (run.clock.current.day, "day_end")
            else:
                await self._close_day_locked(run, run.clock.current.day, "day_end")

    def _actor_has_pending_invitation(self, run: Run, actor_id: str) -> bool:
        return any(
            invitation.get("status") == "pending"
            and actor_id
            in {
                invitation.get("initiatorActorId"),
                invitation.get("targetActorId"),
            }
            for invitation in run.invitations.values()
        )

    def _actor_has_pending_join_request(self, run: Run, actor_id: str) -> bool:
        return any(
            request.get("status") == "pending"
            and (
                request.get("applicantActorId") == actor_id
                or actor_id in request.get("approverActorIds", [])
            )
            for request in run.join_requests.values()
        )

    def _actor_has_pending_request(self, run: Run, actor_id: str) -> bool:
        return self._actor_has_pending_invitation(
            run,
            actor_id,
        ) or self._actor_has_pending_join_request(run, actor_id)

    def _expire_pending_requests_locked(self, run: Run) -> None:
        """Expire all unanswered requests once the daily cutoff is reached."""

        if run.clock.current.clock_minutes < run.clock.new_chat_cutoff_minutes:
            return
        now = run.clock.as_dict()["label"]
        for invitation in run.invitations.values():
            if invitation.get("status") != "pending":
                continue
            invitation["status"] = "expired"
            invitation["expiredAt"] = now
            initiator_id = invitation.get("initiatorActorId")
            if initiator_id in run.actor_states:
                actor = self.registry.actor(initiator_id)
                if actor is not None:
                    run.actor_states[initiator_id]["status"] = (
                        "present" if actor.kind == "player" else "waiting"
                    )
            run.append_event(
                "invitation_request_cleared",
                {"invitationId": invitation.get("invitationId"), "reason": "expired"},
            )
            run.append_event(
                "invitation_expired",
                {
                    "invitationId": invitation.get("invitationId"),
                    "initiatorActorId": invitation.get("initiatorActorId"),
                    "targetActorId": invitation.get("targetActorId"),
                    "expiredAt": now,
                },
            )
        for join_request in run.join_requests.values():
            if join_request.get("status") == "pending":
                self._expire_join_request_locked(
                    run,
                    join_request,
                    reason="new_chat_cutoff",
                )

    def _validate_new_chat_window_locked(self, run: Run) -> None:
        """Enforce the backend's 17:00 new-chat cutoff."""

        if run.clock.current.clock_minutes >= run.clock.active_end_minutes:
            raise InvalidInvitationError(
                "The world day has ended; no chat operation is accepted.",
                details={"worldTime": run.clock.as_dict(), "reason": "day_end"},
            )
        if not run.clock.new_chat_allowed:
            raise InvalidInvitationError(
                "New invitations and conversation expansion are closed after 17:00.",
                details={
                    "worldTime": run.clock.as_dict(),
                    "newChatCutoff": "17:00",
                    "reason": "new_chat_cutoff",
                },
            )

    def _require_chat_open_locked(self, run: Run, *, operation: str) -> None:
        """Reject player chat mutations at/after the 18:00 hard boundary."""

        if run.clock.current.clock_minutes < run.clock.active_end_minutes:
            return
        if operation == "message":
            raise InvalidMessageError(
                "The world day has ended; new messages are no longer accepted.",
            )
        raise InvalidConversationParticipantsError(
            "The world day has ended; chat operations are no longer accepted.",
        )

    async def _close_day_locked(self, run: Run, day: int, reason: str) -> None:
        """Force-close all conversations and consolidate every NPC once."""

        if day in run.closed_days:
            return
        if run.active_chat_pipelines:
            run.pending_day_end = (day, reason)
            return
        run.closed_days.add(day)
        for conversation in list(run.open_conversations()):
            await self._close_conversation_locked(run, conversation, reason)
        run.append_event(
            "world_day_ended",
            {"worldTime": run.clock.as_dict(), "reason": reason},
        )

    async def _apply_world_event_locked(self, run: Run, event: Any) -> None:
        run.fired_event_ids.add(event.event_id)
        visible: list[str]
        if isinstance(event.visible_actor_ids, str):
            visible = [
                actor_id for actor_id, position in run.positions.items()
                if math.hypot(position["x"], position["y"]) <= 6
                and run.actor_states.get(actor_id, {}).get("status") != "departed"
            ]
        else:
            visible = list(event.visible_actor_ids)
        run.world_events[event.event_id] = {
            "eventId": event.event_id,
            "worldDay": event.world_day,
            "at": event.at,
            "visibility": event.visibility,
            "sourceLabel": event.source_label,
            "summary": event.summary,
            "visibleActorIds": visible,
        }
        for key, value in event.current_world_state_changes:
            if event.visibility == "public":
                run.current_world_state[key] = deepcopy(value)
            else:
                run.scene_state[key] = deepcopy(value)
                for actor_id in visible:
                    run.actor_world_state.setdefault(actor_id, {})[key] = deepcopy(value)
        if event.visibility == "public" or self.registry.player_actor_id in visible:
            public_event = {
                key: deepcopy(value)
                for key, value in run.world_events[event.event_id].items()
                if key != "visibleActorIds"
            }
            run.append_event(
                "world_event_occurred",
                {"event": public_event, "worldTime": run.clock.as_dict()},
            )
        for actor_id in visible:
            actor = self.registry.actor(actor_id)
            if actor is None or actor.kind != "npc":
                continue
            memory_id = run.next_memory_identity()
            run.memories[memory_id] = {
                "memoryId": memory_id,
                "ownerNpcId": actor_id,
                "type": "event",
                "content": event.summary,
                "actorIds": [item for item in visible if item != actor_id],
                "topicIds": list(event.topic_ids),
                "importance": 4,
                "confidence": "high",
                "source": "world_event",
                "eventId": event.event_id,
                "createdAt": run.clock.as_dict()["label"],
                "evidenceMessageIds": [],
            }
            run.fresh_event_context.setdefault(actor_id, []).append(memory_id)

    @staticmethod
    def _event_condition_met(run: Run, event: Any) -> bool:
        condition = event.trigger_condition
        if condition is None:
            return True
        if "==" not in condition:
            return False
        left, expected_text = (part.strip() for part in condition.split("==", 1))
        prefix = "currentWorldState."
        if not left.startswith(prefix):
            return False
        key = left[len(prefix):]
        expected = expected_text.strip("\"'")
        actual = run.scene_state.get(key, run.current_world_state.get(key))
        if isinstance(actual, bool):
            return expected.lower() == str(actual).lower()
        return str(actual) == expected

    def _npc_agent(self, npc_id: str) -> NPCAgent:
        """Return the logical Agent bound to ``npc_id``.

        The registry owns the binding (and therefore the private tool
        permission).  Keeping this lookup here makes it explicit that the
        world service never constructs an Agent per message.
        """

        return self.agents.get(npc_id)

    def _memory_tool_context(
        self,
        run: Run,
        npc_id: str,
        conversation_id: str | None = None,
    ) -> MemoryToolContext:
        """Build an isolated, read-only snapshot for an Agent invocation."""

        return MemoryToolContext(
            owner_npc_id=npc_id,
            run_id=run.run_id,
            conversation_id=conversation_id,
            # Foreign private memories never enter this Agent's Graph State.
            # The tool repeats the owner check as a hard boundary, but the
            # runtime snapshot is already least-privilege before execution.
            memories={
                memory_id: deepcopy(memory)
                for memory_id, memory in run.memories.items()
                if memory.get("ownerNpcId") == npc_id
            },
            # ScenarioRegistry exposes mappingproxy values; copy into a
            # plain dict before handing the snapshot to the Agent tool.
            topics={
                topic_id: deepcopy(topic)
                for topic_id, topic in self.registry.topics.items()
            },
        )

    async def _daily_action_locked(self, run: Run, npc_id: str) -> None:
        actor = self.registry.actor(npc_id)
        if actor is None or actor.kind != "npc":
            return
        if not run.clock.new_chat_allowed:
            run.actor_states[npc_id]["status"] = "waiting"
            run.append_event(
                "npc_waited",
                {"actorId": npc_id, "reason": "new_chat_cutoff", "worldTime": run.clock.as_dict()},
            )
            return
        active_goals = [goal for goal in run.goals.values() if goal["ownerNpcId"] == npc_id and goal["status"] in {"active", "blocked"}]
        candidates: list[str] = []
        open_count = len(run.open_conversations())
        for candidate in self.registry.actors.values():
            if candidate.actor_id == npc_id or run.actor_states.get(candidate.actor_id, {}).get("status") == "departed":
                continue
            if self._actor_has_pending_request(run, candidate.actor_id):
                continue
            candidate_conversation = run.actor_open_conversation(candidate.actor_id)
            if candidate_conversation is not None and len(candidate_conversation.participants) < 3:
                candidates.append(candidate.actor_id)
            elif candidate_conversation is None and open_count < 2:
                candidates.append(candidate.actor_id)
        prompt = self._npc_prompt(
            run,
            npc_id,
            "daily_action",
            {
                "activeGoals": active_goals,
                "candidateActorIds": candidates,
                "candidateStates": {
                    actor_id: self._candidate_state(run, actor_id)
                    for actor_id in candidates
                },
                "priorConversationCounts": self._prior_conversation_counts(
                    run,
                    npc_id,
                ),
                "memoryCache": self._initial_memory_ids(run, npc_id, candidates),
            },
        )
        decision_day = run.clock.current.day
        try:
            agent_result = await self._await_model_without_run_lock(
                run,
                self._npc_agent(npc_id).daily_tick(
                    AgentInvocation(
                        run_id=run.run_id,
                        npc_id=npc_id,
                        event_type="daily_tick",
                        prompt=prompt,
                        candidate_actor_ids=tuple(candidates),
                        memory_cache=tuple(self._initial_memory_ids(run, npc_id, candidates)),
                        memory_context=self._memory_tool_context(run, npc_id),
                    )
                )
            )
            decision = cast(DailyActionDecision, agent_result.decision)
        except StructuredCallFailed:
            decision = DailyActionDecision(action="wait")
        if (
            run.run_finished
            or run.clock.current.day != decision_day
            or not run.clock.new_chat_allowed
        ):
            run.actor_states[npc_id]["status"] = "waiting"
            return
        if decision.action == "wait":
            run.actor_states[npc_id]["status"] = "waiting"
            run.append_event("npc_waited", {"actorId": npc_id, "worldTime": run.clock.as_dict()})
            return
        if not decision.goal_id or not decision.target_actor_id:
            run.actor_states[npc_id]["status"] = "waiting"
            run.append_event("npc_waited", {"actorId": npc_id, "reason": "invalid_action"})
            return
        goal = run.goals.get(decision.goal_id)
        target = self.registry.actor(decision.target_actor_id)
        if (
            goal is None
            or goal["ownerNpcId"] != npc_id
            or goal["status"] not in {"active", "blocked"}
            or target is None
            or target.actor_id not in candidates
        ):
            run.actor_states[npc_id]["status"] = "waiting"
            run.append_event("npc_waited", {"actorId": npc_id, "reason": "invalid_target"})
            return
        if run.actor_open_conversation(npc_id) is not None:
            return
        run.actor_states[npc_id]["status"] = "approaching"
        run.append_event("actor_movement_started", {"actorId": npc_id, "targetActorId": target.actor_id})
        run.positions[npc_id] = deepcopy(run.positions.get(target.actor_id, {"x": 0, "y": 0}))
        run.actor_states[npc_id]["status"] = "inviting"
        run.append_event("actor_movement_completed", {"actorId": npc_id, "targetActorId": target.actor_id, "position": deepcopy(run.positions[npc_id])})
        open_target = run.actor_open_conversation(target.actor_id)
        if open_target is not None and len(open_target.participants) < 3:
            await self._request_join_locked(run, open_target, npc_id)
            return
        if open_target is not None:
            run.actor_states[npc_id]["status"] = "waiting"
            run.append_event("npc_waited", {"actorId": npc_id, "reason": "target_busy"})
            return
        await self._request_invitation_locked(
            run,
            npc_id,
            target.actor_id,
            private_goal_id=decision.goal_id,
            private_intent=decision.intent,
        )

    async def _request_invitation_locked(
        self,
        run: Run,
        initiator_id: str,
        target_id: str,
        *,
        private_goal_id: str | None = None,
        private_intent: str | None = None,
    ) -> dict[str, Any]:
        self._expire_pending_requests_locked(run)
        self._validate_new_invitation_locked(run, initiator_id, target_id)

        invitation_id = run.next_invitation_identity()
        invitation = {
            "invitationId": invitation_id,
            "initiatorActorId": initiator_id,
            "targetActorId": target_id,
            "status": "pending",
            "requestedAt": run.clock.as_dict()["label"],
            "_goalId": private_goal_id,
            "_intent": private_intent,
        }
        run.invitations[invitation_id] = invitation
        run.actor_states[initiator_id]["status"] = "inviting"
        run.append_event("invitation_requested", {"invitationId": invitation_id, "initiatorActorId": initiator_id, "targetActorId": target_id})
        if target_id == self.registry.player_actor_id:
            return invitation
        target_prompt = self._npc_prompt(
            run,
            target_id,
            "invitation",
            {
                "initiatorActorId": initiator_id,
                "visibleRequest": True,
                "memoryCache": self._initial_memory_ids(
                    run,
                    target_id,
                    [initiator_id],
                ),
            },
        )
        try:
            agent_result = await self._await_model_without_run_lock(
                run,
                self._npc_agent(target_id).invitation_received(
                    AgentInvocation(
                        run_id=run.run_id,
                        npc_id=target_id,
                        event_type="invitation_received",
                        prompt=target_prompt,
                        candidate_actor_ids=(initiator_id,),
                        memory_cache=tuple(
                            self._initial_memory_ids(run, target_id, [initiator_id])
                        ),
                        memory_context=self._memory_tool_context(run, target_id),
                    )
                )
            )
            decision = cast(InvitationDecision, agent_result.decision)
        except StructuredCallFailed:
            decision = InvitationDecision(decision="refuse")
        if invitation.get("status") != "pending":
            return invitation
        if (
            not run.clock.new_chat_allowed
            or run.actor_open_conversation(initiator_id) is not None
            or run.actor_open_conversation(target_id) is not None
        ):
            invitation["status"] = "expired"
            invitation["respondedAt"] = run.clock.as_dict()["label"]
            run.append_event(
                "invitation_request_cleared",
                {"invitationId": invitation_id},
            )
            run.actor_states[initiator_id]["status"] = (
                "present" if initiator_id == self.registry.player_actor_id else "waiting"
            )
            return invitation
        if decision.decision == "accept":
            invitation["status"] = "accepted"
            invitation["respondedAt"] = run.clock.as_dict()["label"]
            run.append_event("invitation_request_cleared", {"invitationId": invitation_id})
            conversation = self._open_conversation_locked(run, [initiator_id, target_id], opening_speech=initiator_id != self.registry.player_actor_id)
            invitation["conversationId"] = conversation.conversation_id
            run.append_event("invitation_accepted", {"invitationId": invitation_id, "conversationId": conversation.conversation_id})
            if initiator_id != self.registry.player_actor_id:
                await self._generate_opening_speech_locked(
                    run,
                    conversation,
                    initiator_id,
                    goal_id=private_goal_id,
                    intent=private_intent,
                )
            return invitation
        invitation["status"] = "refused"
        invitation["respondedAt"] = run.clock.as_dict()["label"]
        run.append_event("invitation_request_cleared", {"invitationId": invitation_id})
        run.append_event("invitation_refused", {"invitationId": invitation_id, "targetActorId": target_id})
        run.actor_states[initiator_id]["status"] = (
            "present" if initiator_id == self.registry.player_actor_id else "waiting"
        )
        return invitation

    async def _request_join_locked(
        self,
        run: Run,
        conversation: Conversation,
        applicant_actor_id: str,
    ) -> dict[str, Any]:
        self._require_active_run(run)
        self._validate_new_chat_window_locked(run)
        self._require_actor(applicant_actor_id)
        if not conversation.is_open:
            raise InvalidJoinRequestError("Conversation is already closed.")
        if len(conversation.participants) >= 3:
            raise ConversationFullError()
        if applicant_actor_id in conversation.participants:
            raise ActorAlreadyInConversationError(
                details={"actorId": applicant_actor_id}
            )
        if run.actor_states.get(applicant_actor_id, {}).get("status") == "departed":
            raise InvalidConversationParticipantsError(
                "A departed actor cannot request to join."
            )
        if run.actor_open_conversation(applicant_actor_id) is not None:
            raise ActorAlreadyInConversationError(
                details={"actorId": applicant_actor_id}
            )
        if self._actor_has_pending_invitation(run, applicant_actor_id):
            raise InvalidInvitationError(
                "The applicant must answer or finish the pending invitation first."
            )
        if self._actor_has_pending_join_request(run, applicant_actor_id):
            raise InvalidJoinRequestError(
                "The applicant already has a pending join request."
            )
        if any(
            request.get("status") == "pending"
            and request.get("conversationId") == conversation.conversation_id
            for request in run.join_requests.values()
        ):
            raise InvalidJoinRequestError(
                "This conversation already has a pending join request."
            )

        approvers = list(conversation.participants)
        join_request_id = run.next_join_request_identity()
        join_request: dict[str, Any] = {
            "joinRequestId": join_request_id,
            "conversationId": conversation.conversation_id,
            "applicantActorId": applicant_actor_id,
            "status": "pending",
            "requestedAt": run.clock.as_dict()["label"],
            "approverActorIds": approvers,
            "approverDecisions": {
                actor_id: "pending" for actor_id in approvers
            },
        }
        run.join_requests[join_request_id] = join_request
        applicant = self.registry.actor(applicant_actor_id)
        run.actor_states[applicant_actor_id]["status"] = "inviting"
        run.append_event(
            "join_request_created",
            {
                "joinRequest": self._public_join_request(join_request),
            },
        )

        npc_approvals: list[tuple[str, AgentInvocation]] = []
        for approver_id in approvers:
            approver = self.registry.actor(approver_id)
            if approver is None:
                self._refuse_join_request_locked(run, join_request)
                return join_request
            if approver.kind == "player":
                continue
            memory_ids = self._initial_memory_ids(
                run,
                approver_id,
                [applicant_actor_id, *approvers],
            )
            prompt = self._npc_prompt(
                run,
                approver_id,
                "join_request",
                {
                    "requestKind": "join_request",
                    "joinRequestId": join_request_id,
                    "conversationId": conversation.conversation_id,
                    "applicant": (
                        self.registry.public_actor(applicant_actor_id)
                        if applicant is not None
                        else {"actorId": applicant_actor_id}
                    ),
                    "participantActorIds": approvers,
                    "visibleRequest": True,
                    "memoryCache": memory_ids,
                },
            )
            npc_approvals.append(
                (
                    approver_id,
                    AgentInvocation(
                        run_id=run.run_id,
                        npc_id=approver_id,
                        event_type="invitation_received",
                        prompt=prompt,
                        conversation_id=conversation.conversation_id,
                        candidate_actor_ids=(applicant_actor_id,),
                        memory_cache=tuple(memory_ids),
                        memory_context=self._memory_tool_context(
                            run,
                            approver_id,
                            conversation.conversation_id,
                        ),
                    ),
                )
            )

        if npc_approvals:
            raw_approvals = await self._await_model_without_run_lock(
                run,
                asyncio.gather(
                    *(
                        self._npc_agent(approver_id).invitation_received(invocation)
                        for approver_id, invocation in npc_approvals
                    ),
                    return_exceptions=True,
                ),
            )
        else:
            raw_approvals = []
        if (
            join_request.get("status") != "pending"
            or not conversation.is_open
            or list(conversation.participants) != approvers
        ):
            return join_request
        for (approver_id, _), agent_result in zip(
            npc_approvals,
            raw_approvals,
            strict=True,
        ):
            if isinstance(agent_result, BaseException):
                decision = InvitationDecision(decision="refuse")
            else:
                decision = cast(InvitationDecision, agent_result.decision)
            join_request["approverDecisions"][approver_id] = decision.decision
            if decision.decision == "refuse":
                self._refuse_join_request_locked(run, join_request)
                return join_request

        await self._resolve_join_request_locked(run, join_request)
        return join_request

    async def _resolve_join_request_locked(
        self,
        run: Run,
        join_request: dict[str, Any],
    ) -> None:
        if join_request.get("status") != "pending":
            return
        decisions = join_request["approverDecisions"]
        if "refuse" in decisions.values():
            self._refuse_join_request_locked(run, join_request)
            return
        if any(value != "accept" for value in decisions.values()):
            return
        if not run.clock.new_chat_allowed:
            self._expire_join_request_locked(
                run,
                join_request,
                reason="new_chat_cutoff",
            )
            return
        conversation = run.conversations.get(join_request["conversationId"])
        applicant_id = str(join_request["applicantActorId"])
        if (
            conversation is None
            or not conversation.is_open
            or len(conversation.participants) >= 3
            or conversation.participants != join_request["approverActorIds"]
            or run.actor_open_conversation(applicant_id) is not None
            or run.actor_states.get(applicant_id, {}).get("status") == "departed"
        ):
            self._expire_join_request_locked(
                run,
                join_request,
                reason="request_invalidated",
            )
            return

        join_request["status"] = "accepted"
        join_request["resolvedAt"] = run.clock.as_dict()["label"]
        run.append_event(
            "join_request_resolved",
            {
                "joinRequestId": join_request["joinRequestId"],
                "conversationId": conversation.conversation_id,
                "applicantActorId": applicant_id,
                "status": "accepted",
                "resolvedAt": join_request["resolvedAt"],
            },
        )
        await self._join_conversation_locked(run, conversation, applicant_id)

    def _refuse_join_request_locked(
        self,
        run: Run,
        join_request: dict[str, Any],
    ) -> None:
        if join_request.get("status") != "pending":
            return
        join_request["status"] = "refused"
        join_request["resolvedAt"] = run.clock.as_dict()["label"]
        self._reset_join_applicant_locked(run, join_request)
        run.append_event(
            "join_request_resolved",
            {
                "joinRequestId": join_request["joinRequestId"],
                "conversationId": join_request["conversationId"],
                "applicantActorId": join_request["applicantActorId"],
                "status": "refused",
                "resolvedAt": join_request["resolvedAt"],
            },
        )

    def _expire_join_request_locked(
        self,
        run: Run,
        join_request: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        if join_request.get("status") != "pending":
            return
        join_request["status"] = "expired"
        join_request["expiredAt"] = run.clock.as_dict()["label"]
        self._reset_join_applicant_locked(run, join_request)
        run.append_event(
            "join_request_resolved",
            {
                "joinRequestId": join_request["joinRequestId"],
                "conversationId": join_request["conversationId"],
                "applicantActorId": join_request["applicantActorId"],
                "status": "expired",
                "reason": reason,
                "expiredAt": join_request["expiredAt"],
            },
        )

    def _expire_join_requests_for_conversation_locked(
        self,
        run: Run,
        conversation_id: str,
        *,
        reason: str,
    ) -> None:
        for join_request in run.join_requests.values():
            if (
                join_request.get("status") == "pending"
                and join_request.get("conversationId") == conversation_id
            ):
                self._expire_join_request_locked(
                    run,
                    join_request,
                    reason=reason,
                )

    def _reset_join_applicant_locked(
        self,
        run: Run,
        join_request: dict[str, Any],
    ) -> None:
        applicant_id = str(join_request["applicantActorId"])
        actor = self.registry.actor(applicant_id)
        if actor is None or applicant_id not in run.actor_states:
            return
        run.actor_states[applicant_id]["status"] = (
            "present" if actor.kind == "player" else "waiting"
        )

    def _validate_new_invitation_locked(
        self,
        run: Run,
        initiator_id: str,
        target_id: str,
    ) -> None:
        self._require_active_run(run)
        self._validate_new_chat_window_locked(run)
        if initiator_id == target_id:
            raise InvalidInvitationError("An actor cannot invite itself.")
        if run.actor_states.get(initiator_id, {}).get("status") == "departed":
            raise InvalidInvitationError("The initiator has left the world.")
        if run.actor_states.get(target_id, {}).get("status") == "departed":
            raise InvalidInvitationError("The target has left the world.")
        if self._actor_has_pending_join_request(
            run,
            initiator_id,
        ) or self._actor_has_pending_join_request(run, target_id):
            raise InvalidInvitationError(
                "One of the actors already has a pending join request."
            )
        if run.actor_open_conversation(initiator_id) is not None:
            raise ActorAlreadyInConversationError(details={"actorId": initiator_id})
        if run.actor_open_conversation(target_id) is not None:
            raise InvalidInvitationError("The target is already in a conversation.")
        if len(run.open_conversations()) >= 2:
            raise ConversationLimitReachedError()
        if any(
            invitation.get("status") == "pending"
            and (
                initiator_id
                in {
                    invitation.get("initiatorActorId"),
                    invitation.get("targetActorId"),
                }
                or target_id
                in {
                    invitation.get("initiatorActorId"),
                    invitation.get("targetActorId"),
                }
            )
            for invitation in run.invitations.values()
        ):
            raise InvalidInvitationError("One of the actors already has a pending invitation.")

    def _validate_conversation_start_locked(
        self,
        run: Run,
        participant_ids: list[str],
    ) -> None:
        self._require_active_run(run)
        self._validate_new_chat_window_locked(run)
        if len(run.open_conversations()) >= 2:
            raise ConversationLimitReachedError()
        for actor_id in participant_ids:
            if run.actor_states.get(actor_id, {}).get("status") == "departed":
                raise InvalidInvitationError("A departed actor cannot start a conversation.")
            if run.actor_open_conversation(actor_id) is not None:
                raise ActorAlreadyInConversationError(details={"actorId": actor_id})

    def _open_conversation_locked(self, run: Run, participant_ids: list[str], *, opening_speech: bool) -> Conversation:
        self._validate_conversation_start_locked(run, participant_ids)
        conversation_id, creation_seq = run.next_conversation_identity()
        conversation = Conversation(conversation_id=conversation_id, creation_seq=creation_seq, participants=list(participant_ids))
        run.conversations[conversation_id] = conversation
        run.messages[conversation_id] = []
        run.segments[conversation_id] = [{"segmentId": run.next_segment_identity(), "participants": list(participant_ids), "startedAt": run.clock.as_dict()["label"], "summary": None, "summaryThroughMessageId": None}]
        run.conversation_drafts[conversation_id] = {
            actor_id: {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            }
            for actor_id in participant_ids
            if actor_id in {npc.actor_id for npc in self.registry.npcs}
        }
        for actor_id in participant_ids:
            run.actor_states[actor_id]["status"] = "chatting"
            actor = self.registry.actor(actor_id)
            run.memory_cache[(conversation_id, actor_id)] = (
                set(self._initial_memory_ids(run, actor_id, participant_ids))
                if actor is not None and actor.kind == "npc"
                else set()
            )
        initiator_id = participant_ids[0]
        state = self._round_state_locked(run, conversation)
        initiator = self.registry.actor(initiator_id)
        if opening_speech and initiator is not None and initiator.kind == "npc":
            state.update(
                {
                    "status": "opener",
                    "openerActorId": initiator_id,
                    "openerKind": "conversation_opener",
                }
            )
            self._ensure_chat_task(run, conversation_id)
        else:
            state.update(
                {
                    "status": "awaiting_player_opener",
                    "awaitingPlayerOpener": True,
                }
            )
        run.append_event("conversation_created", {"conversation": conversation.to_public_dict()})
        return conversation

    async def _generate_opening_speech_locked(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
        *,
        goal_id: str | None = None,
        intent: str | None = None,
    ) -> None:
        if run.clock.current.clock_minutes >= run.clock.active_end_minutes:
            return
        state = self._round_state_locked(run, conversation)
        state.update(
            {
                "roundVersion": int(state.get("roundVersion", 0)) + 1,
                "status": "opener",
                "openerActorId": npc_id,
                "openerKind": "conversation_opener",
                "openerGoalId": goal_id,
                "openerIntent": intent,
                "awaitingPlayerOpener": False,
            }
        )
        self._ensure_chat_task(run, conversation.conversation_id)

    async def _join_conversation_locked(self, run: Run, conversation: Conversation, actor_id: str) -> None:
        if not conversation.is_open or len(conversation.participants) >= 3:
            raise ConversationFullError()
        self._validate_new_chat_window_locked(run)
        if run.actor_open_conversation(actor_id) is not None:
            raise ActorAlreadyInConversationError(details={"actorId": actor_id})
        if run.actor_states.get(actor_id, {}).get("status") == "departed":
            raise InvalidConversationParticipantsError(
                "A departed actor cannot join a conversation."
            )
        if any(
            invitation.get("status") == "pending"
            and actor_id
            in {
                invitation.get("initiatorActorId"),
                invitation.get("targetActorId"),
            }
            for invitation in run.invitations.values()
        ):
            raise InvalidInvitationError(
                "The actor must answer or finish the pending invitation first."
            )
        await self._close_current_segment_locked(run, conversation)
        conversation.add_participant(actor_id)
        run.segments[conversation.conversation_id].append({"segmentId": run.next_segment_identity(), "participants": list(conversation.participants), "startedAt": run.clock.as_dict()["label"], "summary": None, "summaryThroughMessageId": None})
        if self.registry.actor(actor_id) is not None and self.registry.actor(actor_id).kind == "npc":  # type: ignore[union-attr]
            run.conversation_drafts.setdefault(conversation.conversation_id, {})[actor_id] = {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            }
        joined_actor = self.registry.actor(actor_id)
        run.memory_cache[(conversation.conversation_id, actor_id)] = (
            set(self._initial_memory_ids(run, actor_id, conversation.participants))
            if joined_actor is not None and joined_actor.kind == "npc"
            else set()
        )
        run.actor_states[actor_id]["status"] = "chatting"
        run.append_event("conversation_participant_joined", {"conversation": conversation.to_public_dict(), "actorJoined": actor_id})
        run.idle_counts[conversation.conversation_id] = 0
        if joined_actor is not None and joined_actor.kind == "npc":
            self._reset_round_for_participant_change_locked(
                run,
                conversation,
                status="opener",
                opener_actor_id=actor_id,
                opener_kind="join_opener",
            )
            self._ensure_chat_task(run, conversation.conversation_id)
        else:
            self._reset_round_for_participant_change_locked(
                run,
                conversation,
                status="awaiting_player_opener",
                awaiting_player_opener=True,
            )

    async def _close_current_segment_locked(self, run: Run, conversation: Conversation) -> None:
        segments = run.segments.get(conversation.conversation_id, [])
        if not segments or segments[-1].get("endedAt") is not None:
            return
        segment = segments[-1]
        segment["endedAt"] = run.clock.as_dict()["label"]
        segment["summary"] = await self._summarize_segment_locked(run, conversation, segment)

    @staticmethod
    def _empty_segment_summary(participants: list[str]) -> dict[str, Any]:
        return {
            "claims": [],
            "commitments": [],
            "revealedFacts": [],
            "openQuestions": [],
            "actorIds": list(participants),
            "topicHints": [],
        }

    def _segment_messages(
        self,
        run: Run,
        conversation: Conversation,
        segment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return the complete, private source set for one Segment.

        This is intentionally separate from prompt projection.  Messages are
        never deleted or replaced by a summary; the pointer only tells the
        prompt builder which prefix has already been represented by the
        shared summary.
        """

        return [
            self._public_messages([message])[0]
            for message in run.messages.get(conversation.conversation_id, [])
            if message.get("segmentId") == segment.get("segmentId")
        ]

    @staticmethod
    def _messages_after_summary_cursor(
        messages: list[dict[str, Any]],
        cursor: str | None,
    ) -> list[dict[str, Any]]:
        if not cursor:
            return list(messages)
        for index, message in enumerate(messages):
            if message.get("messageId") == cursor:
                return messages[index + 1 :]
        # A missing cursor is treated conservatively as an uncompressed
        # source set.  It is safer to repeat input than to silently hide it.
        return list(messages)

    @staticmethod
    def _approximate_message_tokens(messages: list[dict[str, Any]]) -> int:
        """Estimate prompt tokens without adding a provider-specific tokenizer.

        CJK characters are conservatively counted one-for-one. Other text is
        estimated at four characters per token, plus a small per-message
        envelope for role and metadata. Exact provider token accounting is not
        required here: this threshold exists to summarize unusually long
        messages before the deterministic message-count threshold is reached.
        """

        total = 0
        for message in messages:
            text = str(message.get("text", ""))
            cjk_count = sum(
                1
                for character in text
                if (
                    "\u3400" <= character <= "\u4dbf"
                    or "\u4e00" <= character <= "\u9fff"
                    or "\uf900" <= character <= "\ufaff"
                )
            )
            total += cjk_count + math.ceil((len(text) - cjk_count) / 4) + 3
        return total

    def _segment_summary_entries(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "segmentId": segment.get("segmentId"),
                "summary": deepcopy(segment.get("summary")),
            }
            for segment in run.segments.get(conversation.conversation_id, [])
            if npc_id in segment.get("participants", [])
            and segment.get("summary") is not None
        ]

    async def _maybe_roll_segment_summary_locked(
        self,
        run: Run,
        conversation: Conversation,
    ) -> None:
        """Compress the current Segment once its un-summarized tail is long.

        The model sees the prior neutral summary plus the older part of the
        uncompressed tail, while the configured recent source messages remain
        verbatim. The roll is triggered by either message count or an
        approximate token budget. A failed call leaves both the summary and
        its cursor untouched so a later message can retry it.
        """

        segments = run.segments.get(conversation.conversation_id, [])
        if not conversation.is_open or not segments:
            return
        segment = segments[-1]
        if segment.get("endedAt") is not None:
            return
        source_messages = self._segment_messages(run, conversation, segment)
        unsummarized = self._messages_after_summary_cursor(
            source_messages,
            segment.get("summaryThroughMessageId"),
        )
        message_limit_reached = (
            len(unsummarized) > self.segment_summary_trigger_messages
        )
        token_limit_reached = (
            len(unsummarized) > self.segment_summary_recent_messages
            and self._approximate_message_tokens(unsummarized)
            > self.segment_summary_trigger_tokens
        )
        if not message_limit_reached and not token_limit_reached:
            return
        to_compress = unsummarized[: -self.segment_summary_recent_messages]
        if not to_compress:
            return
        segment_id = segment.get("segmentId")
        previous_cursor = segment.get("summaryThroughMessageId")
        prompt = json.dumps(
            {
                "protocol": "segment_summary",
                "mode": "rolling",
                "participants": list(segment.get("participants", [])),
                "summary": deepcopy(segment.get("summary")),
                "summaryThroughMessageId": previous_cursor,
                "messages": to_compress,
            },
            ensure_ascii=False,
        )
        try:
            result = await self._await_model_without_run_lock(
                run,
                self.decisions.segment_summary(prompt),
            )
        except StructuredCallFailed:
            return
        if self.decisions.last_failed_protocol == "SegmentSummary":
            return
        current_segments = run.segments.get(conversation.conversation_id, [])
        if (
            not conversation.is_open
            or not current_segments
            or current_segments[-1].get("segmentId") != segment_id
            or current_segments[-1].get("endedAt") is not None
            or current_segments[-1].get("summaryThroughMessageId") != previous_cursor
        ):
            return
        summary = result.model_dump(by_alias=True)
        participants = list(current_segments[-1].get("participants", []))
        summary["actorIds"] = [
            actor_id for actor_id in summary.get("actorIds", [])
            if actor_id in participants
        ]
        current_segments[-1]["summary"] = summary
        current_segments[-1]["summaryThroughMessageId"] = to_compress[-1].get("messageId")

    async def _summarize_segment_locked(
        self,
        run: Run,
        conversation: Conversation,
        segment: dict[str, Any],
    ) -> dict[str, Any]:
        messages = self._segment_messages(run, conversation, segment)
        participants = list(segment.get("participants", []))
        if not messages:
            return self._empty_segment_summary(participants)
        previous_summary = deepcopy(segment.get("summary"))
        remaining = self._messages_after_summary_cursor(
            messages,
            segment.get("summaryThroughMessageId"),
        )
        if previous_summary is not None and not remaining:
            return previous_summary
        prompt = json.dumps(
            {
                "protocol": "segment_summary",
                "mode": "final",
                "summary": previous_summary,
                "summaryThroughMessageId": segment.get("summaryThroughMessageId"),
                "messages": remaining,
                "participants": participants,
            },
            ensure_ascii=False,
        )
        try:
            result = await self._await_model_without_run_lock(
                run,
                self.decisions.segment_summary(prompt),
            )
            if self.decisions.last_failed_protocol == "SegmentSummary":
                return previous_summary or self._empty_segment_summary(participants)
            if segment not in run.segments.get(conversation.conversation_id, []):
                return previous_summary or self._empty_segment_summary(participants)
            summary = result.model_dump(by_alias=True)
            summary["actorIds"] = [
                actor_id for actor_id in summary.get("actorIds", [])
                if actor_id in participants
            ]
            # This is the only successful path that advances the durable
            # pointer.  The raw messages remain in Run.messages forever.
            segment["summaryThroughMessageId"] = messages[-1].get("messageId")
            return summary
        except StructuredCallFailed:
            return previous_summary or self._empty_segment_summary(participants)

    def _write_message_locked(
        self,
        run: Run,
        conversation: Conversation,
        author_id: str,
        text: str,
        *,
        round_id: str | None = None,
        round_sequence: int | None = None,
        reply_to_message_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if not conversation.is_open or author_id not in conversation.participants:
            raise ActorNotInConversationError(details={"actorId": author_id})
        visible_npcs = [actor_id for actor_id in conversation.participants if self.registry.actor(actor_id) is not None and self.registry.actor(actor_id).kind == "npc"]  # type: ignore[union-attr]
        message = {
            "messageId": run.next_message_identity(),
            "conversationId": conversation.conversation_id,
            "authorActorId": author_id,
            "text": text,
            "createdAt": run.clock.as_dict()["label"],
            "segmentId": run.segments[conversation.conversation_id][-1]["segmentId"],
            "visibleToNpcIds": visible_npcs,
        }
        if round_id is not None:
            message["roundId"] = round_id
        if round_sequence is not None:
            message["roundSequence"] = round_sequence
        if reply_to_message_ids:
            message["replyToMessageIds"] = list(dict.fromkeys(reply_to_message_ids))
        run.messages[conversation.conversation_id].append(message)
        public = {"conversationId": conversation.conversation_id, "messageId": message["messageId"], "authorActorId": author_id}
        if self.registry.player_actor_id in conversation.participants:
            public["text"] = text
        if round_id is not None:
            public["roundId"] = round_id
        if round_sequence is not None:
            public["roundSequence"] = round_sequence
        if reply_to_message_ids:
            public["replyToMessageIds"] = list(dict.fromkeys(reply_to_message_ids))
        run.append_event("message_created" if "text" in public else "conversation_activity", public)
        run.idle_counts[conversation.conversation_id] = 0
        return message

    async def _run_chat_pipeline_locked(
        self,
        run: Run,
        conversation: Conversation,
        trigger_message_id: str | None,
        chain_left: int | None = None,
        *,
        _registered: bool = False,
        _allow_idle_reentry: bool = True,
    ) -> None:
        del chain_left, _registered, _allow_idle_reentry
        if trigger_message_id:
            self._queue_message_round_locked(
                run,
                conversation,
                [trigger_message_id],
            )
            self._ensure_chat_task(run, conversation.conversation_id)

    async def _chat_worker(self, run_id: str, conversation_id: str) -> None:
        run = await self.repository.get(run_id)
        if run is None:
            return
        round_lock = run.conversation_round_locks.setdefault(
            conversation_id,
            asyncio.Lock(),
        )
        async with round_lock:
            while True:
                cooldown_wait: float | None = None
                mode: str | None = None
                wake = self._chat_wake_events.setdefault(
                    (run_id, conversation_id),
                    asyncio.Event(),
                )
                async with run.lock:
                    # Clear while holding the same lock used by message
                    # enqueueing. A player message arriving after this point
                    # sets the event and cannot be lost before cooldown wait.
                    wake.clear()
                    conversation = run.conversations.get(conversation_id)
                    if conversation is None or not conversation.is_open or run.run_finished:
                        run.conversation_round_states.pop(conversation_id, None)
                        return
                    state = self._round_state_locked(run, conversation)
                    status = str(state.get("status", "idle"))
                    if status in {"deciding", "generating"}:
                        # A task cannot survive a process restart. Re-run the
                        # frozen trigger batch; no message was published yet.
                        state["status"] = "queued"
                        status = "queued"
                    if status == "publishing" and not state.get("pendingPublications"):
                        recovery = state.get("recovery", {})
                        has_publication_tail = bool(
                            recovery.get("publishedMessageIds")
                            or state.get("pendingLeaverIds")
                            or state.get("pendingPostSpeechLeaverIds")
                            or state.get("queuedMessageIds")
                        )
                        if not has_publication_tail:
                            state["status"] = "idle"
                            status = "idle"
                    if status == "cooldown":
                        due = float(state.get("cooldownDueAt") or time.time())
                        cooldown_wait = max(0.0, due - time.time())
                        mode = "cooldown"
                    elif status == "publishing":
                        mode = "publishing"
                    elif status == "opener":
                        mode = "opener"
                    elif status in {"queued", "final_check"}:
                        mode = "round"
                    elif status in {"idle", "awaiting_player_opener"}:
                        return
                    else:
                        state["status"] = "idle"
                        await self.repository.save(run)
                        return

                if mode == "cooldown":
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=cooldown_wait)
                    except TimeoutError:
                        async with run.lock:
                            conversation = run.conversations.get(conversation_id)
                            if conversation is None or not conversation.is_open:
                                return
                            state = self._round_state_locked(run, conversation)
                            if state.get("status") != "cooldown":
                                continue
                            state["roundVersion"] = int(state.get("roundVersion", 0)) + 1
                            state["status"] = "final_check"
                            state["finalCheckUsed"] = True
                            state["cooldownDueAt"] = None
                            state["syntheticTrigger"] = "final_check"
                            await self.repository.save(run)
                    continue
                if mode == "publishing":
                    await self._publish_pending_round(run, conversation_id)
                    continue
                if mode == "opener":
                    await self._execute_opener_round(run, conversation_id)
                    continue
                if mode == "round":
                    await self._execute_message_round(run, conversation_id)
                    continue

    def _frozen_prompt_builder(
        self,
        prompt: str,
        memory_snapshot: dict[str, dict[str, Any]],
    ) -> Any:
        base = json.loads(prompt)

        def build(memory_ids: list[str]) -> str:
            payload = deepcopy(base)
            payload["protocol"] = "chat_decision_with_memory"
            payload["context"]["memoryCache"] = list(memory_ids)
            payload["memories"] = [
                deepcopy(memory_snapshot[memory_id])
                for memory_id in memory_ids
                if memory_id in memory_snapshot
            ]
            return json.dumps(payload, ensure_ascii=False, default=str)

        return build

    def _prepare_round_invocations_locked(
        self,
        run: Run,
        conversation: Conversation,
        state: dict[str, Any],
        *,
        trigger: str,
        only_actor_id: str | None = None,
    ) -> list[tuple[str, AgentInvocation]]:
        trigger_ids = [str(item) for item in state.get("triggerMessageIds", [])]
        trigger_messages = [
            message
            for message in run.messages.get(conversation.conversation_id, [])
            if message.get("messageId") in set(trigger_ids)
        ]
        trigger_authors = {
            str(message.get("authorActorId")) for message in trigger_messages
        }
        result: list[tuple[str, AgentInvocation]] = []
        for npc_id in list(conversation.participants):
            actor = self.registry.actor(npc_id)
            if actor is None or actor.kind != "npc":
                continue
            if only_actor_id is not None and npc_id != only_actor_id:
                continue
            if trigger_messages and trigger_authors == {npc_id}:
                continue
            cache_key = (conversation.conversation_id, npc_id)
            cached_memory_ids = [
                memory_id
                for memory_id in run.memory_cache.get(cache_key, set())
                if run.memories.get(memory_id, {}).get("ownerNpcId") == npc_id
            ]
            extra = {
                "conversationId": conversation.conversation_id,
                "trigger": trigger,
                "triggerMessageId": trigger_ids[-1] if trigger_ids else None,
                "triggerMessageIds": trigger_ids,
                "roundId": state.get("roundId"),
                **self._chat_context(run, conversation, npc_id),
                "memoryCache": cached_memory_ids,
            }
            prompt = self._npc_prompt(run, npc_id, "chat_decision", extra)
            memory_snapshot = {
                memory_id: deepcopy(memory)
                for memory_id, memory in run.memories.items()
                if memory.get("ownerNpcId") == npc_id
            }
            result.append(
                (
                    npc_id,
                    AgentInvocation(
                        run_id=run.run_id,
                        npc_id=npc_id,
                        event_type="chat_message_received",
                        prompt=prompt,
                        conversation_id=conversation.conversation_id,
                        trigger_message_id=trigger_ids[-1] if trigger_ids else None,
                        visible_messages=tuple(
                            self._visible_messages(run, conversation, npc_id)
                        ),
                        memory_cache=tuple(cached_memory_ids),
                        memory_context=self._memory_tool_context(
                            run,
                            npc_id,
                            conversation.conversation_id,
                        ),
                        prompt_builder=self._frozen_prompt_builder(
                            prompt,
                            memory_snapshot,
                        ),
                    ),
                )
            )
        return result

    async def _invoke_chat_decision(
        self,
        npc_id: str,
        invocation: AgentInvocation,
    ) -> tuple[str, Any | None]:
        try:
            result = await asyncio.wait_for(
                self._npc_agent(npc_id).chat_message_received(invocation),
                timeout=self.chat_model_call_timeout_seconds,
            )
            return npc_id, result
        except Exception:
            return npc_id, None

    def _round_still_current_locked(
        self,
        run: Run,
        conversation: Conversation,
        *,
        round_id: int,
        round_version: int,
        segment_id: str | None,
        participant_version: int,
    ) -> bool:
        state = self._round_state_locked(run, conversation)
        segments = run.segments.get(conversation.conversation_id, [])
        current_segment = segments[-1].get("segmentId") if segments else None
        return (
            conversation.is_open
            and int(state.get("roundId", -1)) == round_id
            and int(state.get("roundVersion", -1)) == round_version
            and int(state.get("participantVersion", -1)) == participant_version
            and state.get("segmentId") == segment_id
            and current_segment == segment_id
        )

    async def _execute_message_round(self, run: Run, conversation_id: str) -> None:
        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None or not conversation.is_open:
                return
            state = self._round_state_locked(run, conversation)
            final_check = state.get("status") == "final_check"
            if run.clock.current.clock_minutes >= run.clock.active_end_minutes:
                state["status"] = "idle"
                return
            state["roundId"] = int(state.get("roundId", 0)) + 1
            state["recovery"] = {
                "resumeStatus": None,
                "attempt": 0,
                "publishedMessageIds": [],
            }
            round_id = int(state["roundId"])
            round_version = int(state.get("roundVersion", 0))
            segment_id = state.get("segmentId")
            participant_version = int(state.get("participantVersion", 0))
            state["status"] = "deciding"
            trigger = "final_check" if final_check else "normal_round"
            invocations = self._prepare_round_invocations_locked(
                run,
                conversation,
                state,
                trigger=trigger,
            )
            trigger_message_ids = list(state.get("triggerMessageIds", []))
            run.active_chat_pipelines += 1
            await self.repository.save(run)

        try:
            raw_results = await asyncio.gather(
                *(
                    self._invoke_chat_decision(npc_id, invocation)
                    for npc_id, invocation in invocations
                )
            )
            decisions: dict[str, ChatDecision] = {}
            recalled: dict[str, tuple[str, ...]] = {}
            for npc_id, agent_result in raw_results:
                if agent_result is None:
                    decisions[npc_id] = ChatDecision(result="decided", action="wait")
                    continue
                decisions[npc_id] = cast(ChatDecision, agent_result.decision)
                recalled[npc_id] = tuple(agent_result.recalled_memory_ids)

            async with run.lock:
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                state = self._round_state_locked(run, conversation)
                for npc_id in conversation.participants:
                    decision = decisions.get(npc_id)
                    if decision is None:
                        continue
                    for memory_id in recalled.get(npc_id, ()):
                        memory = run.memories.get(memory_id)
                        if memory is not None and memory.get("ownerNpcId") == npc_id:
                            run.memory_cache.setdefault(
                                (conversation_id, npc_id),
                                set(),
                            ).add(memory_id)
                    self._apply_chat_drafts(run, conversation, npc_id, decision)

                leaving_ids = [
                    npc_id
                    for npc_id, decision in decisions.items()
                    if decision.action == "leave_chat"
                    and npc_id in conversation.participants
                ]
                candidates = [
                    (npc_id, decision)
                    for npc_id, decision in decisions.items()
                    if decision.action == "speak"
                    and npc_id in conversation.participants
                ]
                if not candidates or run.clock.current.clock_minutes >= run.clock.active_end_minutes:
                    before_seq = run.event_seq
                    await self._finish_no_speech_round_locked(
                        run,
                        conversation,
                        state,
                        final_check=final_check,
                        leaving_ids=leaving_ids,
                    )
                    events = [event.to_dict() for event in run.events_after(before_seq)]
                    await self.repository.save(run)
                    for event in events:
                        await self.event_hub.publish(run.run_id, event)
                    return

                ordered = self._order_speakers(
                    run,
                    conversation,
                    candidates,
                    trigger_message_ids,
                )
                prompts = [
                    (
                        npc_id,
                        decision,
                        self._npc_prompt(
                            run,
                            npc_id,
                            "speech",
                            {
                                "conversationId": conversation_id,
                                "roundId": round_id,
                                "replyToMessageIds": trigger_message_ids,
                                "intent": decision.intent,
                                **self._chat_context(run, conversation, npc_id),
                            },
                        ),
                    )
                    for npc_id, decision in ordered
                ]
                state["status"] = "generating"
                state["pendingLeaverIds"] = leaving_ids
                state["pendingPostSpeechLeaverIds"] = [
                    npc_id
                    for npc_id, decision in ordered
                    if decision.leave_chat_after_speaking
                ]
                run.in_flight_speech_calls += len(prompts)
                await self.repository.save(run)

            speech_results = await asyncio.gather(
                *(self._generate_one_speech(npc_id, decision, prompt) for npc_id, decision, prompt in prompts)
            )

            async with run.lock:
                run.in_flight_speech_calls = max(
                    0,
                    run.in_flight_speech_calls - len(prompts),
                )
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                state = self._round_state_locked(run, conversation)
                publications: list[dict[str, Any]] = []
                for sequence, (npc_id, decision, text) in enumerate(speech_results, start=1):
                    cleaned = text.strip() if isinstance(text, str) else ""
                    if (
                        not cleaned
                        or npc_id not in conversation.participants
                        or self._is_near_duplicate_locked(run, conversation, npc_id, cleaned)
                    ):
                        continue
                    publications.append(
                        {
                            "actorId": npc_id,
                            "text": cleaned,
                            "decision": decision.model_dump(by_alias=True),
                            "roundSequence": sequence,
                            "replyToMessageIds": trigger_message_ids,
                        }
                    )
                state["pendingPublications"] = publications
                state["status"] = "publishing"
                if not publications:
                    before_seq = run.event_seq
                    await self._finish_no_speech_round_locked(
                        run,
                        conversation,
                        state,
                        final_check=final_check,
                        leaving_ids=list(state.get("pendingLeaverIds", [])),
                    )
                    events = [event.to_dict() for event in run.events_after(before_seq)]
                else:
                    events = []
                await self.repository.save(run)
                for event in events:
                    await self.event_hub.publish(run.run_id, event)

            if publications:
                await self._publish_pending_round(run, conversation_id)
        finally:
            async with run.lock:
                run.active_chat_pipelines = max(0, run.active_chat_pipelines - 1)
                await self._finish_pending_boundary_locked(run)
                await self.repository.save(run)

    async def _generate_one_speech(
        self,
        npc_id: str,
        decision: ChatDecision,
        prompt: str,
    ) -> tuple[str, ChatDecision, str]:
        try:
            speech = await asyncio.wait_for(
                self._npc_agent(npc_id).generate_speech(prompt),
                timeout=self.chat_model_call_timeout_seconds,
            )
            return npc_id, decision, speech.text
        except Exception:
            return npc_id, decision, ""

    def _order_speakers(
        self,
        run: Run,
        conversation: Conversation,
        candidates: list[tuple[str, ChatDecision]],
        trigger_message_ids: list[str],
    ) -> list[tuple[str, ChatDecision]]:
        trigger_text = "\n".join(
            str(message.get("text", ""))
            for message in run.messages.get(conversation.conversation_id, [])
            if message.get("messageId") in set(trigger_message_ids)
        )
        join_index = {
            actor_id: index for index, actor_id in enumerate(conversation.participants)
        }

        def key(item: tuple[str, ChatDecision]) -> tuple[int, int, int, str]:
            actor_id, decision = item
            actor = self.registry.actor(actor_id)
            directly_addressed = int(
                actor_id in trigger_text
                or (actor is not None and bool(actor.name) and actor.name in trigger_text)
            )
            return (
                -directly_addressed,
                -int(decision.response_desire),
                join_index.get(actor_id, 999),
                actor_id,
            )

        return sorted(candidates, key=key)

    def _is_near_duplicate_locked(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
        text: str,
    ) -> bool:
        normalized = "".join(text.split()).lower()
        if not normalized:
            return True
        recent = [
            "".join(str(message.get("text", "")).split()).lower()
            for message in run.messages.get(conversation.conversation_id, [])
            if message.get("authorActorId") == npc_id
        ][-3:]
        return any(
            previous == normalized
            or difflib.SequenceMatcher(None, previous, normalized).ratio() >= 0.94
            for previous in recent
            if previous
        )

    def _publish_delay_seconds(self, run: Run, round_id: int, sequence: int, text: str) -> float:
        low = self.chat_publish_delay_min_seconds
        high = self.chat_publish_delay_max_seconds
        if high <= low:
            return low
        digest = hashlib.sha256(
            f"{run.seed}:{round_id}:{sequence}:{len(text)}".encode()
        ).digest()
        fraction = int.from_bytes(digest[:4], "big") / (2**32 - 1)
        length_bias = min(len(text), 120) / 120
        mixed = min(1.0, fraction * 0.55 + length_bias * 0.45)
        return low + (high - low) * mixed

    async def _publish_pending_round(self, run: Run, conversation_id: str) -> None:
        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None or not conversation.is_open:
                return
            state = self._round_state_locked(run, conversation)
            round_id_number = int(state.get("roundId", 0))
            round_version = int(state.get("roundVersion", 0))
            participant_version = int(state.get("participantVersion", 0))
            segment_id = state.get("segmentId")
            published_ids = list(
                state.setdefault("recovery", {}).get("publishedMessageIds", [])
            )
        while True:
            async with run.lock:
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id_number,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                state = self._round_state_locked(run, conversation)
                pending = list(state.get("pendingPublications", []))
                if not pending:
                    break
                item = deepcopy(pending[0])
                round_id = f"{conversation_id}:round:{round_id_number}"
                sequence = int(item.get("roundSequence", 1))
                delay = 0.0 if not published_ids else self._publish_delay_seconds(
                    run,
                    round_id_number,
                    sequence,
                    str(item.get("text", "")),
                )
            if delay > 0:
                await asyncio.sleep(delay)
            async with run.lock:
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id_number,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                state = self._round_state_locked(run, conversation)
                pending = list(state.get("pendingPublications", []))
                if not pending or pending[0] != item:
                    continue
                actor_id = str(item["actorId"])
                if actor_id not in conversation.participants:
                    state["pendingPublications"] = pending[1:]
                    await self.repository.save(run)
                    continue
                before_seq = run.event_seq
                message = self._write_message_locked(
                    run,
                    conversation,
                    actor_id,
                    str(item["text"]),
                    round_id=round_id,
                    round_sequence=int(item.get("roundSequence", 1)),
                    reply_to_message_ids=list(item.get("replyToMessageIds", [])),
                )
                decision = ChatDecision.model_validate(item["decision"])
                self._apply_spoken_chapter_effects(
                    run,
                    conversation,
                    actor_id,
                    decision,
                    message["messageId"],
                )
                published_ids.append(message["messageId"])
                recovery = state.setdefault("recovery", {})
                recovery["publishedMessageIds"] = list(published_ids)
                state["pendingPublications"] = pending[1:]
                events = [event.to_dict() for event in run.events_after(before_seq)]
                await self.repository.save(run)
            for event in events:
                await self.event_hub.publish(run.run_id, event)

        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None or not self._round_still_current_locked(
                run,
                conversation,
                round_id=round_id_number,
                round_version=round_version,
                segment_id=segment_id,
                participant_version=participant_version,
            ):
                return
            before_seq = run.event_seq
            state = self._round_state_locked(run, conversation)
            direct_leavers = list(state.pop("pendingLeaverIds", []))
            published_authors = {
                str(item.get("authorActorId"))
                for item in run.messages.get(conversation_id, [])
                if item.get("messageId") in published_ids
            }
            post_speech_leavers = [
                str(npc_id)
                for npc_id in state.pop("pendingPostSpeechLeaverIds", [])
                if str(npc_id) in published_authors
            ]
            queued = list(state.get("queuedMessageIds", []))
            trigger = self._ordered_message_ids_locked(
                run,
                conversation_id,
                [*published_ids, *queued],
            )
            state["queuedMessageIds"] = []
            state["pendingPublications"] = []
            state["finalCheckUsed"] = False
            state["recovery"] = {
                "resumeStatus": None,
                "attempt": 0,
                "publishedMessageIds": [],
            }
            trigger_authors = {
                message.get("authorActorId")
                for message in run.messages.get(conversation_id, [])
                if message.get("messageId") in set(trigger)
            }
            if self.registry.player_actor_id in trigger_authors:
                state["npcOnlyRounds"] = 0
            else:
                state["npcOnlyRounds"] = int(state.get("npcOnlyRounds", 0)) + 1
            for npc_id in list(dict.fromkeys(direct_leavers)):
                if npc_id in conversation.participants:
                    await self._leave_and_consolidate_locked(
                        run,
                        conversation,
                        npc_id,
                        "model_leave",
                    )
            for npc_id in list(dict.fromkeys(post_speech_leavers)):
                if npc_id in conversation.participants:
                    await self._leave_and_consolidate_locked(
                        run,
                        conversation,
                        npc_id,
                        "said_and_left",
                    )
            if not conversation.is_open:
                events = [event.to_dict() for event in run.events_after(before_seq)]
                await self.repository.save(run)
                for event in events:
                    await self.event_hub.publish(run.run_id, event)
                return
            if (
                state.get("npcOnlyRounds", 0) >= self.chat_npc_only_safety_rounds
                and self.registry.player_actor_id not in trigger_authors
            ):
                self._enter_cooldown_locked(run, conversation, state)
            elif trigger and run.clock.current.clock_minutes < run.clock.active_end_minutes:
                state["roundVersion"] = int(state.get("roundVersion", 0)) + 1
                state["triggerMessageIds"] = trigger
                state["status"] = "queued"
            else:
                state["status"] = "idle"
            events = [event.to_dict() for event in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in events:
            await self.event_hub.publish(run.run_id, event)

    @staticmethod
    def _ordered_message_ids_locked(
        run: Run,
        conversation_id: str,
        message_ids: list[str],
    ) -> list[str]:
        wanted = set(message_ids)
        return [
            str(message["messageId"])
            for message in run.messages.get(conversation_id, [])
            if message.get("messageId") in wanted
        ]

    def _enter_cooldown_locked(
        self,
        run: Run,
        conversation: Conversation,
        state: dict[str, Any],
    ) -> None:
        state["status"] = "cooldown"
        state["triggerMessageIds"] = []
        state["cooldownDueAt"] = time.time() + self.chat_cooldown_seconds
        state["finalCheckUsed"] = False
        run.idle_counts[conversation.conversation_id] = 1
        run.append_event(
            "conversation_idle",
            {"conversationId": conversation.conversation_id, "idleCount": 1},
        )

    async def _finish_no_speech_round_locked(
        self,
        run: Run,
        conversation: Conversation,
        state: dict[str, Any],
        *,
        final_check: bool,
        leaving_ids: list[str],
    ) -> None:
        for npc_id in list(dict.fromkeys(leaving_ids)):
            if npc_id in conversation.participants:
                await self._leave_and_consolidate_locked(
                    run,
                    conversation,
                    npc_id,
                    "model_leave",
                )
        if not conversation.is_open:
            return
        if final_check:
            for npc_id in list(conversation.participants):
                actor = self.registry.actor(npc_id)
                if actor is not None and actor.kind == "npc":
                    await self._leave_and_consolidate_locked(
                        run,
                        conversation,
                        npc_id,
                        "conversation_idle",
                    )
            if conversation.is_open:
                state["status"] = "idle"
            return
        self._enter_cooldown_locked(run, conversation, state)

    async def _execute_opener_round(self, run: Run, conversation_id: str) -> None:
        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None or not conversation.is_open:
                return
            state = self._round_state_locked(run, conversation)
            npc_id = str(state.get("openerActorId") or "")
            if npc_id not in conversation.participants:
                state["status"] = "idle"
                return
            state["roundId"] = int(state.get("roundId", 0)) + 1
            state["recovery"] = {
                "resumeStatus": None,
                "attempt": 0,
                "publishedMessageIds": [],
            }
            round_id = int(state["roundId"])
            round_version = int(state.get("roundVersion", 0))
            segment_id = state.get("segmentId")
            participant_version = int(state.get("participantVersion", 0))
            opener_kind = str(state.get("openerKind") or "conversation_opener")
            state["status"] = "deciding"
            invocations = self._prepare_round_invocations_locked(
                run,
                conversation,
                state,
                trigger=opener_kind,
                only_actor_id=npc_id,
            )
            run.active_chat_pipelines += 1
            await self.repository.save(run)
        try:
            decision = ChatDecision(
                result="decided",
                action="speak",
                responseDesire=3,
                intent=(
                    "自然地说出开场白并说明想聊的话题"
                    if opener_kind == "conversation_opener"
                    else "自然地向当前参与者打招呼并简短加入眼前话题"
                ),
            )
            recalled_ids: tuple[str, ...] = ()
            if invocations:
                _, result = await self._invoke_chat_decision(*invocations[0])
                if result is not None:
                    candidate = cast(ChatDecision, result.decision)
                    recalled_ids = tuple(result.recalled_memory_ids)
                    if candidate.result == "decided" and candidate.action == "speak":
                        decision = candidate
            async with run.lock:
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                if run.clock.current.clock_minutes >= run.clock.active_end_minutes:
                    state = self._round_state_locked(run, conversation)
                    state["status"] = "idle"
                    return
                for memory_id in recalled_ids:
                    if run.memories.get(memory_id, {}).get("ownerNpcId") == npc_id:
                        run.memory_cache.setdefault((conversation_id, npc_id), set()).add(memory_id)
                self._apply_chat_drafts(run, conversation, npc_id, decision)
                prompt = self._npc_prompt(
                    run,
                    npc_id,
                    "opening_speech" if opener_kind == "conversation_opener" else "join_speech",
                    {
                        "conversationId": conversation_id,
                        "participants": list(conversation.participants),
                        "intent": decision.intent or state.get("openerIntent"),
                        "invitingGoalId": state.get("openerGoalId"),
                        **self._chat_context(run, conversation, npc_id),
                    },
                )
                state["status"] = "generating"
                run.in_flight_speech_calls += 1
                await self.repository.save(run)
            _, decision, text = await self._generate_one_speech(npc_id, decision, prompt)
            async with run.lock:
                run.in_flight_speech_calls = max(0, run.in_flight_speech_calls - 1)
                conversation = run.conversations.get(conversation_id)
                if conversation is None or not self._round_still_current_locked(
                    run,
                    conversation,
                    round_id=round_id,
                    round_version=round_version,
                    segment_id=segment_id,
                    participant_version=participant_version,
                ):
                    return
                state = self._round_state_locked(run, conversation)
                if not text.strip():
                    before_seq = run.event_seq
                    run.append_event(
                        "conversation_activity",
                        {"conversationId": conversation_id, "reason": "speech_unavailable"},
                    )
                    self._enter_cooldown_locked(run, conversation, state)
                    events = [event.to_dict() for event in run.events_after(before_seq)]
                else:
                    events = []
                    state["pendingPublications"] = [
                        {
                            "actorId": npc_id,
                            "text": text.strip(),
                            "decision": decision.model_dump(by_alias=True),
                            "roundSequence": 1,
                            "replyToMessageIds": [],
                        }
                    ]
                    state["status"] = "publishing"
                await self.repository.save(run)
                for event in events:
                    await self.event_hub.publish(run.run_id, event)
            if text.strip():
                await self._publish_pending_round(run, conversation_id)
        finally:
            async with run.lock:
                run.active_chat_pipelines = max(0, run.active_chat_pipelines - 1)
                await self._finish_pending_boundary_locked(run)
                await self.repository.save(run)

    async def _finish_pending_boundary_locked(self, run: Run) -> None:
        if run.active_chat_pipelines:
            return
        chapter_event_id = run.pending_chapter_event_id
        pending = run.pending_day_end
        if chapter_event_id is not None:
            run.pending_chapter_event_id = None
            run.pending_day_end = None
            chapter_event = next(
                event for event in self.registry.events if event.event_id == chapter_event_id
            )
            await self._finish_chapter_locked(run, chapter_event)
        elif pending is not None:
            run.pending_day_end = None
            await self._close_day_locked(run, pending[0], pending[1])

    def _visible_messages(self, run: Run, conversation: Conversation, npc_id: str) -> list[dict[str, Any]]:
        return [deepcopy(message) for message in run.messages.get(conversation.conversation_id, []) if npc_id in message.get("visibleToNpcIds", [])]

    def _boundary_carryover_messages(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
    ) -> list[dict[str, Any]]:
        """Return the previous Segment tail only to continuing participants."""

        segments = run.segments.get(conversation.conversation_id, [])
        if len(segments) < 2:
            return []
        previous_segment, current_segment = segments[-2:]
        if (
            npc_id not in previous_segment.get("participants", [])
            or npc_id not in current_segment.get("participants", [])
        ):
            return []
        visible_ids = {
            message["messageId"]
            for message in self._visible_messages(run, conversation, npc_id)
            if message.get("segmentId") == previous_segment.get("segmentId")
        }
        visible_previous_messages = [
            message
            for message in self._segment_messages(
                run,
                conversation,
                previous_segment,
            )
            if message.get("messageId") in visible_ids
        ]
        return visible_previous_messages[
            -self.segment_boundary_carryover_messages :
        ]

    def _chat_context(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
    ) -> dict[str, Any]:
        segments = run.segments.get(conversation.conversation_id, [])
        current_segment = segments[-1] if segments else None
        current_segment_id = current_segment.get("segmentId") if current_segment else None
        visible_messages = [
            message
            for message in self._visible_messages(run, conversation, npc_id)
            if message.get("segmentId") == current_segment_id
        ]
        if current_segment is not None:
            messages = self._messages_after_summary_cursor(
                visible_messages,
                current_segment.get("summaryThroughMessageId"),
            )
        else:
            messages = []
        summaries = self._segment_summary_entries(run, conversation, npc_id)
        # Before the first successful compression preserve the existing
        # small-chat behaviour.  After compression the cursor, rather than a
        # hard slice, decides which raw tail is still needed; this ensures a
        # failed summary never hides source evidence from the Agent.
        return {
            "segmentSummaries": summaries,
            "boundaryMessages": self._boundary_carryover_messages(
                run,
                conversation,
                npc_id,
            ),
            "messages": messages,
        }

    def _consolidation_prompt_messages(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
    ) -> list[dict[str, Any]]:
        """Project summaries plus uncompressed evidence for ExitConsolidation."""

        result: list[dict[str, Any]] = []
        for segment in run.segments.get(conversation.conversation_id, []):
            if npc_id not in segment.get("participants", []):
                continue
            segment_messages = self._segment_messages(run, conversation, segment)
            visible_ids = {
                message["messageId"]
                for message in self._visible_messages(run, conversation, npc_id)
                if message.get("segmentId") == segment.get("segmentId")
            }
            if segment.get("summaryThroughMessageId"):
                # ExitConsolidation still needs a bounded evidence tail for
                # validating Memory/Goal/Effect references.  The shared
                # The summary carries the older context; the configured raw
                # tail retains concrete evidence without re-expanding the
                # whole segment prompt.
                projected = segment_messages[-self.segment_summary_recent_messages :]
            else:
                projected = segment_messages
            result.extend(
                message
                for message in projected
                if message.get("messageId") in visible_ids
            )
        return result

    @staticmethod
    def _public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: deepcopy(value) for key, value in message.items() if key != "visibleToNpcIds"}
            for message in messages
        ]

    @staticmethod
    def _public_invitation(invitation: dict[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in invitation.items()
            if not key.startswith("_")
        }

    def _public_join_request(self, join_request: dict[str, Any]) -> dict[str, Any]:
        player_id = self.registry.player_actor_id
        result: dict[str, Any] = {
            "joinRequestId": join_request["joinRequestId"],
            "conversationId": join_request["conversationId"],
            "applicantActorId": join_request["applicantActorId"],
            "status": join_request["status"],
            "requestedAt": join_request["requestedAt"],
            "approverActorIds": list(join_request["approverActorIds"]),
            "pendingPlayerDecision": (
                join_request["status"] == "pending"
                and join_request["approverDecisions"].get(player_id) == "pending"
            ),
        }
        if "resolvedAt" in join_request:
            result["resolvedAt"] = join_request["resolvedAt"]
        if "expiredAt" in join_request:
            result["expiredAt"] = join_request["expiredAt"]
        return result

    def _resolve_topic_hints(self, hints: list[str]) -> list[str]:
        resolved: list[str] = []
        for topic in self.registry.topics.values():
            if (
                topic.topic_id in hints
                or topic.name in hints
                or any(alias in hints for alias in topic.aliases)
            ):
                resolved.append(topic.topic_id)
        return resolved

    def _initial_memory_ids(
        self,
        run: Run,
        owner_npc_id: str,
        participant_ids: list[str],
    ) -> list[str]:
        active_topic_ids = {
            topic_id
            for goal in run.goals.values()
            if goal["ownerNpcId"] == owner_npc_id
            and goal["status"] in {"active", "blocked"}
            for topic_id in goal.get("topicIds", [])
        }
        candidates: list[tuple[int, int, str]] = []
        for memory_id, memory in run.memories.items():
            if memory.get("ownerNpcId") != owner_npc_id:
                continue
            actor_hits = len(set(participant_ids) & set(memory.get("actorIds", [])))
            topic_hits = len(active_topic_ids & set(memory.get("topicIds", [])))
            if not actor_hits and not topic_hits:
                continue
            candidates.append(
                (
                    actor_hits * 2 + topic_hits,
                    int(memory.get("importance", 1)),
                    memory_id,
                )
            )
        candidates.sort(reverse=True)
        ordered = list(run.fresh_event_context.get(owner_npc_id, []))
        ordered.extend(
            memory_id
            for _, _, memory_id in candidates
            if memory_id not in ordered
        )
        return ordered[:INITIAL_MEMORY_CACHE_LIMIT]

    @staticmethod
    def _prior_conversation_counts(run: Run, npc_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for conversation in run.conversations.values():
            participants = conversation.participant_history()
            if npc_id not in participants:
                continue
            for actor_id in participants:
                if actor_id != npc_id:
                    counts[actor_id] = counts.get(actor_id, 0) + 1
        return counts

    def _apply_chat_drafts(self, run: Run, conversation: Conversation, npc_id: str, decision: ChatDecision) -> None:
        draft = run.conversation_drafts.setdefault(conversation.conversation_id, {}).setdefault(
            npc_id,
            {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            },
        )
        visible_ids = {message["messageId"] for message in self._visible_messages(run, conversation, npc_id)}
        own_visible_ids = {
            message["messageId"]
            for message in self._visible_messages(run, conversation, npc_id)
            if message["authorActorId"] == npc_id
        }
        for update in decision.goal_updates:
            goal = run.goals.get(update.goal_id)
            if goal is None or goal["ownerNpcId"] != npc_id or not update.evidence_message_ids or not set(update.evidence_message_ids).issubset(visible_ids):
                continue
            existing_update = draft["goalUpdates"].get(update.goal_id)
            effective_status = (
                existing_update["newStatus"] if existing_update is not None else goal["status"]
            )
            if (
                effective_status in {"achieved", "abandoned"}
                or update.new_status == effective_status
            ):
                continue
            draft["goalUpdates"][update.goal_id] = {"newStatus": update.new_status, "reason": update.reason, "evidenceMessageIds": list(update.evidence_message_ids)}
        changed_dimensions: set[tuple[str, str]] = set()
        for relationship_update in decision.relationship_updates:
            if relationship_update.target_actor_id == npc_id or relationship_update.target_actor_id not in conversation.participants or not relationship_update.evidence_message_ids or not set(relationship_update.evidence_message_ids).issubset(visible_ids):
                continue
            key = (relationship_update.target_actor_id, relationship_update.dimension)
            if key in changed_dimensions:
                continue
            changed_dimensions.add(key)
            draft["relationshipUpdates"].append(relationship_update.model_dump(by_alias=True))
        if decision.pending_goal is not None and decision.pending_goal.evidence_message_ids and set(decision.pending_goal.evidence_message_ids).issubset(visible_ids):
            draft["pendingGoals"].append(decision.pending_goal.model_dump(by_alias=True))
        for effect in decision.chapter_effects:
            evidence = set(effect.evidence_message_ids)
            if evidence and evidence.issubset(own_visible_ids):
                draft["chapterEffects"].append(effect.model_dump(by_alias=True))

    def _apply_spoken_chapter_effects(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
        decision: ChatDecision,
        message_id: str,
    ) -> None:
        draft = run.conversation_drafts.get(conversation.conversation_id, {}).get(npc_id)
        if draft is None:
            return
        for effect in decision.chapter_effects:
            if effect.evidence_message_ids:
                continue
            data = effect.model_dump(by_alias=True)
            data["evidenceMessageIds"] = [message_id]
            draft["chapterEffects"].append(data)

    async def _leave_and_consolidate_locked(self, run: Run, conversation: Conversation, npc_id: str, reason: str) -> None:
        if npc_id not in conversation.participants:
            return
        await self._close_current_segment_locked(run, conversation)
        conversation.remove_participant(npc_id)
        self._expire_join_requests_for_conversation_locked(
            run,
            conversation.conversation_id,
            reason="conversation_participants_changed",
        )
        run.actor_states[npc_id]["status"] = "waiting"
        run.append_event("conversation_participant_left" if conversation.is_open else "conversation_closed", {"conversation": conversation.to_public_dict(), "actorLeft": npc_id})
        if conversation.is_open:
            run.segments[conversation.conversation_id].append(
                {
                    "segmentId": run.next_segment_identity(),
                    "participants": list(conversation.participants),
                    "startedAt": run.clock.as_dict()["label"],
                    "summary": None,
                    "summaryThroughMessageId": None,
                }
            )
            run.idle_counts[conversation.conversation_id] = 0
        await self._consolidate_locked(run, conversation, npc_id, reason)
        if conversation.is_open and len(conversation.participants) >= 2:
            self._reset_round_for_participant_change_locked(
                run,
                conversation,
                status="idle",
            )
            return
        if not conversation.is_open:
            for remaining in list(conversation.participants):
                actor = self.registry.actor(remaining)
                if actor is not None and actor.kind == "npc":
                    await self._consolidate_locked(run, conversation, remaining, "conversation_closed")
                elif actor is not None and actor.kind == "player":
                    run.actor_states[remaining]["status"] = "present"
                    run.memory_cache.pop((conversation.conversation_id, remaining), None)

    async def _remove_player_locked(
        self,
        run: Run,
        conversation: Conversation,
        player_id: str,
    ) -> None:
        if player_id not in conversation.participants:
            raise ActorNotInConversationError(details={"actorId": player_id})
        segments = run.segments.get(conversation.conversation_id, [])
        segment = segments[-1] if segments else None
        if segment is not None and segment.get("endedAt") is None:
            segment["endedAt"] = run.clock.as_dict()["label"]
        closed_segment_id = str(segment.get("segmentId")) if segment else None
        conversation.remove_participant(player_id)
        self._expire_join_requests_for_conversation_locked(
            run,
            conversation.conversation_id,
            reason="conversation_participants_changed",
        )
        run.actor_states[player_id]["status"] = "present"
        run.memory_cache.pop((conversation.conversation_id, player_id), None)
        run.append_event(
            "conversation_participant_left" if conversation.is_open else "conversation_closed",
            {"conversation": conversation.to_public_dict(), "actorLeft": player_id},
        )
        if conversation.is_open:
            run.segments[conversation.conversation_id].append(
                {
                    "segmentId": run.next_segment_identity(),
                    "participants": list(conversation.participants),
                    "startedAt": run.clock.as_dict()["label"],
                    "summary": None,
                    "summaryThroughMessageId": None,
                }
            )
            run.idle_counts[conversation.conversation_id] = 0
            state = self._reset_round_for_participant_change_locked(
                run,
                conversation,
                status="idle",
            )
            if closed_segment_id is not None:
                state["pendingSummarySegmentIds"] = [closed_segment_id]
            self._schedule_player_leave_maintenance(
                run,
                conversation.conversation_id,
                closed_segment_id,
                [],
            )
            return
        remaining_npcs = [
            remaining
            for remaining in conversation.participants
            if (actor := self.registry.actor(remaining)) is not None
            and actor.kind == "npc"
        ]
        for npc_id in remaining_npcs:
            run.actor_states[npc_id]["status"] = "waiting"
            existing = run.consolidation_status.get(
                (conversation.conversation_id, npc_id),
                {},
            )
            run.consolidation_status[(conversation.conversation_id, npc_id)] = {
                **existing,
                "status": "pending",
                "reason": "conversation_closed",
                "createdAt": run.clock.as_dict()["label"],
                "attempts": int(existing.get("attempts", 0)),
                "draftsCommitted": bool(existing.get("draftsCommitted")),
                "interactionRecorded": bool(existing.get("interactionRecorded")),
            }
        state = run.ensure_conversation_round_state(conversation.conversation_id)
        state["status"] = "closed_maintenance"
        state["pendingSummarySegmentIds"] = (
            [closed_segment_id] if closed_segment_id is not None else []
        )
        self._wake_chat_worker(run.run_id, conversation.conversation_id)
        self._schedule_player_leave_maintenance(
            run,
            conversation.conversation_id,
            closed_segment_id,
            remaining_npcs,
        )

    def _schedule_player_leave_maintenance(
        self,
        run: Run,
        conversation_id: str,
        closed_segment_id: str | None,
        npc_ids: list[str],
    ) -> None:
        key = (run.run_id, conversation_id)
        if key in self._maintenance_keys:
            return
        self._maintenance_keys.add(key)
        task = asyncio.create_task(
            self._complete_player_leave_maintenance(
                run,
                conversation_id,
                closed_segment_id,
                npc_ids,
            ),
            name=f"chat-leave-maintenance:{run.run_id}:{conversation_id}",
        )
        self._maintenance_tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._maintenance_tasks.discard(done)
            self._maintenance_keys.discard(key)
            if not done.cancelled():
                error = done.exception()
                if error is None:
                    self._resume_maintenance_tasks(run)

        task.add_done_callback(finished)

    async def _complete_player_leave_maintenance(
        self,
        run: Run,
        conversation_id: str,
        closed_segment_id: str | None,
        npc_ids: list[str],
    ) -> None:
        async with run.lock:
            conversation = run.conversations.get(conversation_id)
            if conversation is None:
                return
            before_seq = run.event_seq
            segment = next(
                (
                    item
                    for item in run.segments.get(conversation_id, [])
                    if item.get("segmentId") == closed_segment_id
                ),
                None,
            )
            if segment is not None and segment.get("summary") is None:
                segment["summary"] = await self._summarize_segment_locked(
                    run,
                    conversation,
                    segment,
                )
            state = run.conversation_round_states.get(conversation_id)
            if state is not None and closed_segment_id is not None:
                state["pendingSummarySegmentIds"] = [
                    segment_id
                    for segment_id in state.get("pendingSummarySegmentIds", [])
                    if segment_id != closed_segment_id
                ]
            for npc_id in npc_ids:
                await self._consolidate_locked(
                    run,
                    conversation,
                    npc_id,
                    "conversation_closed",
                )
            if (
                not conversation.is_open
                and not any(
                    status.get("status") == "pending"
                    for (candidate_id, _), status in run.consolidation_status.items()
                    if candidate_id == conversation_id
                )
                and not (
                    run.conversation_round_states.get(conversation_id, {}).get(
                        "pendingSummarySegmentIds"
                    )
                )
            ):
                run.conversation_round_states.pop(conversation_id, None)
            events = [event.to_dict() for event in run.events_after(before_seq)]
            await self.repository.save(run)
        for event in events:
            await self.event_hub.publish(run.run_id, event)

    async def _close_conversation_locked(self, run: Run, conversation: Conversation, reason: str) -> None:
        was_open = conversation.is_open
        self._expire_join_requests_for_conversation_locked(
            run,
            conversation.conversation_id,
            reason="conversation_closed",
        )
        if conversation.is_open:
            await self._close_current_segment_locked(run, conversation)
        for actor_id in list(conversation.participants):
            actor = self.registry.actor(actor_id)
            if actor is not None and actor.kind == "npc":
                run.actor_states[actor_id]["status"] = "waiting"
                await self._consolidate_locked(run, conversation, actor_id, reason)
            elif actor is not None and actor.kind == "player":
                run.actor_states[actor_id]["status"] = "present"
                run.memory_cache.pop((conversation.conversation_id, actor_id), None)
        if was_open:
            conversation.close(reason)
            run.append_event("conversation_closed", {"conversation": conversation.to_public_dict()})
        run.conversation_round_states.pop(conversation.conversation_id, None)
        run.conversation_round_locks.pop(conversation.conversation_id, None)
        self._wake_chat_worker(run.run_id, conversation.conversation_id)
        # Segment/conversation state is no longer needed by the live
        # scheduler.  Keep messages and consolidation status for history and
        # retry, but drop per-conversation caches and drafts that were
        # successfully committed.
        run.idle_counts.pop(conversation.conversation_id, None)
        for key in list(run.memory_cache):
            if key[0] == conversation.conversation_id:
                run.memory_cache.pop(key, None)
        npc_history = [
            actor_id
            for actor_id in conversation.participant_history()
            if (actor := self.registry.actor(actor_id)) is not None and actor.kind == "npc"
        ]
        if all(
            run.consolidation_status.get((conversation.conversation_id, actor_id), {}).get("status") == "succeeded"
            for actor_id in npc_history
        ):
            run.conversation_drafts.pop(conversation.conversation_id, None)

    async def _consolidate_locked(self, run: Run, conversation: Conversation, npc_id: str, reason: str) -> None:
        key = (conversation.conversation_id, npc_id)
        existing = run.consolidation_status.get(key)
        if existing and existing.get("status") == "succeeded":
            return
        messages = self._visible_messages(run, conversation, npc_id)
        prompt_messages = self._consolidation_prompt_messages(run, conversation, npc_id)
        draft = run.conversation_drafts.get(conversation.conversation_id, {}).get(
            npc_id,
            {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            },
        )
        prompt = self._npc_prompt(
            run,
            npc_id,
            "exit_consolidation",
            {
                "reason": reason,
                "messages": prompt_messages,
                "segmentSummaries": self._segment_summary_entries(
                    run,
                    conversation,
                    npc_id,
                ),
                "draft": draft,
            },
        )
        try:
            consolidation = await self._await_model_without_run_lock(
                run,
                self.decisions.exit_consolidation(prompt),
            )
        except StructuredCallFailed:
            consolidation = ExitConsolidation()
        current_status = run.consolidation_status.get(key)
        if current_status and current_status.get("status") == "succeeded":
            return
        failed = self.decisions.last_failed_protocol == "ExitConsolidation"
        drafts_committed = bool(existing and existing.get("draftsCommitted"))
        interactions_recorded = bool(existing and existing.get("interactionRecorded"))
        if not drafts_committed:
            for goal_id, update in draft.get("goalUpdates", {}).items():
                if goal_id in run.goals and run.goals[goal_id]["ownerNpcId"] == npc_id:
                    run.goals[goal_id]["status"] = update["newStatus"]
            for raw in draft.get("relationshipUpdates", []):
                self._apply_relationship_update(run, npc_id, raw)
            self._store_chapter_draft_effects(run, npc_id, draft, messages)
            drafts_committed = True
        if not failed:
            memory_refs = self._store_memories(run, npc_id, consolidation, messages)
            self._store_chapter_effects(run, npc_id, consolidation, messages)
            for pending in consolidation.new_short_goals:
                self._create_short_goal(
                    run,
                    npc_id,
                    pending.model_dump(by_alias=True),
                    memory_refs,
                )
        if not interactions_recorded:
            self._increment_interactions(run, conversation, npc_id)
            interactions_recorded = True
        status = "failed" if failed else "succeeded"
        run.consolidation_status[key] = {
            "status": status,
            "reason": reason,
            "createdAt": run.clock.as_dict()["label"],
            "attempts": int(existing.get("attempts", 0)) + 1 if existing else 1,
            "draftsCommitted": drafts_committed,
            "interactionRecorded": interactions_recorded,
        }
        run.memory_cache.pop((conversation.conversation_id, npc_id), None)
        if status == "succeeded":
            run.conversation_drafts.get(conversation.conversation_id, {}).pop(npc_id, None)
        has_unresolved_goal = any(
            goal["ownerNpcId"] == npc_id and goal["status"] in {"active", "blocked"}
            for goal in run.goals.values()
        )
        has_pending_goal = bool(draft.get("pendingGoals")) and status == "failed"
        if not has_unresolved_goal and not has_pending_goal:
            run.actor_states[npc_id]["status"] = "departed"
        elif run.actor_states[npc_id].get("status") != "departed":
            run.actor_states[npc_id]["status"] = "present"
        run.append_event("npc_consolidated", {"conversationId": conversation.conversation_id, "npcId": npc_id, "status": status})

    def _create_short_goal(
        self,
        run: Run,
        npc_id: str,
        data: dict[str, Any],
        memory_refs: dict[str, str],
    ) -> None:
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            return
        trigger_refs = data.get("triggerMemoryRefs", [])
        if not isinstance(trigger_refs, list) or not trigger_refs:
            return
        trigger_memory_ids = [memory_refs[ref] for ref in trigger_refs if ref in memory_refs]
        if len(trigger_memory_ids) != len(trigger_refs):
            return
        parent_goal_id = data.get("parentGoalId")
        if parent_goal_id is not None:
            parent = run.goals.get(parent_goal_id)
            if parent is None or parent["ownerNpcId"] != npc_id:
                return
        goal_id = f"goal_{npc_id}_short_{len(run.goals) + 1:03d}"
        run.goals[goal_id] = {
            "goalId": goal_id,
            "ownerNpcId": npc_id,
            "horizon": "short_term",
            "disclosure": "guarded",
            "description": description,
            "parentGoalId": parent_goal_id,
            "targetActorIds": [actor_id for actor_id in data.get("targetActorIds", []) if self.registry.actor(actor_id) is not None],
            "topicIds": self._resolve_topic_hints(list(data.get("topicHints", []))),
            "importance": int(data.get("importance", 2)),
            "status": "active",
            "createdAt": run.clock.as_dict()["label"],
        }
        for memory_id in trigger_memory_ids:
            run.memory_links.append(
                {
                    "memoryId": memory_id,
                    "kind": "RELATED_TO_GOAL",
                    "targetId": goal_id,
                    "role": "trigger",
                }
            )

    def _apply_relationship_update(self, run: Run, npc_id: str, raw: dict[str, Any] | RelationshipUpdate) -> None:
        if isinstance(raw, RelationshipUpdate):
            data = raw.model_dump(by_alias=True)
        else:
            data = raw
        target = data.get("targetActorId")
        dimension = data.get("dimension")
        direction = data.get("direction")
        if (
            not target
            or target == npc_id
            or self.registry.actor(str(target)) is None
            or dimension not in {"trust", "affinity", "tension"}
            or direction not in {"increase", "decrease"}
        ):
            return
        key = (npc_id, target)
        relationship = run.relationships.get(key)
        if relationship is None:
            relationship = {"fromActorId": npc_id, "toActorId": target, "familiarity": 0, "trust": 0, "affinity": 0, "tension": 0, "interactionCount": 0}
            run.relationships[key] = relationship
        delta = 1 if direction == "increase" else -1
        minimum, maximum = (0, 2) if dimension == "tension" else (-2, 2)
        relationship[dimension] = max(minimum, min(maximum, int(relationship.get(dimension, 0)) + delta))

    def _increment_interactions(self, run: Run, conversation: Conversation, npc_id: str) -> None:
        segments = run.segments.get(conversation.conversation_id, [])
        others = {
            actor_id
            for segment in segments
            if npc_id in segment.get("participants", [])
            for actor_id in segment.get("participants", [])
            if actor_id != npc_id
        }
        for target in others:
            key = (npc_id, target)
            relationship = run.relationships.get(key)
            if relationship is None:
                continue
            relationship["interactionCount"] = int(relationship.get("interactionCount", 0)) + 1
            count = relationship["interactionCount"]
            calculated = 3 if count >= 6 else 2 if count >= 3 else 1
            relationship["familiarity"] = max(
                int(relationship.get("familiarity", 0)),
                calculated,
            )

    def _store_memories(
        self,
        run: Run,
        npc_id: str,
        consolidation: ExitConsolidation,
        messages: list[dict[str, Any]],
    ) -> dict[str, str]:
        visible_ids = {message["messageId"] for message in messages}
        stored_refs: dict[str, str] = {}
        for item in consolidation.memories:
            evidence = list(dict.fromkeys(item.evidence_message_ids))
            if (
                item.ref in stored_refs
                or not evidence
                or not set(evidence).issubset(visible_ids)
            ):
                continue
            memory_id = run.next_memory_identity()
            topic_ids = self._resolve_topic_hints(list(item.topic_hints))
            run.memories[memory_id] = {
                "memoryId": memory_id,
                "ownerNpcId": npc_id,
                "type": item.type,
                "content": item.content,
                "actorIds": [actor_id for actor_id in item.actor_ids if self.registry.actor(actor_id) is not None],
                "topicIds": topic_ids,
                "importance": item.importance,
                "confidence": item.confidence,
                "source": "conversation",
                "conversationId": messages[0]["conversationId"] if messages else None,
                "createdAt": run.clock.as_dict()["label"],
                "evidenceMessageIds": evidence,
                "goalIds": [
                    goal_id
                    for goal_id in item.goal_ids
                    if goal_id in run.goals
                    and run.goals[goal_id]["ownerNpcId"] == npc_id
                ],
            }
            stored_refs[item.ref] = memory_id
            for actor_id in run.memories[memory_id]["actorIds"]:
                run.memory_links.append({"memoryId": memory_id, "kind": "ABOUT", "targetId": actor_id})
            for topic_id in run.memories[memory_id]["topicIds"]:
                run.memory_links.append({"memoryId": memory_id, "kind": "ABOUT", "targetId": topic_id})
            for goal_id in run.memories[memory_id]["goalIds"]:
                run.memory_links.append({"memoryId": memory_id, "kind": "RELATED_TO_GOAL", "targetId": goal_id})
        return stored_refs

    def _store_chapter_draft_effects(
        self,
        run: Run,
        npc_id: str,
        draft: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        own_message_ids = {
            message["messageId"]
            for message in messages
            if message["authorActorId"] == npc_id
        }
        for effect in draft.get("chapterEffects", []):
            evidence = effect.get("evidenceMessageIds", [])
            if not evidence or not set(evidence).issubset(own_message_ids):
                continue
            self._commit_chapter_effect(run, npc_id, effect)

    def _store_chapter_effects(self, run: Run, npc_id: str, consolidation: ExitConsolidation, messages: list[dict[str, Any]]) -> None:
        own_message_ids = {message["messageId"] for message in messages if message["authorActorId"] == npc_id}
        for effect in consolidation.chapter_effects:
            if (
                not effect.evidence_message_ids
                or not set(effect.evidence_message_ids).issubset(own_message_ids)
            ):
                continue
            self._commit_chapter_effect(
                run,
                npc_id,
                effect.model_dump(by_alias=True),
            )

    def _commit_chapter_effect(
        self,
        run: Run,
        npc_id: str,
        effect: dict[str, Any],
    ) -> None:
        kind = effect.get("kind")
        value = effect.get("value")
        agenda_id = effect.get("agendaId")
        if kind == "overall_stance" and value in {
            "unknown",
            "support",
            "conditional",
            "oppose",
            "withdrawn",
        }:
            run.chapter_actor_stances[npc_id] = value
        elif (
            kind == "zhou_authorization"
            and npc_id == "npc_005"
            and value in {"none", "approved", "conditional", "rejected"}
        ):
            run.zhou_authorization = value
        elif (
            kind == "agenda_stance"
            and agenda_id is not None
            and (agenda_id, npc_id) in run.chapter_agenda_stances
            and value in {"unknown", "support", "conditional", "oppose", "withdrawn"}
        ):
            run.chapter_agenda_stances[(agenda_id, npc_id)] = value

    async def _finish_chapter_locked(self, run: Run, event: Any) -> None:
        if run.run_finished:
            return
        if run.active_chat_pipelines:
            # The whole deadline transaction is deferred, not just closing
            # the Conversation.  Chapter state must be read only after the
            # already-authorized model call returns and consolidation commits.
            run.pending_day_end = (event.world_day, "chapter_deadline")
            run.pending_chapter_event_id = event.event_id
            return
        run.fired_event_ids.add(event.event_id)
        # Stop new invites and close all active chats before reading chapter
        # state.  The deadline is a day-end variant, but its reason remains
        # visible as ``chapter_deadline`` for consolidation and history.
        await self._close_day_locked(run, event.world_day, "chapter_deadline")
        run.world_events[event.event_id] = {"eventId": event.event_id, "worldDay": event.world_day, "at": event.at, "visibility": event.visibility, "sourceLabel": event.source_label, "summary": event.summary, "visibleActorIds": list(event.visible_actor_ids) if not isinstance(event.visible_actor_ids, str) else list(run.positions)}
        run.append_event(
            "world_event_occurred",
            {
                "event": {
                    key: deepcopy(value)
                    for key, value in run.world_events[event.event_id].items()
                    if key != "visibleActorIds"
                },
                "worldTime": run.clock.as_dict(),
            },
        )
        stances = run.chapter_actor_stances
        support = sum(1 for value in stances.values() if value == "support")
        positive = sum(1 for value in stances.values() if value in {"support", "conditional"})
        negative = sum(1 for value in stances.values() if value in {"oppose", "withdrawn"})
        if run.zhou_authorization == "approved" and support == len(self.registry.npcs):
            branch = "consensus_submitted"
        elif run.zhou_authorization in {"approved", "conditional"} and positive >= 3 and negative <= 1:
            branch = "compromise_submitted"
        else:
            branch = "no_submission"
        agenda_results: dict[str, str] = {}
        for agenda in self.registry.public_agendas:
            if branch == "no_submission":
                agenda_results[agenda.agenda_id] = "not_adopted"
                continue
            values = [run.chapter_agenda_stances.get((agenda.agenda_id, npc.actor_id), "unknown") for npc in self.registry.npcs]
            owner_value = run.chapter_agenda_stances.get((agenda.agenda_id, agenda.owner_npc_id), "unknown")
            zhou_value = run.chapter_agenda_stances.get((agenda.agenda_id, "npc_005"), "unknown")
            support_count = sum(1 for value in values if value == "support")
            positive_count = sum(1 for value in values if value in {"support", "conditional"})
            negative_count = sum(1 for value in values if value in {"oppose", "withdrawn"})
            if owner_value == "support" and zhou_value == "support" and support_count >= 4:
                agenda_results[agenda.agenda_id] = "core_adopted"
            elif owner_value in {"support", "conditional"} and zhou_value in {"support", "conditional"} and positive_count >= 2 and negative_count <= 1:
                agenda_results[agenda.agenda_id] = "partially_adopted"
            else:
                agenda_results[agenda.agenda_id] = "not_adopted"
        player_task = None
        if run.player_agenda_id is not None:
            result = agenda_results.get(run.player_agenda_id, "not_adopted")
            player_task = "completed" if result == "core_adopted" else "partial" if result == "partially_adopted" else "failed"
        player_messages = [
            {
                "messageId": message["messageId"],
                "conversationId": message["conversationId"],
                "text": message["text"],
                "createdAt": message.get("createdAt"),
            }
            for conversation_messages in run.messages.values()
            for message in conversation_messages
            if message.get("authorActorId") == self.registry.player_actor_id
        ]
        player_messages.sort(key=lambda message: message["messageId"])
        player_messages = player_messages[-5:]
        run.chapter_resolution = {
            "chapterId": self.registry.chapter_id,
            "branch": branch,
            "agendaResults": agenda_results,
            "playerTaskResult": player_task,
            "actorStances": deepcopy(run.chapter_actor_stances),
            "playerHighlights": player_messages,
        }
        run.run_finished = True
        run.clock.status = "chapter_ended"
        run.append_event("chapter_resolved", deepcopy(run.chapter_resolution))

    def _conversation(self, run: Run, conversation_id: str) -> Conversation:
        conversation = run.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(details={"conversationId": conversation_id})
        return conversation

    def _player_can_read_conversation(self, run: Run, conversation: Conversation) -> bool:
        return self.registry.player_actor_id in conversation.participant_history()

    def _stable_actor_rank(self, run: Run, actor_id: str) -> int:
        digest = hashlib.sha256(f"{run.seed}:{actor_id}".encode()).digest()
        return int.from_bytes(digest[:4], "big")

    @staticmethod
    def _candidate_state(run: Run, actor_id: str) -> dict[str, Any]:
        conversation = run.actor_open_conversation(actor_id)
        return {
            "status": run.actor_states.get(actor_id, {}).get("status", "present"),
            "position": deepcopy(run.positions.get(actor_id, {"x": 0, "y": 0})),
            "conversationId": conversation.conversation_id if conversation is not None else None,
        }

    def _npc_prompt(self, run: Run, npc_id: str, protocol: str, extra: dict[str, Any]) -> str:
        persona = self.registry.npc_personas.get(npc_id)
        actor = self.registry.actor(npc_id)
        conversation_id = extra.get("conversationId")
        draft = (
            run.conversation_drafts.get(str(conversation_id), {}).get(npc_id, {})
            if conversation_id is not None
            else {}
        )
        goals = [
            deepcopy(goal)
            for goal in run.goals.values()
            if goal["ownerNpcId"] == npc_id
        ]
        for goal in goals:
            update = draft.get("goalUpdates", {}).get(goal["goalId"])
            if update is not None:
                goal["status"] = update["newStatus"]
                goal["sessionChangeReason"] = update.get("reason", "")
        if protocol == "daily_action":
            goals = [goal for goal in goals if goal["status"] in {"active", "blocked"}]
        relationships = [
            deepcopy(value)
            for key, value in run.relationships.items()
            if key[0] == npc_id
        ]
        relationship_by_target = {
            relationship["toActorId"]: relationship for relationship in relationships
        }
        for update in draft.get("relationshipUpdates", []):
            target_id = update.get("targetActorId")
            dimension = update.get("dimension")
            relationship = relationship_by_target.get(target_id)
            if relationship is None or dimension not in {"trust", "affinity", "tension"}:
                continue
            delta = 1 if update.get("direction") == "increase" else -1
            minimum, maximum = (0, 2) if dimension == "tension" else (-2, 2)
            relationship[dimension] = max(
                minimum,
                min(maximum, int(relationship.get(dimension, 0)) + delta),
            )
            relationship.setdefault("sessionChangeReasons", []).append(update.get("reason", ""))
        memories = [value for value in run.memories.values() if value.get("ownerNpcId") == npc_id and value.get("memoryId") in set(extra.get("memoryCache", []))]
        chapter_context: dict[str, Any] | None = None
        if protocol in {
            "chat_decision",
            "chat_decision_with_memory",
            "exit_consolidation",
        }:
            chapter_context = {
                "agendas": [
                    {
                        "agendaId": agenda.agenda_id,
                        "ownerNpcId": agenda.owner_npc_id,
                        "title": agenda.title,
                        "publicSummary": agenda.public_summary,
                    }
                    for agenda in self.registry.public_agendas
                ],
                "selectedPlayerAgendaId": run.player_agenda_id,
                "ownOverallStance": run.chapter_actor_stances.get(
                    npc_id, "unknown"
                ),
                "ownAgendaStances": {
                    agenda.agenda_id: run.chapter_agenda_stances.get(
                        (agenda.agenda_id, npc_id), "unknown"
                    )
                    for agenda in self.registry.public_agendas
                },
                "canSetZhouAuthorization": npc_id == "npc_005",
                "ownZhouAuthorization": (
                    run.zhou_authorization if npc_id == "npc_005" else None
                ),
            }
        payload: dict[str, Any] = {
            "protocol": protocol,
            "worldTime": run.clock.as_dict(),
            "timePolicy": run.clock.time_policy(),
            "actor": {"actorId": npc_id, "name": actor.name if actor else npc_id},
            "actorState": {
                "status": run.actor_states.get(npc_id, {}).get("status", "present"),
                "position": deepcopy(run.positions.get(npc_id, {"x": 0, "y": 0})),
            },
            "persona": {
                "summary": persona.persona_summary if persona else "",
                "traits": list(persona.traits) if persona else [],
                "values": list(persona.values) if persona else [],
                "socialStyle": {"initiative": persona.initiative, "directness": persona.directness, "openness": persona.openness, "conflictStyle": persona.conflict_style} if persona else {},
                "speechStyle": {"tone": persona.speech_tone, "length": persona.speech_length, "habits": list(persona.speech_habits)} if persona else {},
                "boundaries": list(persona.boundaries) if persona else [],
                "coreSecrets": list(persona.core_secrets) if persona else [],
            },
            "goals": goals,
            "relationships": relationships,
            "memories": memories,
            "freshEvents": [run.memories[item] for item in run.fresh_event_context.get(npc_id, []) if item in run.memories],
            "publicWorldState": {
                **deepcopy(run.current_world_state),
                **deepcopy(run.actor_world_state.get(npc_id, {})),
            },
            "sessionDraft": {
                "chapterEffects": deepcopy(draft.get("chapterEffects", [])),
                "pendingGoals": deepcopy(draft.get("pendingGoals", [])),
            },
            **({"chapterContext": chapter_context} if chapter_context is not None else {}),
            "context": extra,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _require_actor(self, actor_id: str) -> None:
        if self.registry.actor(actor_id) is None:
            raise ActorNotFoundError(details={"actorId": actor_id})

    @staticmethod
    def _require_active_run(run: Run) -> None:
        if run.run_finished:
            raise ChapterAlreadyEndedError()

    @staticmethod
    def _fingerprint(operation: str, payload: dict[str, Any]) -> str:
        serialized = json.dumps({"operation": operation, **payload}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _existing_command(run: Run, command_id: str | None, fingerprint: str) -> dict[str, Any] | None:
        if command_id is None:
            return None
        previous = run.command_records.get(command_id)
        if previous is None:
            return None
        if previous.fingerprint != fingerprint:
            raise DuplicateCommandError(details={"commandId": command_id})
        return previous.result

    @staticmethod
    def _record_command(run: Run, command_id: str | None, fingerprint: str, result: dict[str, Any]) -> None:
        if command_id is not None:
            run.command_records[command_id] = CommandRecord(
                fingerprint=fingerprint,
                result=deepcopy(result),
            )


__all__ = ["RunService"]
