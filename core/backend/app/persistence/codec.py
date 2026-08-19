"""Explicit storage codec for the mutable :class:`Run` aggregate.

The in-memory aggregate contains asyncio locks and counters used only while a
model call is in flight.  Neither belongs in durable state.  This module keeps
the conversion explicit rather than relying on ``dataclasses.asdict`` (which
would attempt to copy locks and would make future private fields easy to leak
into storage accidentally).

The returned mapping is intentionally split into named sections.  A SQL
repository can persist the sections in normalized tables while still using
the same codec for the less relational, scenario-shaped values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ..domain.clock import WorldClock, WorldTime
from ..domain.conversation import Conversation
from ..domain.run import CommandRecord, Run, RunEvent


def _copy(value: Any) -> Any:
    """Copy a JSON-shaped value without copying aggregate locks."""

    return deepcopy(value)


def _encode_pair_map(
    values: Mapping[tuple[str, str], Any],
    first_key: str,
    second_key: str,
    value_key: str = "value",
) -> list[dict[str, Any]]:
    return [
        {
            first_key: first,
            second_key: second,
            value_key: _copy(value),
        }
        for (first, second), value in values.items()
    ]


def _decode_pair_map(
    values: Sequence[Mapping[str, Any]] | None,
    first_key: str,
    second_key: str,
    value_key: str = "value",
) -> dict[tuple[str, str], dict[str, Any]]:
    decoded: dict[tuple[str, str], dict[str, Any]] = {}
    for item in values or ():
        first = str(item[first_key])
        second = str(item[second_key])
        value = item.get(value_key, {})
        decoded[(first, second)] = dict(_copy(value))
    return decoded


def _encode_conversations(run: Run) -> list[dict[str, Any]]:
    return [
        {
            "conversationId": conversation.conversation_id,
            "creationSeq": conversation.creation_seq,
            "participants": list(conversation.participants),
            "status": conversation.status,
            "closeReason": conversation.close_reason,
            "seenParticipants": sorted(conversation.participant_history()),
        }
        for conversation in run.conversations.values()
    ]


def _new_conversation(data: Mapping[str, Any]) -> Conversation:
    """Restore a Conversation, including a closed one with one participant.

    ``Conversation.__post_init__`` intentionally rejects a newly opened
    conversation with fewer than two participants.  A conversation that has
    already closed after a participant left may legitimately contain one, so
    restoration must bypass the open-state constructor validation.
    """

    participants = [str(item) for item in data.get("participants", ())]
    status = str(data.get("status", "open"))
    if status == "open":
        conversation = Conversation(
            conversation_id=str(data["conversationId"]),
            creation_seq=int(data.get("creationSeq", 0)),
            participants=participants,
        )
    else:
        conversation = Conversation.__new__(Conversation)
        object.__setattr__(conversation, "conversation_id", str(data["conversationId"]))
        object.__setattr__(conversation, "creation_seq", int(data.get("creationSeq", 0)))
        object.__setattr__(conversation, "participants", participants)
        object.__setattr__(conversation, "status", "closed")
        object.__setattr__(conversation, "close_reason", data.get("closeReason"))
        object.__setattr__(conversation, "_seen_participants", set())
    seen = {
        str(item)
        for item in data.get("seenParticipants", participants)
    }
    # Older snapshots may not have stored the participant history explicitly.
    seen.update(participants)
    conversation._seen_participants = seen
    if status == "open":
        conversation.close_reason = data.get("closeReason")
    return conversation


def serialize_run(run: Run) -> dict[str, Any]:
    """Return all durable Run state, excluding locks and in-flight counters."""

    return {
        "runId": run.run_id,
        "playerAgendaId": run.player_agenda_id,
        "seed": run.seed,
        "stateVersion": run.state_version,
        "eventSeq": run.event_seq,
        "clock": {
            "day": run.clock.current.day,
            "hour": run.clock.current.hour,
            "minute": run.clock.current.minute,
            "status": run.clock.status,
            "activeStartMinutes": run.clock.active_start_minutes,
            "activeEndMinutes": run.clock.active_end_minutes,
            "newChatCutoffMinutes": run.clock.new_chat_cutoff_minutes,
            "finalDay": run.clock.final_day,
        },
        "nextConversationSeq": run.next_conversation_seq,
        "actorStates": _copy(run.actor_states),
        "conversations": _encode_conversations(run),
        "commandRecords": [
            {
                "commandId": command_id,
                "fingerprint": record.fingerprint,
                "result": _copy(record.result),
            }
            for command_id, record in run.command_records.items()
        ],
        "positions": _copy(run.positions),
        "dailyThinkMinutes": _copy(run.daily_think_minutes),
        "dailyThinkOrder": list(run.daily_think_order),
        "dailyThinkSchedule": {
            str(day): _copy(schedule)
            for day, schedule in run.daily_think_schedule.items()
        },
        "thoughtDays": {
            npc_id: sorted(days)
            for npc_id, days in run.thought_days.items()
        },
        "goals": _copy(run.goals),
        "relationships": _encode_pair_map(
            run.relationships,
            "fromActorId",
            "toActorId",
        ),
        "memories": _copy(run.memories),
        "memoryLinks": _copy(run.memory_links),
        "memoryCache": [
            {
                "conversationId": conversation_id,
                "npcId": npc_id,
                "memoryIds": sorted(memory_ids),
            }
            for (conversation_id, npc_id), memory_ids in run.memory_cache.items()
        ],
        "worldEvents": _copy(run.world_events),
        "freshEventContext": _copy(run.fresh_event_context),
        "actorWorldState": _copy(run.actor_world_state),
        "sceneState": _copy(run.scene_state),
        "currentWorldState": _copy(run.current_world_state),
        "invitations": _copy(run.invitations),
        "joinRequests": _copy(run.join_requests),
        "messages": _copy(run.messages),
        "segments": _copy(run.segments),
        "conversationDrafts": _copy(run.conversation_drafts),
        "idleCounts": _copy(run.idle_counts),
        "consolidationStatus": _encode_pair_map(
            run.consolidation_status,
            "conversationId",
            "npcId",
        ),
        "chapterActorStances": _copy(run.chapter_actor_stances),
        "zhouAuthorization": run.zhou_authorization,
        "chapterAgendaStances": _encode_pair_map(
            run.chapter_agenda_stances,
            "agendaId",
            "npcId",
            value_key="stance",
        ),
        "chapterResolution": _copy(run.chapter_resolution),
        "firedEventIds": sorted(run.fired_event_ids),
        "nextMessageSeq": run.next_message_seq,
        "nextSegmentSeq": run.next_segment_seq,
        "nextInvitationSeq": run.next_invitation_seq,
        "nextJoinRequestSeq": run.next_join_request_seq,
        "nextMemorySeq": run.next_memory_seq,
        "runFinished": run.run_finished,
        "closedDays": sorted(run.closed_days),
        # These are recovery markers, not locks or counters.  The counters
        # below are intentionally reset by deserialize_run because a provider
        # task cannot survive a process restart.
        "pendingDayEnd": list(run.pending_day_end) if run.pending_day_end else None,
        "pendingChapterEventId": run.pending_chapter_event_id,
        "events": [event.to_dict() for event in run.events],
    }


def _event_from_storage(data: Mapping[str, Any]) -> RunEvent:
    return RunEvent(
        run_id=str(data.get("runId", data.get("run_id", ""))),
        event_seq=int(data.get("eventSeq", data.get("event_seq", 0))),
        state_version=int(data.get("stateVersion", data.get("state_version", 0))),
        event_type=str(data.get("eventType", data.get("event_type", ""))),
        payload=_copy(data.get("payload", {})),
    )


def deserialize_run(
    data: Mapping[str, Any],
    *,
    events: Sequence[RunEvent | Mapping[str, Any]] | None = None,
) -> Run:
    """Restore a Run while constructing fresh asyncio locks.

    ``events`` is accepted separately so a normalized SQL repository can load
    the event table independently of the aggregate rows.  If omitted, events
    embedded in a codec snapshot are used for backwards compatibility.
    """

    clock_data = data.get("clock", {})
    clock = WorldClock(
        current=WorldTime(
            day=int(clock_data.get("day", 1)),
            hour=int(clock_data.get("hour", 9)),
            minute=int(clock_data.get("minute", 0)),
        ),
        status=str(clock_data.get("status", "running")),  # type: ignore[arg-type]
        active_start_minutes=int(clock_data.get("activeStartMinutes", 8 * 60)),
        active_end_minutes=int(clock_data.get("activeEndMinutes", 18 * 60)),
        new_chat_cutoff_minutes=int(clock_data.get("newChatCutoffMinutes", 17 * 60)),
        final_day=int(clock_data.get("finalDay", 7)),
    )
    run = Run(
        run_id=str(data["runId"]),
        player_agenda_id=data.get("playerAgendaId"),
        clock=clock,
        seed=int(data.get("seed", 0)),
        state_version=int(data.get("stateVersion", 0)),
        event_seq=int(data.get("eventSeq", 0)),
        next_conversation_seq=int(data.get("nextConversationSeq", 0)),
    )
    run.actor_states = _copy(data.get("actorStates", {}))
    run.positions = _copy(data.get("positions", {}))
    run.daily_think_minutes = {
        str(actor_id): int(minutes)
        for actor_id, minutes in dict(data.get("dailyThinkMinutes", {})).items()
    }
    run.daily_think_order = [str(item) for item in data.get("dailyThinkOrder", ())]
    run.daily_think_schedule = {
        int(day): {
            str(actor_id): int(minutes)
            for actor_id, minutes in dict(schedule).items()
        }
        for day, schedule in dict(data.get("dailyThinkSchedule", {})).items()
    }
    run.thought_days = {
        str(npc_id): {int(day) for day in days}
        for npc_id, days in dict(data.get("thoughtDays", {})).items()
    }
    run.goals = _copy(data.get("goals", {}))
    run.relationships = _decode_pair_map(
        data.get("relationships"),
        "fromActorId",
        "toActorId",
    )
    run.memories = _copy(data.get("memories", {}))
    run.memory_links = _copy(data.get("memoryLinks", []))
    run.memory_cache = {
        (str(item["conversationId"]), str(item["npcId"])): {
            str(memory_id) for memory_id in item.get("memoryIds", ())
        }
        for item in data.get("memoryCache", ())
    }
    run.world_events = _copy(data.get("worldEvents", {}))
    run.fresh_event_context = _copy(data.get("freshEventContext", {}))
    run.actor_world_state = _copy(data.get("actorWorldState", {}))
    run.scene_state = _copy(data.get("sceneState", {}))
    run.current_world_state = _copy(data.get("currentWorldState", {}))
    run.invitations = _copy(data.get("invitations", {}))
    run.join_requests = _copy(data.get("joinRequests", {}))
    run.messages = _copy(data.get("messages", {}))
    run.segments = _copy(data.get("segments", {}))
    run.conversation_drafts = _copy(data.get("conversationDrafts", {}))
    run.idle_counts = {
        str(conversation_id): int(count)
        for conversation_id, count in dict(data.get("idleCounts", {})).items()
    }
    run.consolidation_status = _decode_pair_map(
        data.get("consolidationStatus"),
        "conversationId",
        "npcId",
    )
    run.chapter_actor_stances = _copy(data.get("chapterActorStances", {}))
    run.zhou_authorization = str(data.get("zhouAuthorization", "none"))
    run.chapter_agenda_stances = {
        (str(item["agendaId"]), str(item["npcId"])): str(item.get("stance", "unknown"))
        for item in data.get("chapterAgendaStances", ())
    }
    run.chapter_resolution = _copy(data.get("chapterResolution"))
    run.fired_event_ids = {str(item) for item in data.get("firedEventIds", ())}
    run.next_message_seq = int(data.get("nextMessageSeq", 0))
    run.next_segment_seq = int(data.get("nextSegmentSeq", 0))
    run.next_invitation_seq = int(data.get("nextInvitationSeq", 0))
    run.next_join_request_seq = int(data.get("nextJoinRequestSeq", 0))
    run.next_memory_seq = int(data.get("nextMemorySeq", 0))
    run.run_finished = bool(data.get("runFinished", False))
    run.closed_days = {int(day) for day in data.get("closedDays", ())}
    pending_day_end = data.get("pendingDayEnd")
    if pending_day_end is not None:
        run.pending_day_end = (int(pending_day_end[0]), str(pending_day_end[1]))
    run.pending_chapter_event_id = data.get("pendingChapterEventId")

    for item in data.get("conversations", ()):
        conversation = _new_conversation(item)
        run.conversations[conversation.conversation_id] = conversation

    for item in data.get("commandRecords", ()):
        run.command_records[str(item["commandId"])] = CommandRecord(
            fingerprint=str(item.get("fingerprint", "")),
            result=_copy(item.get("result", {})),
        )

    raw_events = events if events is not None else data.get("events", ())
    run.events = [
        event if isinstance(event, RunEvent) else _event_from_storage(event)
        for event in raw_events
    ]
    # The event table is authoritative for the event stream.  Keep aggregate
    # counters coherent even when a legacy snapshot omitted them.
    if run.events:
        run.event_seq = max(run.event_seq, max(event.event_seq for event in run.events))
        run.state_version = max(
            run.state_version,
            max(event.state_version for event in run.events),
        )
    return run


# Friendly aliases for callers that prefer storage terminology.
run_to_storage = serialize_run
run_from_storage = deserialize_run


__all__ = [
    "deserialize_run",
    "run_from_storage",
    "run_to_storage",
    "serialize_run",
]
