"""Write the queryable relational projection of one Run aggregate."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from ..domain.run import Run

_TIME = re.compile(r"Day(?P<day>[1-7])\s+(?P<hour>\d{2}):(?P<minute>\d{2})")


def _time(label: Any, run: Run) -> tuple[int, int]:
    match = _TIME.fullmatch(str(label or ""))
    if match is None:
        return run.clock.current.day, run.clock.current.clock_minutes
    return int(match.group("day")), int(match.group("hour")) * 60 + int(match.group("minute"))


def _seq(identifier: str, default: int = 1) -> int:
    try:
        return max(1, int(identifier.rsplit("_", 1)[-1]))
    except (TypeError, ValueError):
        return default


async def _insert(session: Any, table: Any, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    if materialized:
        await session.execute(table.insert(), materialized)


async def replace_normalized_projection(session: Any, run: Run, metadata: Any) -> None:
    """Replace stable runtime entities inside the caller's transaction.

    Raw messages and memories remain individual rows.  The auxiliary
    ``run_state_items`` codec is only a lossless recovery aid for flexible
    structures; these normalized tables are the retrieval/query surface.
    """

    table = metadata.tables
    run_id = run.run_id
    memory_table = table["memories"]
    existing_embeddings: dict[str, dict[str, Any]] = {}
    existing_result = await session.execute(
        memory_table.select()
        .with_only_columns(
            memory_table.c.memory_id,
            memory_table.c.owner_npc_id,
            memory_table.c.content,
            memory_table.c.embedding,
            memory_table.c.embedding_model,
            memory_table.c.embedding_dimensions,
        )
        .where(memory_table.c.run_id == run_id)
    )
    for row in existing_result.mappings():
        existing_embeddings[str(row["memory_id"])] = dict(row)
    delete_order = (
        "chapter_resolutions",
        "chapter_agenda_stances",
        "chapter_authorizations",
        "chapter_actor_stances",
        "memory_edges",
        "memory_goal_links",
        "memory_topic_links",
        "memory_actor_links",
        "memory_evidence_messages",
        "consolidations",
        "conversation_drafts",
        "join_request_approvers",
        "join_requests",
        "invitations",
        "memories",
        "relationships",
        "goals",
        "conversation_idle_states",
        "messages",
        "segment_participants",
        "conversation_segments",
        "conversation_participants",
        "conversations",
    )
    for name in delete_order:
        await session.execute(table[name].delete().where(table[name].c.run_id == run_id))

    await _insert(session, table["conversations"], _conversation_rows(run))
    await _insert(session, table["conversation_participants"], _participant_rows(run))
    await _insert(session, table["conversation_segments"], _segment_rows(run))
    await _insert(session, table["segment_participants"], _segment_participant_rows(run))
    await _insert(session, table["messages"], _message_rows(run))
    await _insert(session, table["conversation_idle_states"], _idle_rows(run))
    await _insert(session, table["goals"], _goal_rows(run))
    await _insert(session, table["relationships"], _relationship_rows(run))
    await _insert(
        session,
        memory_table,
        _memory_rows(run, existing_embeddings=existing_embeddings),
    )
    memory_ids = set(run.memories)
    message_ids = {
        str(message.get("messageId"))
        for messages in run.messages.values()
        for message in messages
    }
    actor_links, topic_links, goal_links, edges = _memory_link_rows(run, memory_ids)
    await _insert(session, table["memory_evidence_messages"], _evidence_rows(run, message_ids))
    await _insert(session, table["memory_actor_links"], actor_links)
    await _insert(session, table["memory_topic_links"], topic_links)
    await _insert(session, table["memory_goal_links"], goal_links)
    await _insert(session, table["memory_edges"], edges)
    await _insert(session, table["invitations"], _invitation_rows(run))
    await _insert(session, table["join_requests"], _join_rows(run))
    await _insert(session, table["join_request_approvers"], _approver_rows(run))
    await _insert(session, table["conversation_drafts"], _draft_rows(run))
    await _insert(session, table["consolidations"], _consolidation_rows(run))
    await _insert(session, table["chapter_actor_stances"], _actor_stance_rows(run))
    await _insert(session, table["chapter_authorizations"], _authorization_rows(run))
    await _insert(session, table["chapter_agenda_stances"], _agenda_stance_rows(run))
    await _insert(session, table["chapter_resolutions"], _resolution_rows(run))


def _conversation_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for conversation in run.conversations.values():
        segments = run.segments.get(conversation.conversation_id, [])
        started = segments[0].get("startedAt") if segments else None
        started_day, started_minute = _time(started, run)
        ended_label = segments[-1].get("endedAt") if segments else None
        ended_day, ended_minute = _time(ended_label, run)
        rows.append(
            {
                "run_id": run.run_id,
                "conversation_id": conversation.conversation_id,
                "conversation_seq": conversation.creation_seq,
                "status": conversation.status,
                "close_reason": conversation.close_reason,
                "started_world_day": started_day,
                "started_world_minute": started_minute,
                "ended_world_day": ended_day if conversation.status == "closed" else None,
                "ended_world_minute": ended_minute if conversation.status == "closed" else None,
            }
        )
    return rows


def _participant_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for conversation in run.conversations.values():
        segments = run.segments.get(conversation.conversation_id, [])
        started_day, started_minute = _time(segments[0].get("startedAt") if segments else None, run)
        for index, actor_id in enumerate(sorted(conversation.participant_history()), start=1):
            current = actor_id in conversation.participants
            rows.append(
                {
                    "run_id": run.run_id,
                    "conversation_id": conversation.conversation_id,
                    "actor_id": actor_id,
                    "joined_seq": index,
                    "is_current": current,
                    "joined_world_day": started_day,
                    "joined_world_minute": started_minute,
                    "left_world_day": None if current else run.clock.current.day,
                    "left_world_minute": None if current else run.clock.current.clock_minutes,
                }
            )
    return rows


def _segment_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for conversation_id, segments in run.segments.items():
        for index, segment in enumerate(segments, start=1):
            started_day, started_minute = _time(segment.get("startedAt"), run)
            ended_day, ended_minute = _time(segment.get("endedAt"), run)
            summary = deepcopy(segment.get("summary"))
            rows.append(
                {
                    "run_id": run.run_id,
                    "segment_id": segment["segmentId"],
                    "conversation_id": conversation_id,
                    "segment_seq": index,
                    "started_world_day": started_day,
                    "started_world_minute": started_minute,
                    "ended_world_day": ended_day if segment.get("endedAt") else None,
                    "ended_world_minute": ended_minute if segment.get("endedAt") else None,
                    "participant_ids": list(segment.get("participants", [])),
                    "summary": summary,
                    "summary_through_message_id": segment.get("summaryThroughMessageId"),
                    "summary_status": "succeeded" if summary is not None else "none",
                }
            )
    return rows


def _segment_participant_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for segments in run.segments.values():
        for segment in segments:
            day, minute = _time(segment.get("startedAt"), run)
            for actor_id in segment.get("participants", []):
                rows.append(
                    {
                        "run_id": run.run_id,
                        "segment_id": segment["segmentId"],
                        "actor_id": actor_id,
                        "is_current": segment.get("endedAt") is None,
                        "joined_world_day": day,
                        "joined_world_minute": minute,
                        "left_world_day": None,
                        "left_world_minute": None,
                    }
                )
    return rows


def _message_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for conversation_id, messages in run.messages.items():
        for index, message in enumerate(messages, start=1):
            day, minute = _time(message.get("createdAt"), run)
            author = str(message["authorActorId"])
            kind = "player" if author.startswith("player_") else "npc"
            rows.append(
                {
                    "run_id": run.run_id,
                    "message_id": message["messageId"],
                    "conversation_id": conversation_id,
                    "segment_id": message["segmentId"],
                    "message_seq": index,
                    "author_actor_id": author,
                    "message_kind": kind,
                    "content": str(message.get("text", "")),
                    "visible_to_npc_ids": list(message.get("visibleToNpcIds", [])),
                    "world_day": day,
                    "world_minute": minute,
                }
            )
    return rows


def _idle_rows(run: Run) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run.run_id,
            "conversation_id": conversation_id,
            "idle_count": count,
            "active": run.conversations[conversation_id].is_open,
            "last_idle_world_day": run.clock.current.day,
            "last_idle_world_minute": run.clock.current.clock_minutes,
        }
        for conversation_id, count in run.idle_counts.items()
        if conversation_id in run.conversations
    ]


def _goal_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for goal_id, goal in run.goals.items():
        day, minute = _time(goal.get("createdAt"), run)
        status = str(goal.get("status", "active"))
        rows.append(
            {
                "run_id": run.run_id,
                "goal_id": goal_id,
                "definition_goal_id": goal_id if not goal_id.startswith("goal_npc_") else None,
                "parent_goal_id": goal.get("parentGoalId"),
                "owner_npc_id": goal["ownerNpcId"],
                "horizon": goal.get("horizon", "short_term"),
                "disclosure": goal.get("disclosure", "guarded"),
                "description": goal.get("description", ""),
                "importance": int(goal.get("importance", 1)),
                "target_actor_ids": list(goal.get("targetActorIds", [])),
                "topic_ids": list(goal.get("topicIds", [])),
                "status": status,
                "created_world_day": day,
                "created_world_minute": minute,
                "resolved_world_day": day if status in {"achieved", "abandoned"} else None,
                "resolved_world_minute": minute if status in {"achieved", "abandoned"} else None,
                "resolution_reason": goal.get("resolutionReason"),
            }
        )
    return rows


def _relationship_rows(run: Run) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run.run_id,
            "from_actor_id": source,
            "to_actor_id": target,
            "social_roles": list(value.get("socialRoles", [])),
            "familiarity": int(value.get("familiarity", 0)),
            "trust": int(value.get("trust", 0)),
            "affinity": int(value.get("affinity", 0)),
            "tension": int(value.get("tension", 0)),
            "interaction_count": int(value.get("interactionCount", 0)),
            "updated_world_day": run.clock.current.day,
            "updated_world_minute": run.clock.current.clock_minutes,
        }
        for (source, target), value in run.relationships.items()
    ]


def _memory_rows(
    run: Run,
    *,
    existing_embeddings: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    segment_ids = {
        str(segment["segmentId"])
        for segments in run.segments.values()
        for segment in segments
    }
    for memory_id, memory in run.memories.items():
        day, minute = _time(memory.get("createdAt"), run)
        segment_id = memory.get("segmentId")
        existing = (existing_embeddings or {}).get(memory_id)
        preserve_embedding = bool(
            existing
            and existing.get("owner_npc_id") == memory.get("ownerNpcId")
            and existing.get("content") == memory.get("content", "")
        )
        embedding_source = existing if preserve_embedding and existing is not None else {}
        rows.append(
            {
                "run_id": run.run_id,
                "memory_id": memory_id,
                "owner_npc_id": memory["ownerNpcId"],
                "memory_type": memory.get("type", "belief"),
                "content": memory.get("content", ""),
                "importance": int(memory.get("importance", 1)),
                "confidence": memory.get("confidence", "medium"),
                "source": memory.get("source", "reflection"),
                "event_id": memory.get("eventId"),
                "conversation_id": memory.get("conversationId"),
                "segment_id": segment_id if segment_id in segment_ids else None,
                "created_world_day": day,
                "created_world_minute": minute,
                "learned_world_day": day,
                "learned_world_minute": minute,
                "occurred_world_day": None,
                "occurred_world_minute": None,
                "last_recalled_world_day": None,
                "last_recalled_world_minute": None,
                "embedding": embedding_source.get("embedding"),
                "embedding_model": (
                    embedding_source.get("embedding_model")
                ),
                "embedding_dimensions": (
                    embedding_source.get("embedding_dimensions")
                ),
            }
        )
    return rows


def _evidence_rows(run: Run, message_ids: set[str]) -> list[dict[str, Any]]:
    rows = {
        (memory_id, message_id): {
            "run_id": run.run_id,
            "memory_id": memory_id,
            "message_id": message_id,
            "evidence_role": "source",
        }
        for memory_id, memory in run.memories.items()
        for message_id in memory.get("evidenceMessageIds", [])
        if message_id in message_ids
    }
    return list(rows.values())


def _memory_link_rows(
    run: Run,
    memory_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    actor: dict[tuple[str, str], dict[str, Any]] = {}
    topic: dict[tuple[str, str], dict[str, Any]] = {}
    goal: dict[tuple[str, str, str], dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for memory_id, memory in run.memories.items():
        for actor_id in memory.get("actorIds", []):
            actor[(memory_id, actor_id)] = {"run_id": run.run_id, "memory_id": memory_id, "actor_id": actor_id, "link_role": "mentioned"}
        for topic_id in memory.get("topicIds", []):
            topic[(memory_id, topic_id)] = {"run_id": run.run_id, "memory_id": memory_id, "topic_id": topic_id}
        for goal_id in memory.get("goalIds", []):
            goal[(memory_id, goal_id, "evidence")] = {"run_id": run.run_id, "memory_id": memory_id, "goal_id": goal_id, "role": "evidence"}
    for link in run.memory_links:
        memory_id = str(link.get("memoryId", ""))
        target = str(link.get("targetId", ""))
        kind = str(link.get("kind", ""))
        if memory_id not in memory_ids:
            continue
        if target.startswith(("npc_", "player_")):
            actor[(memory_id, target)] = {"run_id": run.run_id, "memory_id": memory_id, "actor_id": target, "link_role": "mentioned"}
        elif target.startswith("topic_"):
            topic[(memory_id, target)] = {"run_id": run.run_id, "memory_id": memory_id, "topic_id": target}
        elif target.startswith("goal_"):
            role = str(link.get("role", "evidence"))
            role = role if role in {"evidence", "trigger", "state_change"} else "evidence"
            goal[(memory_id, target, role)] = {"run_id": run.run_id, "memory_id": memory_id, "goal_id": target, "role": role}
        elif target in memory_ids:
            edge_type = kind if kind in {"SUPPORTS", "CAUSES", "CONTRADICTS", "SUPERSEDES", "DERIVED_FROM"} else "DERIVED_FROM"
            if memory_id != target:
                edges[(memory_id, target, edge_type)] = {"run_id": run.run_id, "from_memory_id": memory_id, "to_memory_id": target, "edge_type": edge_type, "created_world_day": run.clock.current.day, "created_world_minute": run.clock.current.clock_minutes}
    return list(actor.values()), list(topic.values()), list(goal.values()), list(edges.values())


def _invitation_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for invitation_id, item in run.invitations.items():
        requested_day, requested_minute = _time(item.get("requestedAt"), run)
        responded_day, responded_minute = _time(item.get("respondedAt"), run)
        rows.append({"run_id": run.run_id, "invitation_id": invitation_id, "invitation_seq": _seq(invitation_id), "initiator_actor_id": item["initiatorActorId"], "target_actor_id": item["targetActorId"], "status": item.get("status", "pending"), "conversation_id": item.get("conversationId"), "private_goal_id": item.get("_goalId"), "private_payload": {"intent": item.get("_intent")}, "requested_world_day": requested_day, "requested_world_minute": requested_minute, "responded_world_day": responded_day if item.get("respondedAt") else None, "responded_world_minute": responded_minute if item.get("respondedAt") else None})
    return rows


def _join_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for request_id, item in run.join_requests.items():
        day, minute = _time(item.get("requestedAt"), run)
        resolved_day, resolved_minute = _time(item.get("resolvedAt"), run)
        rows.append({"run_id": run.run_id, "join_request_id": request_id, "join_request_seq": _seq(request_id), "conversation_id": item["conversationId"], "applicant_actor_id": item["applicantActorId"], "status": item.get("status", "pending"), "requested_world_day": day, "requested_world_minute": minute, "resolved_world_day": resolved_day if item.get("resolvedAt") else None, "resolved_world_minute": resolved_minute if item.get("resolvedAt") else None, "resolution_reason": item.get("resolutionReason")})
    return rows


def _approver_rows(run: Run) -> list[dict[str, Any]]:
    rows = []
    for request_id, item in run.join_requests.items():
        for actor_id in item.get("approverActorIds", []):
            decision = item.get("approverDecisions", {}).get(actor_id, "pending")
            rows.append({"run_id": run.run_id, "join_request_id": request_id, "approver_actor_id": actor_id, "decision": decision, "decided_world_day": run.clock.current.day if decision != "pending" else None, "decided_world_minute": run.clock.current.clock_minutes if decision != "pending" else None})
    return rows


def _draft_rows(run: Run) -> list[dict[str, Any]]:
    return [{"run_id": run.run_id, "conversation_id": conversation_id, "npc_id": npc_id, "draft_version": 1, "payload": deepcopy(payload), "updated_world_day": run.clock.current.day, "updated_world_minute": run.clock.current.clock_minutes} for conversation_id, drafts in run.conversation_drafts.items() for npc_id, payload in drafts.items()]


def _consolidation_rows(run: Run) -> list[dict[str, Any]]:
    return [{"run_id": run.run_id, "conversation_id": conversation_id, "npc_id": npc_id, "status": item.get("status", "pending"), "reason": item.get("reason", "unknown"), "attempts": int(item.get("attempts", 0)), "drafts_committed": bool(item.get("draftsCommitted", False)), "interaction_recorded": bool(item.get("interactionRecorded", False)), "updated_world_day": run.clock.current.day, "updated_world_minute": run.clock.current.clock_minutes} for (conversation_id, npc_id), item in run.consolidation_status.items()]


def _actor_stance_rows(run: Run) -> list[dict[str, Any]]:
    return [{"run_id": run.run_id, "npc_id": npc_id, "stance": stance, "source_message_id": None, "source_memory_id": None, "effective_world_day": run.clock.current.day, "effective_world_minute": run.clock.current.clock_minutes} for npc_id, stance in run.chapter_actor_stances.items()]


def _authorization_rows(run: Run) -> list[dict[str, Any]]:
    return [{"run_id": run.run_id, "value": run.zhou_authorization, "source_message_id": None, "source_memory_id": None, "effective_world_day": run.clock.current.day, "effective_world_minute": run.clock.current.clock_minutes}]


def _agenda_stance_rows(run: Run) -> list[dict[str, Any]]:
    return [{"run_id": run.run_id, "agenda_id": agenda_id, "npc_id": npc_id, "stance": stance, "source_message_id": None, "source_memory_id": None, "effective_world_day": run.clock.current.day, "effective_world_minute": run.clock.current.clock_minutes} for (agenda_id, npc_id), stance in run.chapter_agenda_stances.items()]


def _resolution_rows(run: Run) -> list[dict[str, Any]]:
    if run.chapter_resolution is None:
        return []
    value = run.chapter_resolution
    return [{"run_id": run.run_id, "branch": value.get("branch", "no_submission"), "agenda_results": deepcopy(value.get("agendaResults", {})), "player_task_result": value.get("playerTaskResult"), "resolved_world_day": run.clock.current.day, "resolved_world_minute": run.clock.current.clock_minutes}]


__all__ = ["replace_normalized_projection"]
