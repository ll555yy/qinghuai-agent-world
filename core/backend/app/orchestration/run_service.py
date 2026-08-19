"""Application service for in-memory Runs and public events."""

from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from copy import deepcopy
from typing import Any, cast

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
    InvalidMessageError,
    InvitationNotFoundError,
    PlayerAccessDeniedError,
    RunNotFoundError,
    WorldStepError,
)
from ..domain.run import CommandRecord, Run
from ..persistence.in_memory import InMemoryRunRepository
from ..scenario.models import ScenarioRegistry
from .event_hub import EventHub


class RunService:
    """Coordinate repository, domain rules, per-Run locks, and event fan-out."""

    def __init__(
        self,
        registry: ScenarioRegistry,
        repository: InMemoryRunRepository | None = None,
        event_hub: EventHub | None = None,
        text_model: Any | None = None,
        seed: int = 1,
    ) -> None:
        self.registry = registry
        self.repository = repository or InMemoryRunRepository()
        self.event_hub = event_hub or EventHub()
        self.text_model = text_model
        self.decisions = DecisionService(text_model)
        # Five logical NPC Agents share this DecisionService and one compiled
        # LangGraph.  They receive only invocation snapshots; RunService
        # remains the authority that applies their semantic decisions.
        self.agent_runtime = NPCAgentRuntime(self.decisions)
        self.agents = NPCAgentRegistry(
            self.agent_runtime,
            [npc.actor_id for npc in self.registry.npcs],
        )
        self.seed = seed

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
        run.daily_think_minutes = self._daily_schedule(run_seed)
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
            # Day1's public notice is authoritative and is always visible
            # before the first scheduled NPC thought.
            initial_events = [event]
            await self._process_due_locked(run)
            initial_events.extend(run.events[len(initial_events):])
            snapshot = run.to_public_snapshot(self.registry)
        await self.repository.add(run)
        for initial_event in initial_events:
            await self.event_hub.publish(run_id, initial_event.to_dict())
        return snapshot

    async def get_run_entity(self, run_id: str) -> Run:
        run = await self.repository.get(run_id)
        if run is None:
            raise RunNotFoundError(details={"runId": run_id})
        return run

    async def get_run(self, run_id: str) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        async with run.lock:
            return deepcopy(run.to_public_snapshot(self.registry))

    async def get_events(self, run_id: str, after_seq: int = 0) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("afterSeq must be a non-negative integer")
        async with run.lock:
            events = [event.to_dict() for event in run.events_after(after_seq)]
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
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            if len(participant_ids) not in (2, 3) or len(set(participant_ids)) != len(participant_ids):
                raise InvalidConversationParticipantsError()
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
            run.segments[conversation_id] = [{"segmentId": run.next_segment_identity(), "participants": list(participant_ids), "startedAt": run.clock.as_dict()["label"], "summary": None}]
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
            event = run.append_event("conversation_created", {"conversation": conversation.to_public_dict()})
            event_dict = event.to_dict()
            result = {
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
        assert event_dict is not None
        await self.event_hub.publish(run_id, event_dict)
        return result

    async def add_participant(
        self,
        run_id: str,
        conversation_id: str,
        actor_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint(
            "add_participant", {"conversationId": conversation_id, "actorId": actor_id}
        )
        events_to_publish: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            conversation = run.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(details={"conversationId": conversation_id})
            self._require_actor(actor_id)
            if run.actor_open_conversation(actor_id) is not None:
                raise ActorAlreadyInConversationError(details={"actorId": actor_id})
            before_seq = run.event_seq
            await self._join_conversation_locked(run, conversation, actor_id)
            events_to_publish = [event.to_dict() for event in run.events_after(before_seq)]
            result = {
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
        for event_dict in events_to_publish:
            await self.event_hub.publish(run_id, event_dict)
        return result

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
            conversation = run.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(details={"conversationId": conversation_id})
            if not conversation.is_open:
                raise InvalidConversationParticipantsError("Conversation is already closed.")
            self._require_actor(actor_id)
            before_seq = run.event_seq
            actor = self.registry.actor(actor_id)
            if actor is not None and actor.kind == "npc":
                await self._leave_and_consolidate_locked(run, conversation, actor_id, "api_leave")
            else:
                await self._remove_player_locked(run, conversation, actor_id)
            events_to_publish = [event.to_dict() for event in run.events_after(before_seq)]
            result = {
                "conversation": conversation.to_public_dict(),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
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
        for event_dict in events_to_publish:
            await self.event_hub.publish(run_id, event_dict)
        return result

    async def world_step(
        self,
        run_id: str,
        real_seconds: int,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Advance effective world time (one real second equals one minute)."""

        if isinstance(real_seconds, bool) or not isinstance(real_seconds, int) or real_seconds <= 0:
            raise WorldStepError(
                "realSeconds must be a positive integer.",
                details={"realSeconds": real_seconds},
            )
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("world_step", {"realSeconds": real_seconds})
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            before_seq = run.event_seq
            await self._advance_virtual_locked(run, real_seconds)
            run.append_event("world_stepped", {"worldTime": run.clock.as_dict(), "realSeconds": real_seconds})
            published = [event_item.to_dict() for event_item in run.events_after(before_seq)]
            result = {
                "worldTime": run.clock.as_dict(),
                "run": run.to_public_snapshot(self.registry),
                "advancedMinutes": real_seconds,
            }
            self._record_command(run, command_id, fingerprint, result)
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
        for published_event in published:
            await self.event_hub.publish(run_id, published_event)
        return result

    async def player_join(
        self,
        run_id: str,
        conversation_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        run = await self.get_run_entity(run_id)
        fingerprint = self._fingerprint("player_join", {"conversationId": conversation_id})
        published: list[dict[str, Any]] = []
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            conversation = self._conversation(run, conversation_id)
            if len(conversation.participants) >= 3:
                raise ConversationFullError()
            if run.actor_open_conversation(self.registry.player_actor_id) is not None:
                raise ActorAlreadyInConversationError(details={"actorId": self.registry.player_actor_id})
            before_seq = run.event_seq
            await self._join_conversation_locked(run, conversation, self.registry.player_actor_id)
            published = [event.to_dict() for event in run.events_after(before_seq)]
            result = {
                "conversation": conversation.to_public_dict(),
                "messages": self._public_messages(run.messages.get(conversation_id, [])),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
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
        before_seq = 0
        async with run.lock:
            previous = self._existing_command(run, command_id, fingerprint)
            if previous is not None:
                return deepcopy(previous)
            self._require_active_run(run)
            conversation = self._conversation(run, conversation_id)
            if (
                not conversation.is_open
                or not conversation.has_participant(self.registry.player_actor_id)
            ):
                raise PlayerAccessDeniedError(details={"conversationId": conversation_id})
            before_seq = run.event_seq
            self._write_message_locked(run, conversation, self.registry.player_actor_id, text)
            await self._run_chat_pipeline_locked(run, conversation, trigger_message_id=run.messages[conversation_id][-1]["messageId"])
            result = {
                "conversation": conversation.to_public_dict(),
                "messages": self._public_messages(run.messages.get(conversation_id, [])),
                "run": run.to_public_snapshot(self.registry),
            }
            self._record_command(run, command_id, fingerprint, result)
            events = [item.to_dict() for item in run.events_after(before_seq)]
        for event in events:
            await self.event_hub.publish(run_id, event)
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
            run.idle_counts[conversation_id] = run.idle_counts.get(conversation_id, 0) + 1
            run.append_event("conversation_idle", {"conversationId": conversation_id, "idleCount": run.idle_counts[conversation_id]})
            if run.idle_counts[conversation_id] >= 2:
                await self._close_conversation_locked(run, conversation, "idle")
            result = {"conversation": conversation.to_public_dict(), "run": run.to_public_snapshot(self.registry)}
            events = [item.to_dict() for item in run.events_after(before_seq)]
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
        for event in published:
            await self.event_hub.publish(run_id, event)
        return result

    # ------------------------------------------------------------------
    # World and conversation internals.  These methods run while the Run
    # lock is held.  They append only player-safe public events.

    def _default_positions(self) -> dict[str, dict[str, int]]:
        return {
            "npc_001": {"x": 0, "y": 0},
            "npc_002": {"x": 2, "y": 0},
            "npc_003": {"x": 4, "y": 0},
            "npc_004": {"x": 6, "y": 0},
            "npc_005": {"x": 8, "y": 0},
            "player_001": {"x": 1, "y": 2},
        }

    def _daily_schedule(self, seed: int) -> dict[str, int]:
        ids = [npc.actor_id for npc in self.registry.npcs]
        random.Random(seed).shuffle(ids)
        return {actor_id: (9 + index * 2) * 60 for index, actor_id in enumerate(ids)}

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
        remaining = virtual_minutes
        while remaining and not run.clock.is_ended:
            if run.clock.current.clock_minutes == self.registry.end_hour * 60 + self.registry.end_minute and run.clock.current.day < self.registry.end_day:
                run.clock.current = WorldTime(day=run.clock.current.day + 1, hour=self.registry.active_start_minutes // 60, minute=self.registry.active_start_minutes % 60)
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
                    thought_abs = self._absolute(run.clock.current.day, run.daily_think_minutes[npc.actor_id] // 60, run.daily_think_minutes[npc.actor_id] % 60)
                    if thought_abs > current:
                        candidates.append(thought_abs)
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
            if remaining and run.clock.current.clock_minutes == self.registry.end_hour * 60 + self.registry.end_minute and not run.clock.is_ended:
                if run.clock.current.day < self.registry.end_day:
                    run.clock.current = WorldTime(day=run.clock.current.day + 1, hour=self.registry.active_start_minutes // 60, minute=self.registry.active_start_minutes % 60)
                    run.append_event("world_day_started", {"worldTime": run.clock.as_dict()})
                    for npc in self.registry.npcs:
                        run.fresh_event_context[npc.actor_id] = []
                    await self._process_due_locked(run)

    async def _process_due_locked(self, run: Run) -> None:
        current = self._time_absolute(run)
        # Stable order: script event first, then NPC decisions at that time.
        due_events = sorted(
            [event for event in self.registry.events if event.event_id not in run.fired_event_ids and self._event_absolute(event) <= current],
            key=self._event_absolute,
        )
        for event in due_events:
            if event.world_day == self.registry.end_day and event.at == f"{self.registry.end_hour:02d}:{self.registry.end_minute:02d}":
                await self._finish_chapter_locked(run, event)
                return
            if not self._event_condition_met(run, event):
                run.fired_event_ids.add(event.event_id)
                continue
            await self._apply_world_event_locked(run, event)
        due_thoughts = sorted(
            [npc for npc in self.registry.npcs if run.clock.current.day not in run.thought_days.get(npc.actor_id, set()) and self._absolute(run.clock.current.day, run.daily_think_minutes[npc.actor_id] // 60, run.daily_think_minutes[npc.actor_id] % 60) <= current],
            key=lambda npc: run.daily_think_minutes[npc.actor_id],
        )
        for npc in due_thoughts:
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
            if run.actor_states.get(npc.actor_id, {}).get("status") == "inviting":
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
        active_goals = [goal for goal in run.goals.values() if goal["ownerNpcId"] == npc_id and goal["status"] in {"active", "blocked"}]
        candidates: list[str] = []
        open_count = len(run.open_conversations())
        for candidate in self.registry.actors.values():
            if candidate.actor_id == npc_id or run.actor_states.get(candidate.actor_id, {}).get("status") == "departed":
                continue
            if any(
                invitation.get("status") == "pending"
                and candidate.actor_id
                in {
                    invitation.get("initiatorActorId"),
                    invitation.get("targetActorId"),
                }
                for invitation in run.invitations.values()
            ):
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
                "memoryCache": self._initial_memory_ids(run, npc_id, candidates),
            },
        )
        try:
            agent_result = await self._npc_agent(npc_id).daily_tick(
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
            decision = cast(DailyActionDecision, agent_result.decision)
        except StructuredCallFailed:
            decision = DailyActionDecision(action="wait")
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
            await self._join_conversation_locked(run, open_target, npc_id)
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
            agent_result = await self._npc_agent(target_id).invitation_received(
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
            decision = cast(InvitationDecision, agent_result.decision)
        except StructuredCallFailed:
            decision = InvitationDecision(decision="refuse")
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

    def _validate_new_invitation_locked(
        self,
        run: Run,
        initiator_id: str,
        target_id: str,
    ) -> None:
        self._require_active_run(run)
        if initiator_id == target_id:
            raise InvalidInvitationError("An actor cannot invite itself.")
        if run.actor_states.get(initiator_id, {}).get("status") == "departed":
            raise InvalidInvitationError("The initiator has left the world.")
        if run.actor_states.get(target_id, {}).get("status") == "departed":
            raise InvalidInvitationError("The target has left the world.")
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
        run.segments[conversation_id] = [{"segmentId": run.next_segment_identity(), "participants": list(participant_ids), "startedAt": run.clock.as_dict()["label"], "summary": None}]
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
        run.append_event("conversation_created", {"conversation": conversation.to_public_dict()})
        if opening_speech:
            # The first NPC line is optional if the provider is unavailable;
            # keeping the conversation open lets the player observe or join.
            return conversation
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
        try:
            speech = await self._npc_agent(npc_id).generate_speech(
                self._npc_prompt(
                    run,
                    npc_id,
                    "opening_speech",
                    {
                        "conversationId": conversation.conversation_id,
                        "participants": conversation.participants,
                        "invitingGoalId": goal_id,
                        "intent": intent,
                        "messages": [],
                    },
                )
            )
        except StructuredCallFailed:
            run.append_event("conversation_activity", {"conversationId": conversation.conversation_id, "reason": "speech_unavailable"})
            return
        if speech.text.strip():
            message = self._write_message_locked(run, conversation, npc_id, speech.text)
            await self._run_chat_pipeline_locked(run, conversation, message["messageId"], 8)

    async def _join_conversation_locked(self, run: Run, conversation: Conversation, actor_id: str) -> None:
        if not conversation.is_open or len(conversation.participants) >= 3:
            raise ConversationFullError()
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
        old_participants = list(conversation.participants)
        await self._close_current_segment_locked(run, conversation)
        conversation.add_participant(actor_id)
        run.segments[conversation.conversation_id].append({"segmentId": run.next_segment_identity(), "participants": list(conversation.participants), "startedAt": run.clock.as_dict()["label"], "summary": None})
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
        await self._run_participant_event_locked(
            run,
            conversation,
            old_participants,
            f"actor_joined:{actor_id}",
        )

    async def _run_participant_event_locked(
        self,
        run: Run,
        conversation: Conversation,
        participant_ids: list[str],
        trigger: str,
    ) -> None:
        decisions: dict[str, ChatDecision] = {}
        npc_ids = {npc.actor_id for npc in self.registry.npcs}
        for npc_id in participant_ids:
            if npc_id in npc_ids and npc_id in conversation.participants:
                decisions[npc_id] = await self._run_one_chat_decision_locked(
                    run,
                    conversation,
                    npc_id,
                    trigger,
                )
        for npc_id, decision in list(decisions.items()):
            if decision.action == "leave_chat" and npc_id in conversation.participants:
                await self._leave_and_consolidate_locked(
                    run,
                    conversation,
                    npc_id,
                    "model_leave",
                )
        if not conversation.is_open:
            return
        candidates = [
            (npc_id, decision)
            for npc_id, decision in decisions.items()
            if npc_id in conversation.participants and decision.action == "speak"
        ]
        if not candidates:
            return
        winner_id, winner = self._select_speaker(run, conversation, candidates)
        try:
            speech = await self._npc_agent(winner_id).generate_speech(
                self._npc_prompt(
                    run,
                    winner_id,
                    "speech",
                    {
                        "intent": winner.intent,
                        "conversationId": conversation.conversation_id,
                        **self._chat_context(run, conversation, winner_id),
                    },
                )
            )
        except StructuredCallFailed:
            return
        message = self._write_message_locked(run, conversation, winner_id, speech.text)
        self._apply_spoken_chapter_effects(
            run,
            conversation,
            winner_id,
            winner,
            message["messageId"],
        )
        if winner.leave_chat_after_speaking and winner_id in conversation.participants:
            await self._leave_and_consolidate_locked(
                run,
                conversation,
                winner_id,
                "said_and_left",
            )
        if conversation.is_open:
            await self._run_chat_pipeline_locked(
                run,
                conversation,
                message["messageId"],
                7,
            )

    async def _close_current_segment_locked(self, run: Run, conversation: Conversation) -> None:
        segments = run.segments.get(conversation.conversation_id, [])
        if not segments or segments[-1].get("endedAt") is not None:
            return
        segment = segments[-1]
        segment["endedAt"] = run.clock.as_dict()["label"]
        segment["summary"] = await self._summarize_segment_locked(run, conversation, segment)

    async def _summarize_segment_locked(
        self,
        run: Run,
        conversation: Conversation,
        segment: dict[str, Any],
    ) -> dict[str, Any]:
        messages = [
            self._public_messages([message])[0]
            for message in run.messages.get(conversation.conversation_id, [])
            if message.get("segmentId") == segment.get("segmentId")
        ]
        participants = list(segment.get("participants", []))
        if not messages:
            return {
                "claims": [],
                "commitments": [],
                "revealedFacts": [],
                "openQuestions": [],
                "actorIds": participants,
                "topicHints": [],
            }
        prompt = json.dumps(
            {"protocol": "segment_summary", "messages": messages, "participants": participants},
            ensure_ascii=False,
        )
        try:
            result = await self.decisions.segment_summary(prompt)
            summary = result.model_dump(by_alias=True)
            summary["actorIds"] = [
                actor_id for actor_id in summary["actorIds"] if actor_id in participants
            ]
            return summary
        except StructuredCallFailed:
            return {"claims": [], "commitments": [], "revealedFacts": [], "openQuestions": [], "actorIds": participants, "topicHints": []}

    def _write_message_locked(self, run: Run, conversation: Conversation, author_id: str, text: str) -> dict[str, Any]:
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
        run.messages[conversation.conversation_id].append(message)
        public = {"conversationId": conversation.conversation_id, "messageId": message["messageId"], "authorActorId": author_id}
        if self.registry.player_actor_id in conversation.participants:
            public["text"] = text
        run.append_event("message_created" if "text" in public else "conversation_activity", public)
        run.idle_counts[conversation.conversation_id] = 0
        return message

    async def _run_chat_pipeline_locked(self, run: Run, conversation: Conversation, trigger_message_id: str | None, chain_left: int = 8) -> None:
        if not conversation.is_open:
            return
        decisions: dict[str, ChatDecision] = {}
        for actor_id in list(conversation.participants):
            actor = self.registry.actor(actor_id)
            if actor is None or actor.kind != "npc":
                continue
            if actor_id == (run.messages[conversation.conversation_id][-1]["authorActorId"] if run.messages.get(conversation.conversation_id) else None):
                continue
            decisions[actor_id] = await self._run_one_chat_decision_locked(run, conversation, actor_id, "new_message", trigger_message_id)
        for actor_id, decision in list(decisions.items()):
            if actor_id not in conversation.participants:
                continue
            if decision.action == "leave_chat":
                await self._leave_and_consolidate_locked(run, conversation, actor_id, "model_leave")
        if not conversation.is_open or chain_left <= 0:
            return
        candidates = [(actor_id, decision) for actor_id, decision in decisions.items() if actor_id in conversation.participants and decision.action == "speak"]
        if not candidates:
            return
        winner_id, winner = self._select_speaker(run, conversation, candidates)
        try:
            speech = await self._npc_agent(winner_id).generate_speech(
                self._npc_prompt(
                    run,
                    winner_id,
                    "speech",
                    {
                        "conversationId": conversation.conversation_id,
                        "intent": winner.intent,
                        **self._chat_context(run, conversation, winner_id),
                    },
                )
            )
        except StructuredCallFailed:
            run.append_event("conversation_activity", {"conversationId": conversation.conversation_id, "reason": "speech_unavailable"})
            return
        if not speech.text.strip():
            return
        message = self._write_message_locked(run, conversation, winner_id, speech.text)
        self._apply_spoken_chapter_effects(
            run,
            conversation,
            winner_id,
            winner,
            message["messageId"],
        )
        if winner.leave_chat_after_speaking and winner_id in conversation.participants:
            await self._leave_and_consolidate_locked(run, conversation, winner_id, "said_and_left")
        if conversation.is_open:
            await self._run_chat_pipeline_locked(run, conversation, message["messageId"], chain_left - 1)

    async def _run_one_chat_decision_locked(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
        trigger: str,
        trigger_message_id: str | None = None,
    ) -> ChatDecision:
        cache_key = (conversation.conversation_id, npc_id)
        cached_memory_ids = [
            memory_id
            for memory_id in run.memory_cache.get(cache_key, set())
            if run.memories.get(memory_id, {}).get("ownerNpcId") == npc_id
        ]
        prompt = self._npc_prompt(
            run,
            npc_id,
            "chat_decision",
            {
                "conversationId": conversation.conversation_id,
                "trigger": trigger,
                "triggerMessageId": trigger_message_id,
                **self._chat_context(run, conversation, npc_id),
                "memoryCache": cached_memory_ids,
            },
        )

        def prompt_builder(memory_ids: list[str]) -> str:
            return self._npc_prompt(
                run,
                npc_id,
                "chat_decision_with_memory",
                {
                    "conversationId": conversation.conversation_id,
                    "trigger": trigger,
                    "triggerMessageId": trigger_message_id,
                    **self._chat_context(run, conversation, npc_id),
                    "memoryCache": list(memory_ids),
                },
            )

        try:
            agent_result = await self._npc_agent(npc_id).chat_message_received(
                AgentInvocation(
                    run_id=run.run_id,
                    npc_id=npc_id,
                    event_type="chat_message_received",
                    prompt=prompt,
                    conversation_id=conversation.conversation_id,
                    trigger_message_id=trigger_message_id,
                    visible_messages=tuple(
                        self._visible_messages(run, conversation, npc_id)
                    ),
                    memory_cache=tuple(cached_memory_ids),
                    memory_context=self._memory_tool_context(
                        run,
                        npc_id,
                        conversation.conversation_id,
                    ),
                    prompt_builder=prompt_builder,
                )
            )
            for memory_id in agent_result.recalled_memory_ids:
                memory = run.memories.get(memory_id)
                if memory is not None and memory.get("ownerNpcId") == npc_id:
                    run.memory_cache.setdefault(cache_key, set()).add(memory_id)
            decision = cast(ChatDecision, agent_result.decision)
        except StructuredCallFailed:
            decision = ChatDecision(result="decided", action="wait")
        self._apply_chat_drafts(run, conversation, npc_id, decision)
        return decision

    def _visible_messages(self, run: Run, conversation: Conversation, npc_id: str) -> list[dict[str, Any]]:
        return [deepcopy(message) for message in run.messages.get(conversation.conversation_id, []) if npc_id in message.get("visibleToNpcIds", [])]

    def _chat_context(
        self,
        run: Run,
        conversation: Conversation,
        npc_id: str,
    ) -> dict[str, Any]:
        segments = run.segments.get(conversation.conversation_id, [])
        current_segment_id = segments[-1].get("segmentId") if segments else None
        messages = [
            message
            for message in self._visible_messages(run, conversation, npc_id)
            if message.get("segmentId") == current_segment_id
        ][-20:]
        summaries = [
            {
                "segmentId": segment.get("segmentId"),
                "summary": deepcopy(segment.get("summary")),
            }
            for segment in segments[:-1]
            if npc_id in segment.get("participants", [])
            and segment.get("summary") is not None
        ]
        return {"segmentSummaries": summaries, "messages": messages}

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
        return ordered[:8]

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
        run.actor_states[npc_id]["status"] = "waiting"
        run.append_event("conversation_participant_left" if conversation.is_open else "conversation_closed", {"conversation": conversation.to_public_dict(), "actorLeft": npc_id})
        if conversation.is_open:
            run.segments[conversation.conversation_id].append(
                {
                    "segmentId": run.next_segment_identity(),
                    "participants": list(conversation.participants),
                    "startedAt": run.clock.as_dict()["label"],
                    "summary": None,
                }
            )
        await self._consolidate_locked(run, conversation, npc_id, reason)
        if conversation.is_open and len(conversation.participants) >= 2:
            await self._run_participant_event_locked(
                run,
                conversation,
                list(conversation.participants),
                f"actor_left:{npc_id}",
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
        await self._close_current_segment_locked(run, conversation)
        conversation.remove_participant(player_id)
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
                }
            )
            await self._run_participant_event_locked(
                run,
                conversation,
                list(conversation.participants),
                f"actor_left:{player_id}",
            )
            return
        for remaining in list(conversation.participants):
            actor = self.registry.actor(remaining)
            if actor is not None and actor.kind == "npc":
                await self._consolidate_locked(
                    run,
                    conversation,
                    remaining,
                    "conversation_closed",
                )

    async def _close_conversation_locked(self, run: Run, conversation: Conversation, reason: str) -> None:
        if conversation.is_open:
            await self._close_current_segment_locked(run, conversation)
            conversation.close(reason)
            run.append_event("conversation_closed", {"conversation": conversation.to_public_dict()})
        for actor_id in list(conversation.participants):
            actor = self.registry.actor(actor_id)
            if actor is not None and actor.kind == "npc":
                run.actor_states[actor_id]["status"] = "waiting"
                await self._consolidate_locked(run, conversation, actor_id, reason)
            elif actor is not None and actor.kind == "player":
                run.actor_states[actor_id]["status"] = "present"
                run.memory_cache.pop((conversation.conversation_id, actor_id), None)

    async def _consolidate_locked(self, run: Run, conversation: Conversation, npc_id: str, reason: str) -> None:
        key = (conversation.conversation_id, npc_id)
        existing = run.consolidation_status.get(key)
        if existing and existing.get("status") == "succeeded":
            return
        messages = self._visible_messages(run, conversation, npc_id)
        draft = run.conversation_drafts.get(conversation.conversation_id, {}).get(
            npc_id,
            {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            },
        )
        prompt = self._npc_prompt(run, npc_id, "exit_consolidation", {"reason": reason, "messages": messages, "draft": draft})
        try:
            consolidation = await self.decisions.exit_consolidation(prompt)
        except StructuredCallFailed:
            consolidation = ExitConsolidation()
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
            evidence = list(item.evidence_message_ids)
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
        run.fired_event_ids.add(event.event_id)
        # Stop new invites and close all active chats before reading chapter state.
        for conversation in list(run.open_conversations()):
            await self._close_conversation_locked(run, conversation, "chapter_deadline")
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
        run.chapter_resolution = {
            "chapterId": self.registry.chapter_id,
            "branch": branch,
            "agendaResults": agenda_results,
            "playerTaskResult": player_task,
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

    def _select_speaker(
        self,
        run: Run,
        conversation: Conversation,
        candidates: list[tuple[str, ChatDecision]],
    ) -> tuple[str, ChatDecision]:
        messages = run.messages.get(conversation.conversation_id, [])
        latest_text = str(messages[-1].get("text", "")) if messages else ""
        last_author = messages[-1].get("authorActorId") if messages else None

        def score(item: tuple[str, ChatDecision]) -> tuple[int, int, int]:
            actor_id, decision = item
            actor = self.registry.actor(actor_id)
            mentioned = int(
                actor_id in latest_text
                or (actor is not None and bool(actor.name) and actor.name in latest_text)
            )
            desire = decision.response_desire - int(actor_id == last_author)
            return mentioned, desire, -self._stable_actor_rank(run, actor_id)

        return max(candidates, key=score)

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
        payload = {
            "protocol": protocol,
            "worldTime": run.clock.as_dict(),
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
