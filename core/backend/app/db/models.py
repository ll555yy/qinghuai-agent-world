"""SQLAlchemy 2 models for the authoritative PostgreSQL run store.

The schema intentionally keeps frequently queried ownership, status, time and
graph columns relational.  JSONB is reserved for small, shape-changing
payloads (persona fragments, world-state snapshots, drafts, summaries and
public event/result payloads).  IDs that are scoped to a run use composite
foreign keys so that an ID from one run can never accidentally reference a
record in another run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from .base import Base


def _text_array(*, nullable: bool = False) -> Any:
    """Return a run-safe text array column with an empty-array default."""

    return mapped_column(
        ARRAY(String(64)),
        nullable=nullable,
        default=list,
        server_default=text("'{}'::text[]"),
    )


def _int_array(*, nullable: bool = False) -> Any:
    return mapped_column(
        ARRAY(Integer),
        nullable=nullable,
        default=list,
        server_default=text("'{}'::integer[]"),
    )


def _json_object(*, nullable: bool = False) -> Any:
    return mapped_column(
        JSONB,
        nullable=nullable,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


def _json_array(*, nullable: bool = False) -> Any:
    return mapped_column(
        JSONB,
        nullable=nullable,
        default=list,
        server_default=text("'[]'::jsonb"),
    )


class Actor(Base):
    """Scenario actor identity; immutable scenario data is copied by ID."""

    __tablename__ = "actors"

    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    public_background: Mapped[str] = mapped_column(Text, nullable=False)
    public_impression: Mapped[list[Any]] = _json_array()

    __table_args__ = (
        CheckConstraint("kind IN ('player', 'npc')", name="ck_actors_kind"),
        Index("ix_actors_kind", "kind"),
    )


class NpcProfile(Base):
    """Private, non-runtime NPC profile copied from the ScenarioRegistry."""

    __tablename__ = "npc_profiles"

    actor_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("actors.actor_id", ondelete="CASCADE"),
        primary_key=True,
    )
    persona_summary: Mapped[str] = mapped_column(Text, nullable=False)
    traits: Mapped[dict[str, Any]] = _json_object()
    values: Mapped[dict[str, Any]] = _json_object()
    social_style: Mapped[dict[str, Any]] = _json_object()
    speech_style: Mapped[dict[str, Any]] = _json_object()
    boundaries: Mapped[dict[str, Any]] = _json_object()
    core_secrets: Mapped[dict[str, Any]] = _json_object()


class Topic(Base):
    __tablename__ = "topics"

    topic_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    aliases: Mapped[list[Any]] = _json_array()


class GoalDefinition(Base):
    """Scenario goal definition before it is instantiated for a Run."""

    __tablename__ = "goal_definitions"

    goal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_npc_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("actors.actor_id", ondelete="RESTRICT"), nullable=False
    )
    horizon: Mapped[str] = mapped_column(String(32), nullable=False)
    disclosure: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_actor_ids: Mapped[list[str]] = _text_array()
    topic_ids: Mapped[list[str]] = _text_array()
    initial_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )

    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_goal_definitions_importance"),
        CheckConstraint(
            "horizon IN ('short_term', 'long_term', 'chapter')",
            name="ck_goal_definitions_horizon",
        ),
        CheckConstraint(
            "disclosure IN ('shareable', 'guarded')",
            name="ck_goal_definitions_disclosure",
        ),
        Index("ix_goal_definitions_owner", "owner_npc_id"),
    )


class Agenda(Base):
    __tablename__ = "agendas"

    agenda_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_npc_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("actors.actor_id", ondelete="RESTRICT"), nullable=False
    )
    public_goal_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("goal_definitions.goal_id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("chapter_id", "title", name="uq_agendas_chapter_title"),
        Index("ix_agendas_chapter", "chapter_id"),
    )


class ChapterRun(Base):
    __tablename__ = "chapter_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_revision: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'scenario-v1'")
    )
    player_agenda_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("agendas.agenda_id", ondelete="RESTRICT"), nullable=True
    )
    world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    world_minute: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    clock_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'running'")
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    next_conversation_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    next_segment_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    next_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    next_invitation_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    next_join_request_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    next_memory_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    daily_think_order: Mapped[list[str]] = _text_array()
    public_world_state: Mapped[dict[str, Any]] = _json_object()
    scene_state: Mapped[dict[str, Any]] = _json_object()
    zhou_authorization: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'none'")
    )
    chapter_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    run_finished: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    closed_days: Mapped[list[int]] = _int_array()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint("world_day BETWEEN 1 AND 7", name="ck_chapter_runs_world_day"),
        CheckConstraint("world_minute BETWEEN 0 AND 1439", name="ck_chapter_runs_world_minute"),
        CheckConstraint(
            "clock_status IN ('running', 'paused', 'chapter_ended')",
            name="ck_chapter_runs_clock_status",
        ),
        CheckConstraint(
            "zhou_authorization IN ('none', 'approved', 'conditional', 'rejected')",
            name="ck_chapter_runs_zhou_authorization",
        ),
        CheckConstraint("state_version >= 0", name="ck_chapter_runs_state_version"),
        CheckConstraint("event_seq >= 0", name="ck_chapter_runs_event_seq"),
        CheckConstraint(
            "next_conversation_seq >= 0 AND next_segment_seq >= 0 AND next_message_seq >= 0 "
            "AND next_invitation_seq >= 0 AND next_join_request_seq >= 0 AND next_memory_seq >= 0",
            name="ck_chapter_runs_next_sequences",
        ),
        Index("ix_chapter_runs_chapter_status", "chapter_id", "clock_status"),
    )


class RunActorState(Base):
    __tablename__ = "run_actor_states"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'present'")
    )
    position_x: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    position_y: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    daily_think_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    thought_days: Mapped[list[int]] = _int_array()
    actor_world_state: Mapped[dict[str, Any]] = _json_object()
    fresh_event_memory_ids: Mapped[list[str]] = _text_array()

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_run_actor_states_run"
        ),
        ForeignKeyConstraint(
            ["actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_run_actor_states_actor"
        ),
        CheckConstraint(
            "external_status IN ('present', 'approaching', 'inviting', 'chatting', 'waiting', 'departed')",
            name="ck_run_actor_states_external_status",
        ),
        CheckConstraint(
            "daily_think_minute IS NULL OR daily_think_minute BETWEEN 0 AND 1439",
            name="ck_run_actor_states_daily_think_minute",
        ),
        Index("ix_run_actor_states_status", "run_id", "external_status"),
    )


class RunDailySchedule(Base):
    __tablename__ = "run_daily_schedules"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    world_day: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    npc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slot_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    slot_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    thought_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'scheduled'")
    )
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    finished_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_run_daily_schedules_run"
        ),
        ForeignKeyConstraint(
            ["npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_run_daily_schedules_npc"
        ),
        UniqueConstraint("run_id", "world_day", "slot_minute", name="uq_run_daily_schedules_slot"),
        CheckConstraint("world_day BETWEEN 1 AND 7", name="ck_run_daily_schedules_world_day"),
        CheckConstraint("slot_minute BETWEEN 0 AND 1439", name="ck_run_daily_schedules_slot_minute"),
        CheckConstraint("slot_order >= 0", name="ck_run_daily_schedules_slot_order"),
        CheckConstraint(
            "thought_status IN ('scheduled', 'started', 'completed', 'skipped')",
            name="ck_run_daily_schedules_thought_status",
        ),
        Index("ix_run_daily_schedules_day", "run_id", "world_day", "slot_order"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = _json_object()
    world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_run_events_run"
        ),
        CheckConstraint("event_seq >= 0", name="ck_run_events_event_seq"),
        CheckConstraint("state_version >= 0", name="ck_run_events_state_version"),
        CheckConstraint("world_day BETWEEN 1 AND 7", name="ck_run_events_world_day"),
        CheckConstraint("world_minute BETWEEN 0 AND 1439", name="ck_run_events_world_minute"),
        Index("ix_run_events_state_version", "run_id", "state_version"),
    )


class CommandRecord(Base):
    __tablename__ = "command_records"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[dict[str, Any]] = _json_object()
    state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_command_records_run"
        ),
        Index("ix_command_records_created", "run_id", "created_at"),
    )


class WorldEvent(Base):
    __tablename__ = "world_events"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_label: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    topic_ids: Mapped[list[str]] = _text_array()
    visible_actor_ids: Mapped[list[str]] = _text_array()
    neutral_payload: Mapped[dict[str, Any]] = _json_object()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_world_events_run"
        ),
        CheckConstraint("world_day BETWEEN 1 AND 7", name="ck_world_events_world_day"),
        CheckConstraint("world_minute BETWEEN 0 AND 1439", name="ck_world_events_world_minute"),
        Index("ix_world_events_time", "run_id", "world_day", "world_minute"),
    )


class WorldEventObserver(Base):
    __tablename__ = "world_event_observers"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    observed_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    observed_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "event_id"],
            ["world_events.run_id", "world_events.event_id"],
            ondelete="CASCADE",
            name="fk_world_event_observers_event",
        ),
        ForeignKeyConstraint(
            ["actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_world_event_observers_actor"
        ),
        CheckConstraint(
            "observed_world_day BETWEEN 1 AND 7", name="ck_world_event_observers_day"
        ),
        CheckConstraint(
            "observed_world_minute BETWEEN 0 AND 1439", name="ck_world_event_observers_minute"
        ),
    )


class TopicCandidate(Base):
    __tablename__ = "topic_candidates"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(200), nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    first_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    first_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    last_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    last_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'candidate'")
    )
    promoted_topic_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("topics.topic_id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_topic_candidates_run"
        ),
        CheckConstraint("mention_count > 0", name="ck_topic_candidates_mention_count"),
        CheckConstraint(
            "status IN ('candidate', 'promoted', 'discarded')", name="ck_topic_candidates_status"
        ),
        CheckConstraint(
            "first_world_day BETWEEN 1 AND 7 AND last_world_day BETWEEN 1 AND 7",
            name="ck_topic_candidates_days",
        ),
        CheckConstraint(
            "first_world_minute BETWEEN 0 AND 1439 AND last_world_minute BETWEEN 0 AND 1439",
            name="ck_topic_candidates_minutes",
        ),
        UniqueConstraint("run_id", "normalized_text", name="uq_topic_candidates_normalized"),
        Index("ix_topic_candidates_status", "run_id", "status"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'open'")
    )
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contains_player: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    started_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    started_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ended_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ended_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_conversations_run"
        ),
        UniqueConstraint("run_id", "conversation_seq", name="uq_conversations_sequence"),
        CheckConstraint("conversation_seq > 0", name="ck_conversations_sequence"),
        CheckConstraint(
            "status IN ('open', 'closed')", name="ck_conversations_status"
        ),
        CheckConstraint(
            "started_world_day BETWEEN 1 AND 7", name="ck_conversations_started_day"
        ),
        CheckConstraint(
            "started_world_minute BETWEEN 0 AND 1439", name="ck_conversations_started_minute"
        ),
        CheckConstraint(
            "ended_world_day IS NULL OR ended_world_day BETWEEN 1 AND 7",
            name="ck_conversations_ended_day",
        ),
        CheckConstraint(
            "ended_world_minute IS NULL OR ended_world_minute BETWEEN 0 AND 1439",
            name="ck_conversations_ended_minute",
        ),
        Index("ix_conversations_status", "run_id", "status"),
    )


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    joined_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    joined_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    joined_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    left_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    left_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_conversation_participants_conversation",
        ),
        ForeignKeyConstraint(
            ["actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_conversation_participants_actor"
        ),
        CheckConstraint("joined_seq > 0", name="ck_conversation_participants_joined_seq"),
        CheckConstraint(
            "joined_world_day BETWEEN 1 AND 7", name="ck_conversation_participants_joined_day"
        ),
        CheckConstraint(
            "joined_world_minute BETWEEN 0 AND 1439",
            name="ck_conversation_participants_joined_minute",
        ),
        CheckConstraint(
            "left_world_day IS NULL OR left_world_day BETWEEN 1 AND 7",
            name="ck_conversation_participants_left_day",
        ),
        CheckConstraint(
            "left_world_minute IS NULL OR left_world_minute BETWEEN 0 AND 1439",
            name="ck_conversation_participants_left_minute",
        ),
        Index("ix_conversation_participants_current", "run_id", "conversation_id", "is_current"),
    )


class ConversationSegment(Base):
    __tablename__ = "conversation_segments"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    started_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    started_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ended_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ended_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    participant_ids: Mapped[list[str]] = _text_array()
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_through_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'none'")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_conversation_segments_conversation",
        ),
        UniqueConstraint("run_id", "conversation_id", "segment_seq", name="uq_conversation_segments_seq"),
        CheckConstraint("segment_seq > 0", name="ck_conversation_segments_seq"),
        CheckConstraint(
            "started_world_day BETWEEN 1 AND 7", name="ck_conversation_segments_started_day"
        ),
        CheckConstraint(
            "started_world_minute BETWEEN 0 AND 1439",
            name="ck_conversation_segments_started_minute",
        ),
        CheckConstraint(
            "ended_world_day IS NULL OR ended_world_day BETWEEN 1 AND 7",
            name="ck_conversation_segments_ended_day",
        ),
        CheckConstraint(
            "ended_world_minute IS NULL OR ended_world_minute BETWEEN 0 AND 1439",
            name="ck_conversation_segments_ended_minute",
        ),
        CheckConstraint(
            "summary_status IN ('none', 'pending', 'succeeded', 'failed')",
            name="ck_conversation_segments_summary_status",
        ),
        Index("ix_conversation_segments_conversation", "run_id", "conversation_id", "segment_seq"),
    )


class SegmentParticipant(Base):
    __tablename__ = "segment_participants"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    joined_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    joined_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    left_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    left_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "segment_id"],
            ["conversation_segments.run_id", "conversation_segments.segment_id"],
            ondelete="CASCADE",
            name="fk_segment_participants_segment",
        ),
        ForeignKeyConstraint(
            ["actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_segment_participants_actor"
        ),
        CheckConstraint(
            "joined_world_day BETWEEN 1 AND 7", name="ck_segment_participants_joined_day"
        ),
        CheckConstraint(
            "joined_world_minute BETWEEN 0 AND 1439",
            name="ck_segment_participants_joined_minute",
        ),
        CheckConstraint(
            "left_world_day IS NULL OR left_world_day BETWEEN 1 AND 7",
            name="ck_segment_participants_left_day",
        ),
        CheckConstraint(
            "left_world_minute IS NULL OR left_world_minute BETWEEN 0 AND 1439",
            name="ck_segment_participants_left_minute",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    author_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    visible_to_npc_ids: Mapped[list[str]] = _text_array()
    world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_messages_conversation",
        ),
        ForeignKeyConstraint(
            ["run_id", "segment_id"],
            ["conversation_segments.run_id", "conversation_segments.segment_id"],
            ondelete="CASCADE",
            name="fk_messages_segment",
        ),
        ForeignKeyConstraint(
            ["author_actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_messages_author"
        ),
        UniqueConstraint("run_id", "conversation_id", "message_seq", name="uq_messages_sequence"),
        CheckConstraint("message_seq > 0", name="ck_messages_sequence"),
        CheckConstraint(
            "message_kind IN ('player', 'npc', 'system')", name="ck_messages_kind"
        ),
        CheckConstraint("world_day BETWEEN 1 AND 7", name="ck_messages_world_day"),
        CheckConstraint("world_minute BETWEEN 0 AND 1439", name="ck_messages_world_minute"),
        Index("ix_messages_conversation_time", "run_id", "conversation_id", "message_seq"),
        Index("ix_messages_segment_time", "run_id", "segment_id", "message_seq"),
    )


class ConversationIdleState(Base):
    """D-065 state for pure-NPC automatic idle closure."""

    __tablename__ = "conversation_idle_states"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idle_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_idle_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_idle_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_conversation_idle_states_conversation",
        ),
        CheckConstraint("idle_count >= 0", name="ck_conversation_idle_states_count"),
        CheckConstraint(
            "last_idle_world_day IS NULL OR last_idle_world_day BETWEEN 1 AND 7",
            name="ck_conversation_idle_states_day",
        ),
        CheckConstraint(
            "last_idle_world_minute IS NULL OR last_idle_world_minute BETWEEN 0 AND 1439",
            name="ck_conversation_idle_states_minute",
        ),
    )


class Invitation(Base):
    __tablename__ = "invitations"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invitation_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    initiator_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    private_goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    private_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requested_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    responded_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    responded_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_invitations_run"
        ),
        ForeignKeyConstraint(
            ["initiator_actor_id"],
            ["actors.actor_id"],
            ondelete="RESTRICT",
            name="fk_invitations_initiator",
        ),
        ForeignKeyConstraint(
            ["target_actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_invitations_target"
        ),
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="RESTRICT",
            name="fk_invitations_conversation",
        ),
        ForeignKeyConstraint(
            ["run_id", "private_goal_id"],
            ["goals.run_id", "goals.goal_id"],
            ondelete="RESTRICT",
            name="fk_invitations_private_goal",
        ),
        UniqueConstraint("run_id", "invitation_seq", name="uq_invitations_sequence"),
        CheckConstraint("invitation_seq > 0", name="ck_invitations_sequence"),
        CheckConstraint(
            "initiator_actor_id <> target_actor_id", name="ck_invitations_distinct_actors"
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'refused', 'expired')", name="ck_invitations_status"
        ),
        CheckConstraint(
            "requested_world_day BETWEEN 1 AND 7", name="ck_invitations_requested_day"
        ),
        CheckConstraint(
            "requested_world_minute BETWEEN 0 AND 1439", name="ck_invitations_requested_minute"
        ),
        CheckConstraint(
            "responded_world_day IS NULL OR responded_world_day BETWEEN 1 AND 7",
            name="ck_invitations_responded_day",
        ),
        CheckConstraint(
            "responded_world_minute IS NULL OR responded_world_minute BETWEEN 0 AND 1439",
            name="ck_invitations_responded_minute",
        ),
        Index("ix_invitations_pending", "run_id", "status"),
    )


class JoinRequest(Base):
    __tablename__ = "join_requests"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    join_request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    join_request_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    applicant_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    requested_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requested_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    resolved_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resolved_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_join_requests_run"
        ),
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_join_requests_conversation",
        ),
        ForeignKeyConstraint(
            ["applicant_actor_id"],
            ["actors.actor_id"],
            ondelete="RESTRICT",
            name="fk_join_requests_applicant",
        ),
        UniqueConstraint("run_id", "join_request_seq", name="uq_join_requests_sequence"),
        CheckConstraint("join_request_seq > 0", name="ck_join_requests_sequence"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'refused', 'expired')", name="ck_join_requests_status"
        ),
        CheckConstraint(
            "requested_world_day BETWEEN 1 AND 7", name="ck_join_requests_requested_day"
        ),
        CheckConstraint(
            "requested_world_minute BETWEEN 0 AND 1439",
            name="ck_join_requests_requested_minute",
        ),
        CheckConstraint(
            "resolved_world_day IS NULL OR resolved_world_day BETWEEN 1 AND 7",
            name="ck_join_requests_resolved_day",
        ),
        CheckConstraint(
            "resolved_world_minute IS NULL OR resolved_world_minute BETWEEN 0 AND 1439",
            name="ck_join_requests_resolved_minute",
        ),
        Index("ix_join_requests_conversation_status", "run_id", "conversation_id", "status"),
    )


class JoinRequestApprover(Base):
    """Frozen approver set and per-approver decision for D-058."""

    __tablename__ = "join_request_approvers"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    join_request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approver_actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    decided_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    decided_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "join_request_id"],
            ["join_requests.run_id", "join_requests.join_request_id"],
            ondelete="CASCADE",
            name="fk_join_request_approvers_request",
        ),
        ForeignKeyConstraint(
            ["approver_actor_id"],
            ["actors.actor_id"],
            ondelete="RESTRICT",
            name="fk_join_request_approvers_actor",
        ),
        CheckConstraint(
            "decision IN ('pending', 'accept', 'refuse')", name="ck_join_request_approvers_decision"
        ),
        CheckConstraint(
            "decided_world_day IS NULL OR decided_world_day BETWEEN 1 AND 7",
            name="ck_join_request_approvers_day",
        ),
        CheckConstraint(
            "decided_world_minute IS NULL OR decided_world_minute BETWEEN 0 AND 1439",
            name="ck_join_request_approvers_minute",
        ),
    )


class ConversationDraft(Base):
    __tablename__ = "conversation_drafts"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    npc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    draft_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    payload: Mapped[dict[str, Any]] = _json_object()
    updated_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    updated_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_conversation_drafts_conversation",
        ),
        ForeignKeyConstraint(
            ["npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_conversation_drafts_npc"
        ),
        CheckConstraint("draft_version > 0", name="ck_conversation_drafts_version"),
        CheckConstraint(
            "updated_world_day BETWEEN 1 AND 7", name="ck_conversation_drafts_day"
        ),
        CheckConstraint(
            "updated_world_minute BETWEEN 0 AND 1439", name="ck_conversation_drafts_minute"
        ),
    )


class Consolidation(Base):
    __tablename__ = "consolidations"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    npc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    drafts_committed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    interaction_recorded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    updated_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="CASCADE",
            name="fk_consolidations_conversation",
        ),
        ForeignKeyConstraint(
            ["npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_consolidations_npc"
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')", name="ck_consolidations_status"
        ),
        CheckConstraint("attempts >= 0", name="ck_consolidations_attempts"),
        CheckConstraint("updated_world_day BETWEEN 1 AND 7", name="ck_consolidations_day"),
        CheckConstraint(
            "updated_world_minute BETWEEN 0 AND 1439", name="ck_consolidations_minute"
        ),
    )


class Goal(Base):
    """Run-scoped goal instance; definition and mutable status are separate."""

    __tablename__ = "goals"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition_goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_npc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon: Mapped[str] = mapped_column(String(32), nullable=False)
    disclosure: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_actor_ids: Mapped[list[str]] = _text_array()
    topic_ids: Mapped[list[str]] = _text_array()
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    created_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    resolved_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resolved_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_goals_run"
        ),
        ForeignKeyConstraint(
            ["definition_goal_id"],
            ["goal_definitions.goal_id"],
            ondelete="RESTRICT",
            name="fk_goals_definition",
        ),
        ForeignKeyConstraint(
            ["run_id", "parent_goal_id"],
            ["goals.run_id", "goals.goal_id"],
            ondelete="RESTRICT",
            name="fk_goals_parent",
        ),
        ForeignKeyConstraint(
            ["owner_npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_goals_owner"
        ),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_goals_importance"),
        CheckConstraint(
            "horizon IN ('short_term', 'long_term', 'chapter')", name="ck_goals_horizon"
        ),
        CheckConstraint(
            "disclosure IN ('shareable', 'guarded')", name="ck_goals_disclosure"
        ),
        CheckConstraint(
            "status IN ('active', 'blocked', 'completed', 'abandoned', 'departed')",
            name="ck_goals_status",
        ),
        CheckConstraint("created_world_day BETWEEN 1 AND 7", name="ck_goals_created_day"),
        CheckConstraint(
            "created_world_minute BETWEEN 0 AND 1439", name="ck_goals_created_minute"
        ),
        CheckConstraint(
            "resolved_world_day IS NULL OR resolved_world_day BETWEEN 1 AND 7",
            name="ck_goals_resolved_day",
        ),
        CheckConstraint(
            "resolved_world_minute IS NULL OR resolved_world_minute BETWEEN 0 AND 1439",
            name="ck_goals_resolved_minute",
        ),
        Index("ix_goals_owner_status", "run_id", "owner_npc_id", "status"),
    )


class Relationship(Base):
    __tablename__ = "relationships"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    to_actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    social_roles: Mapped[list[str]] = _text_array()
    familiarity: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    trust: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    affinity: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    tension: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    updated_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_relationships_run"
        ),
        ForeignKeyConstraint(
            ["from_actor_id"],
            ["actors.actor_id"],
            ondelete="RESTRICT",
            name="fk_relationships_from_actor",
        ),
        ForeignKeyConstraint(
            ["to_actor_id"],
            ["actors.actor_id"],
            ondelete="RESTRICT",
            name="fk_relationships_to_actor",
        ),
        CheckConstraint("from_actor_id <> to_actor_id", name="ck_relationships_distinct_actors"),
        CheckConstraint(
            "familiarity BETWEEN 0 AND 3", name="ck_relationships_familiarity"
        ),
        CheckConstraint("trust BETWEEN -2 AND 2", name="ck_relationships_trust"),
        CheckConstraint("affinity BETWEEN -2 AND 2", name="ck_relationships_affinity"),
        CheckConstraint("tension BETWEEN 0 AND 2", name="ck_relationships_tension"),
        CheckConstraint("interaction_count >= 0", name="ck_relationships_interaction_count"),
        CheckConstraint(
            "updated_world_day IS NULL OR updated_world_day BETWEEN 1 AND 7",
            name="ck_relationships_updated_day",
        ),
        CheckConstraint(
            "updated_world_minute IS NULL OR updated_world_minute BETWEEN 0 AND 1439",
            name="ck_relationships_updated_minute",
        ),
        Index("ix_relationships_from_actor", "run_id", "from_actor_id"),
    )


class Memory(Base):
    __tablename__ = "memories"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_npc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    segment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    learned_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    learned_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    occurred_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    occurred_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_recalled_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_recalled_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(MEMORY_EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_memories_run"
        ),
        ForeignKeyConstraint(
            ["owner_npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_memories_owner"
        ),
        ForeignKeyConstraint(
            ["run_id", "event_id"],
            ["world_events.run_id", "world_events.event_id"],
            ondelete="RESTRICT",
            name="fk_memories_event",
        ),
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["conversations.run_id", "conversations.conversation_id"],
            ondelete="RESTRICT",
            name="fk_memories_conversation",
        ),
        ForeignKeyConstraint(
            ["run_id", "segment_id"],
            ["conversation_segments.run_id", "conversation_segments.segment_id"],
            ondelete="RESTRICT",
            name="fk_memories_segment",
        ),
        CheckConstraint(
            "memory_type IN ('event', 'belief', 'commitment', 'relationship_change', 'goal_change', 'reflection')",
            name="ck_memories_type",
        ),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_memories_importance"),
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high')", name="ck_memories_confidence"
        ),
        CheckConstraint(
            "source IN ('scenario_seed', 'world_event', 'conversation', 'reflection')",
            name="ck_memories_source",
        ),
        CheckConstraint("created_world_day BETWEEN 1 AND 7", name="ck_memories_created_day"),
        CheckConstraint(
            "created_world_minute BETWEEN 0 AND 1439", name="ck_memories_created_minute"
        ),
        CheckConstraint(
            "learned_world_day IS NULL OR learned_world_day BETWEEN 1 AND 7",
            name="ck_memories_learned_day",
        ),
        CheckConstraint(
            "learned_world_minute IS NULL OR learned_world_minute BETWEEN 0 AND 1439",
            name="ck_memories_learned_minute",
        ),
        CheckConstraint(
            "occurred_world_day IS NULL OR occurred_world_day BETWEEN 1 AND 7",
            name="ck_memories_occurred_day",
        ),
        CheckConstraint(
            "occurred_world_minute IS NULL OR occurred_world_minute BETWEEN 0 AND 1439",
            name="ck_memories_occurred_minute",
        ),
        CheckConstraint(
            "last_recalled_world_day IS NULL OR last_recalled_world_day BETWEEN 1 AND 7",
            name="ck_memories_recalled_day",
        ),
        CheckConstraint(
            "last_recalled_world_minute IS NULL OR last_recalled_world_minute BETWEEN 0 AND 1439",
            name="ck_memories_recalled_minute",
        ),
        CheckConstraint(
            "(embedding IS NULL AND embedding_dimensions IS NULL AND embedding_model IS NULL) "
            "OR (embedding IS NOT NULL AND embedding_dimensions = 1024 AND embedding_model IS NOT NULL)",
            name="ck_memories_embedding_metadata",
        ),
        Index("ix_memories_owner_time", "run_id", "owner_npc_id", "created_world_day", "created_world_minute"),
        Index("ix_memories_owner_type", "run_id", "owner_npc_id", "memory_type"),
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
    )


class MemoryEvidenceMessage(Base):
    __tablename__ = "memory_evidence_messages"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'source'")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_evidence_messages_memory",
        ),
        ForeignKeyConstraint(
            ["run_id", "message_id"],
            ["messages.run_id", "messages.message_id"],
            ondelete="CASCADE",
            name="fk_memory_evidence_messages_message",
        ),
        CheckConstraint(
            "evidence_role IN ('source', 'support', 'contradiction')",
            name="ck_memory_evidence_messages_role",
        ),
    )


class MemoryActorLink(Base):
    __tablename__ = "memory_actor_links"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    link_role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'mentioned'"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_actor_links_memory",
        ),
        ForeignKeyConstraint(
            ["actor_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_memory_actor_links_actor"
        ),
        CheckConstraint(
            "link_role IN ('owner', 'mentioned', 'subject', 'source')",
            name="ck_memory_actor_links_role",
        ),
        Index("ix_memory_actor_links_actor", "run_id", "actor_id"),
    )


class MemoryTopicLink(Base):
    __tablename__ = "memory_topic_links"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_topic_links_memory",
        ),
        ForeignKeyConstraint(
            ["topic_id"], ["topics.topic_id"], ondelete="RESTRICT", name="fk_memory_topic_links_topic"
        ),
        Index("ix_memory_topic_links_topic", "run_id", "topic_id"),
    )


class MemoryGoalLink(Base):
    __tablename__ = "memory_goal_links"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_goal_links_memory",
        ),
        ForeignKeyConstraint(
            ["run_id", "goal_id"],
            ["goals.run_id", "goals.goal_id"],
            ondelete="CASCADE",
            name="fk_memory_goal_links_goal",
        ),
        CheckConstraint(
            "role IN ('evidence', 'trigger', 'state_change')", name="ck_memory_goal_links_role"
        ),
        Index("ix_memory_goal_links_goal", "run_id", "goal_id"),
    )


class MemoryEdge(Base):
    __tablename__ = "memory_edges"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    to_memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    edge_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "from_memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_edges_from_memory",
        ),
        ForeignKeyConstraint(
            ["run_id", "to_memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="CASCADE",
            name="fk_memory_edges_to_memory",
        ),
        CheckConstraint("from_memory_id <> to_memory_id", name="ck_memory_edges_distinct_memories"),
        CheckConstraint(
            "edge_type IN ('SUPPORTS', 'CAUSES', 'CONTRADICTS', 'SUPERSEDES', 'DERIVED_FROM')",
            name="ck_memory_edges_type",
        ),
        CheckConstraint("created_world_day BETWEEN 1 AND 7", name="ck_memory_edges_day"),
        CheckConstraint("created_world_minute BETWEEN 0 AND 1439", name="ck_memory_edges_minute"),
        Index("ix_memory_edges_from", "run_id", "from_memory_id"),
        Index("ix_memory_edges_to", "run_id", "to_memory_id"),
    )


class ChapterActorStance(Base):
    __tablename__ = "chapter_actor_stances"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    npc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stance: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unknown'")
    )
    source_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    effective_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_chapter_actor_stances_run"
        ),
        ForeignKeyConstraint(
            ["npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_chapter_actor_stances_npc"
        ),
        ForeignKeyConstraint(
            ["run_id", "source_message_id"],
            ["messages.run_id", "messages.message_id"],
            ondelete="RESTRICT",
            name="fk_chapter_actor_stances_message",
        ),
        ForeignKeyConstraint(
            ["run_id", "source_memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="RESTRICT",
            name="fk_chapter_actor_stances_memory",
        ),
        CheckConstraint(
            "stance IN ('unknown', 'support', 'conditional', 'oppose', 'withdrawn')",
            name="ck_chapter_actor_stances_value",
        ),
        CheckConstraint(
            "effective_world_day IS NULL OR effective_world_day BETWEEN 1 AND 7",
            name="ck_chapter_actor_stances_day",
        ),
        CheckConstraint(
            "effective_world_minute IS NULL OR effective_world_minute BETWEEN 0 AND 1439",
            name="ck_chapter_actor_stances_minute",
        ),
    )


class ChapterAuthorization(Base):
    __tablename__ = "chapter_authorizations"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'none'"))
    source_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    effective_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_chapter_authorizations_run"
        ),
        ForeignKeyConstraint(
            ["run_id", "source_message_id"],
            ["messages.run_id", "messages.message_id"],
            ondelete="RESTRICT",
            name="fk_chapter_authorizations_message",
        ),
        ForeignKeyConstraint(
            ["run_id", "source_memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="RESTRICT",
            name="fk_chapter_authorizations_memory",
        ),
        CheckConstraint(
            "value IN ('none', 'approved', 'conditional', 'rejected')",
            name="ck_chapter_authorizations_value",
        ),
        CheckConstraint(
            "effective_world_day IS NULL OR effective_world_day BETWEEN 1 AND 7",
            name="ck_chapter_authorizations_day",
        ),
        CheckConstraint(
            "effective_world_minute IS NULL OR effective_world_minute BETWEEN 0 AND 1439",
            name="ck_chapter_authorizations_minute",
        ),
    )


class ChapterAgendaStance(Base):
    __tablename__ = "chapter_agenda_stances"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agenda_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    npc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stance: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unknown'")
    )
    source_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_world_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    effective_world_minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_chapter_agenda_stances_run"
        ),
        ForeignKeyConstraint(
            ["agenda_id"], ["agendas.agenda_id"], ondelete="RESTRICT", name="fk_chapter_agenda_stances_agenda"
        ),
        ForeignKeyConstraint(
            ["npc_id"], ["actors.actor_id"], ondelete="RESTRICT", name="fk_chapter_agenda_stances_npc"
        ),
        ForeignKeyConstraint(
            ["run_id", "source_message_id"],
            ["messages.run_id", "messages.message_id"],
            ondelete="RESTRICT",
            name="fk_chapter_agenda_stances_message",
        ),
        ForeignKeyConstraint(
            ["run_id", "source_memory_id"],
            ["memories.run_id", "memories.memory_id"],
            ondelete="RESTRICT",
            name="fk_chapter_agenda_stances_memory",
        ),
        CheckConstraint(
            "stance IN ('unknown', 'support', 'conditional', 'oppose', 'withdrawn')",
            name="ck_chapter_agenda_stances_value",
        ),
        CheckConstraint(
            "effective_world_day IS NULL OR effective_world_day BETWEEN 1 AND 7",
            name="ck_chapter_agenda_stances_day",
        ),
        CheckConstraint(
            "effective_world_minute IS NULL OR effective_world_minute BETWEEN 0 AND 1439",
            name="ck_chapter_agenda_stances_minute",
        ),
        Index("ix_chapter_agenda_stances_agenda", "run_id", "agenda_id"),
    )


class ChapterResolution(Base):
    __tablename__ = "chapter_resolutions"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    branch: Mapped[str] = mapped_column(String(64), nullable=False)
    agenda_results: Mapped[dict[str, Any]] = _json_object()
    player_task_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_world_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    resolved_world_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_chapter_resolutions_run"
        ),
        CheckConstraint(
            "branch IN ('consensus_submitted', 'compromise_submitted', 'no_submission')",
            name="ck_chapter_resolutions_branch",
        ),
        CheckConstraint(
            "resolved_world_day BETWEEN 1 AND 7", name="ck_chapter_resolutions_day"
        ),
        CheckConstraint(
            "resolved_world_minute BETWEEN 0 AND 1439",
            name="ck_chapter_resolutions_minute",
        ),
    )


class RunStateItem(Base):
    """Small shape-changing per-run items used by the repository codec.

    This is deliberately an item table, not a single JSON snapshot of the
    aggregate.  Stable entities still have their normalized tables above.
    """

    __tablename__ = "run_state_items"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    section: Mapped[str] = mapped_column(String(96), primary_key=True)
    item_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[dict[str, Any] | list[Any] | str | int | bool | None] = mapped_column(
        JSONB, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["chapter_runs.run_id"], ondelete="CASCADE", name="fk_run_state_items_run"
        ),
        Index("ix_run_state_items_section", "run_id", "section"),
    )


class RunStorageRevision(Base):
    """Numeric optimistic-lock revision owned by the SQL repository."""

    __tablename__ = "run_storage_revisions"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["chapter_runs.run_id"],
            ondelete="CASCADE",
            name="fk_run_storage_revisions_run",
        ),
        CheckConstraint("revision >= 0", name="ck_run_storage_revisions_revision"),
    )
