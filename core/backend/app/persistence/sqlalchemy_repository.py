"""PostgreSQL Run repository implemented with SQLAlchemy async sessions.

The repository keeps the durable aggregate split across the schema-owned
``chapter_runs``, actor/schedule, event, command, and world-event tables.  A
small ``run_state_items`` table stores shape-changing per-run records
    (drafts, messages, memories, relationships and conversation round state)
    one item per row.  It is deliberately
not a single JSON document containing the whole Run.  ``run_storage_revisions``
provides a numeric optimistic-lock token without changing the public domain
model.

SQLAlchemy and psycopg are imported lazily.  The default test application uses
the in-memory repository and must remain importable in environments where the
optional PostgreSQL dependencies have not been installed.
"""

from __future__ import annotations

import asyncio
import builtins
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, NamedTuple

from ..domain.run import Run, RunEvent
from .codec import deserialize_run, serialize_run
from .normalized_projection import replace_normalized_projection
from .run_repository import RepositoryConflictError


class _DbModels(NamedTuple):
    base: Any
    chapter_run: Any
    actor_state: Any
    daily_schedule: Any
    event: Any
    command: Any
    world_event: Any
    world_event_observer: Any
    state_items: Any
    revisions: Any


def _import_db_models() -> _DbModels:
    """Import schema models after optional SQL dependencies are available."""

    from ..db.base import Base
    from ..db.models import (
        ChapterRun,
        CommandRecord,
        RunActorState,
        RunDailySchedule,
        RunStateItem,
        RunStorageRevision,
        WorldEvent,
        WorldEventObserver,
    )
    from ..db.models import (
        RunEvent as RunEventRow,
    )

    # These two tables are first-class schema models.  Do not manufacture
    # ad-hoc SQLAlchemy tables here: doing so can make a development process
    # appear healthy while the checked-in migration set is missing storage
    # required for recovery.
    state_items = RunStateItem.__table__
    revisions = RunStorageRevision.__table__
    return _DbModels(
        Base,
        ChapterRun,
        RunActorState,
        RunDailySchedule,
        RunEventRow,
        CommandRecord,
        WorldEvent,
        WorldEventObserver,
        state_items,
        revisions,
    )


def _minute_of_day(run: Run) -> int:
    return run.clock.current.hour * 60 + run.clock.current.minute


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: Any) -> Any:
    return deepcopy(value)


class SQLAlchemyRunRepository:
    """Async PostgreSQL implementation of :class:`RunRepository`.

    The constructor accepts an already-created async engine or session factory
    to keep unit tests independent of a live database.  With only a URL it
    creates and owns the engine and disposes it from :meth:`close`.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine: Any | None = None,
        session_factory: Any | None = None,
        chapter_id: str = "qinghuai_bookstore_day1_7",
        echo: bool = False,
    ) -> None:
        self._models: _DbModels | None = None
        self._engine = engine
        self._session_factory = session_factory
        self._owns_engine = engine is None and session_factory is None
        self._runs: dict[str, Run] = {}
        self._revisions: dict[str, int] = {}
        self._cache_lock = asyncio.Lock()
        self._closed = False
        self._chapter_id = chapter_id
        if self._engine is None and self._session_factory is None:
            if not database_url:
                raise ValueError("database_url is required when no engine is supplied")
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            if database_url.startswith("postgresql://"):
                database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
            self._engine = create_async_engine(
                database_url,
                pool_pre_ping=True,
                echo=echo,
            )
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> Any:
        return self._engine

    @property
    def session_factory(self) -> Any:
        """Expose the shared factory to read-only database adapters."""

        return self._require_session_factory()

    def _require_session_factory(self) -> Any:
        if self._session_factory is None:
            raise RuntimeError("SQLAlchemy repository has no session factory")
        if self._models is None:
            self._models = _import_db_models()
        return self._session_factory

    async def add(self, run: Run) -> None:
        """Insert a Run plus all initial rows in one transaction."""

        factory = self._require_session_factory()
        models = self._models
        assert models is not None
        async with self._cache_lock:
            async with factory() as session:
                async with session.begin():
                    existing = await session.get(models.chapter_run, run.run_id)
                    if existing is not None:
                        raise ValueError(f"Run {run.run_id!r} already exists")
                    session.add(self._chapter_row(run, models))
                    await session.flush()
                    await self._insert_actor_rows(session, run, models)
                    await self._insert_schedule_rows(session, run, models)
                    await self._insert_event_rows(session, run, models)
                    await self._insert_command_rows(session, run, models)
                    await self._insert_world_event_rows(session, run, models)
                    await self._replace_state_items(session, run, models)
                    await replace_normalized_projection(session, run, models.base.metadata)
                    await session.execute(
                        models.revisions.insert().values(run_id=run.run_id, revision=0)
                    )
            self._runs[run.run_id] = run
            self._revisions[run.run_id] = 0

    async def get(self, run_id: str) -> Run | None:
        """Load once per process and then return the stable identity object."""

        async with self._cache_lock:
            cached = self._runs.get(run_id)
            if cached is not None:
                return cached
            factory = self._require_session_factory()
            models = self._models
            assert models is not None
            async with factory() as session:
                row = await session.get(models.chapter_run, run_id)
                if row is None:
                    return None
                run = await self._load_run(session, row, models)
                revision_value = await self._read_revision(session, run_id, models)
                assert revision_value is not None
            self._runs[run_id] = run
            self._revisions[run_id] = revision_value
            return run

    async def save(self, run: Run, *, expected_revision: int | None = None) -> int:
        """Persist a transition with row-lock based optimistic concurrency."""

        factory = self._require_session_factory()
        models = self._models
        assert models is not None
        async with self._cache_lock:
            cached_revision = self._revisions.get(run.run_id)
            if cached_revision is None:
                cached_revision = await self.revision(run.run_id)
            if cached_revision is None:
                raise KeyError(f"unknown Run: {run.run_id}")
            expected = cached_revision if expected_revision is None else expected_revision
            async with factory() as session:
                async with session.begin():
                    revision_row = await session.execute(
                        models.revisions.select()
                        .where(models.revisions.c.run_id == run.run_id)
                        .with_for_update()
                    )
                    revision_record = revision_row.mappings().first()
                    actual = (
                        int(revision_record["revision"])
                        if revision_record is not None
                        else 0
                    )
                    if expected != actual:
                        raise RepositoryConflictError(run.run_id, expected, actual)
                    chapter = await session.get(
                        models.chapter_run,
                        run.run_id,
                        with_for_update=True,
                    )
                    if chapter is None:
                        raise KeyError(f"unknown Run: {run.run_id}")
                    await self._update_chapter_row(chapter, run)
                    await self._replace_actor_rows(session, run, models)
                    await self._replace_schedule_rows(session, run, models)
                    await self._replace_state_items(session, run, models)
                    await self._replace_world_event_rows(session, run, models)
                    await replace_normalized_projection(session, run, models.base.metadata)
                    await self._insert_missing_events(session, run, models)
                    await self._replace_command_rows(session, run, models)
                    new_revision = actual + 1
                    await session.execute(
                        models.revisions.update()
                        .where(models.revisions.c.run_id == run.run_id)
                        .values(revision=new_revision)
                    )
            self._runs[run.run_id] = run
            self._revisions[run.run_id] = new_revision
            return new_revision

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        factory = self._require_session_factory()
        models = self._models
        assert models is not None
        async with factory() as session:
            result = await session.execute(
                models.event.__table__.select()
                .where(
                    models.event.run_id == run_id,
                    models.event.event_seq > after_seq,
                )
                .order_by(models.event.event_seq)
            )
            return [self._domain_event(row) for row in result.mappings()]

    async def revision(self, run_id: str) -> int | None:
        factory = self._require_session_factory()
        models = self._models
        assert models is not None
        async with factory() as session:
            return await self._read_revision(session, run_id, models, missing_none=True)

    async def list(self) -> list[Run]:
        factory = self._require_session_factory()
        models = self._models
        assert models is not None
        async with factory() as session:
            result = await session.execute(
                models.chapter_run.__table__.select().order_by(models.chapter_run.run_id)
            )
            run_ids = [str(row.run_id) for row in result]
        runs: list[Run] = []
        for run_id in run_ids:
            run = await self.get(run_id)
            if run is not None:
                runs.append(run)
        return runs

    async def healthcheck(self) -> bool:
        try:
            factory = self._require_session_factory()

            async with factory() as session:
                # Connectivity alone is insufficient: a process pointed at
                # an unmigrated database must fail health checks instead of
                # silently presenting an empty Run store.
                await session.execute(self._models.chapter_run.__table__.select().limit(1))  # type: ignore[union-attr]
                await session.execute(self._models.revisions.select().limit(1))  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_engine and self._engine is not None:
            await self._engine.dispose()

    # ------------------------------------------------------------------
    # Row construction and restoration

    def _chapter_row(self, run: Run, models: _DbModels) -> Any:
        row = models.chapter_run(
            run_id=run.run_id,
            chapter_id=self._chapter_id,
            player_agenda_id=run.player_agenda_id,
            world_day=run.clock.current.day,
            world_minute=_minute_of_day(run),
            clock_status=run.clock.status,
            seed=run.seed,
            state_version=run.state_version,
            event_seq=run.event_seq,
            next_conversation_seq=run.next_conversation_seq,
            next_segment_seq=run.next_segment_seq,
            next_message_seq=run.next_message_seq,
            next_invitation_seq=run.next_invitation_seq,
            next_join_request_seq=run.next_join_request_seq,
            next_memory_seq=run.next_memory_seq,
            daily_think_order=list(run.daily_think_order),
            public_world_state=deepcopy(run.current_world_state),
            scene_state=deepcopy(run.scene_state),
            zhou_authorization=run.zhou_authorization,
            chapter_resolution=deepcopy(run.chapter_resolution),
            run_finished=run.run_finished,
            closed_days=sorted(run.closed_days),
        )
        return row

    async def _update_chapter_row(self, row: Any, run: Run) -> None:
        row.world_day = run.clock.current.day
        row.world_minute = _minute_of_day(run)
        row.clock_status = run.clock.status
        row.player_agenda_id = run.player_agenda_id
        row.seed = run.seed
        row.state_version = run.state_version
        row.event_seq = run.event_seq
        row.next_conversation_seq = run.next_conversation_seq
        row.next_segment_seq = run.next_segment_seq
        row.next_message_seq = run.next_message_seq
        row.next_invitation_seq = run.next_invitation_seq
        row.next_join_request_seq = run.next_join_request_seq
        row.next_memory_seq = run.next_memory_seq
        row.daily_think_order = list(run.daily_think_order)
        row.public_world_state = deepcopy(run.current_world_state)
        row.scene_state = deepcopy(run.scene_state)
        row.zhou_authorization = run.zhou_authorization
        row.chapter_resolution = deepcopy(run.chapter_resolution)
        row.run_finished = run.run_finished
        row.closed_days = sorted(run.closed_days)

    async def _load_run(self, session: Any, row: Any, models: _DbModels) -> Run:
        state = {
            "runId": row.run_id,
            "playerAgendaId": row.player_agenda_id,
            "seed": row.seed,
            "stateVersion": row.state_version,
            "eventSeq": row.event_seq,
            "nextConversationSeq": row.next_conversation_seq,
            "dailyThinkOrder": list(row.daily_think_order or []),
            "clock": {
                "day": row.world_day,
                "hour": int(row.world_minute) // 60,
                "minute": int(row.world_minute) % 60,
                "status": row.clock_status,
                "activeStartMinutes": 8 * 60,
                "activeEndMinutes": 18 * 60,
                "newChatCutoffMinutes": 17 * 60,
                "finalDay": 7,
            },
            "nextSegmentSeq": row.next_segment_seq,
            "nextMessageSeq": row.next_message_seq,
            "nextInvitationSeq": row.next_invitation_seq,
            "nextJoinRequestSeq": row.next_join_request_seq,
            "nextMemorySeq": row.next_memory_seq,
            "currentWorldState": deepcopy(row.public_world_state or {}),
            "sceneState": deepcopy(row.scene_state or {}),
            "zhouAuthorization": row.zhou_authorization,
            "chapterResolution": deepcopy(row.chapter_resolution),
            "runFinished": row.run_finished,
            "closedDays": list(row.closed_days or []),
        }
        item_result = await session.execute(
            models.state_items.select().where(models.state_items.c.run_id == row.run_id)
        )
        for item in item_result.mappings():
            self._apply_state_item(state, item["section"], item["item_key"], item["value"])

        actor_result = await session.execute(
            models.actor_state.__table__.select().where(models.actor_state.run_id == row.run_id)
        )
        actor_rows = list(actor_result.mappings())
        state["actorStates"] = {
            str(item["actor_id"]): {"status": item["external_status"]}
            for item in actor_rows
        }
        state["positions"] = {
            str(item["actor_id"]): {
                "x": int(item["position_x"]),
                "y": int(item["position_y"]),
            }
            for item in actor_rows
        }
        state["dailyThinkMinutes"] = {
            str(item["actor_id"]): int(item["daily_think_minute"])
            for item in actor_rows
            if item["daily_think_minute"] is not None
        }
        state["thoughtDays"] = {
            str(item["actor_id"]): list(item["thought_days"] or [])
            for item in actor_rows
        }
        state["actorWorldState"] = {
            str(item["actor_id"]): deepcopy(item["actor_world_state"] or {})
            for item in actor_rows
        }
        state["freshEventContext"] = {
            str(item["actor_id"]): list(item["fresh_event_memory_ids"] or [])
            for item in actor_rows
        }

        schedule_result = await session.execute(
            models.daily_schedule.__table__.select().where(
                models.daily_schedule.run_id == row.run_id
            )
        )
        daily_schedule: dict[str, dict[str, int]] = {}
        for item in schedule_result.mappings():
            daily_schedule.setdefault(str(item["world_day"]), {})[
                str(item["npc_id"])
            ] = int(item["slot_minute"])
        state["dailyThinkSchedule"] = daily_schedule

        world_result = await session.execute(
            models.world_event.__table__.select().where(models.world_event.run_id == row.run_id)
        )
        state["worldEvents"] = {
            str(item["event_id"]): deepcopy(item["neutral_payload"] or {})
            for item in world_result.mappings()
        }

        command_result = await session.execute(
            models.command.__table__.select().where(models.command.run_id == row.run_id)
        )
        state["commandRecords"] = [
            {
                "commandId": str(item["command_id"]),
                "fingerprint": str(item["fingerprint"]),
                "result": deepcopy(item["result"] or {}),
            }
            for item in command_result.mappings()
        ]

        event_result = await session.execute(
            models.event.__table__.select()
            .where(models.event.run_id == row.run_id)
            .order_by(models.event.event_seq)
        )
        events = [self._domain_event(item) for item in event_result.mappings()]
        return deserialize_run(state, events=events)

    @staticmethod
    def _apply_state_item(
        state: dict[str, Any], section: str, item_key: str, value: Any
    ) -> None:
        if section in {"meta", "root"}:
            state[item_key] = deepcopy(value)
            return
        if section == "firedEventIds":
            state.setdefault(section, []).append(item_key)
            return
        if section in {"memoryLinks"}:
            state.setdefault(section, []).append(deepcopy(value))
            return
        if section == "memoryCache":
            state.setdefault(section, []).append(deepcopy(value))
            return
        if section == "chapterActorStances":
            state.setdefault(section, {})[item_key] = deepcopy(value)
            return
        if section == "conversations":
            state.setdefault(section, []).append(deepcopy(value))
            return
        if section in {"goals", "memories", "invitations", "joinRequests"}:
            state.setdefault(section, {})[item_key] = deepcopy(value)
            return
        if section in {
            "messages",
            "segments",
            "conversationDrafts",
            "conversationRoundStates",
            "conversation_round_states",
            "idleCounts",
        }:
            target_section = (
                "conversationRoundStates"
                if section == "conversation_round_states"
                else section
            )
            state.setdefault(target_section, {})[item_key] = deepcopy(value)
            return
        if section in {"relationships", "consolidationStatus"}:
            pair = json.loads(item_key)
            state.setdefault(section, []).append(
                {"fromActorId": pair[0], "toActorId": pair[1], "value": deepcopy(value)}
                if section == "relationships"
                else {
                    "conversationId": pair[0],
                    "npcId": pair[1],
                    "value": deepcopy(value),
                }
            )
            return
        if section == "chapterAgendaStances":
            pair = json.loads(item_key)
            state.setdefault(section, []).append(
                {"agendaId": pair[0], "npcId": pair[1], "stance": deepcopy(value)}
            )
            return
        state[section] = deepcopy(value)

    def _domain_event(self, row: Any) -> RunEvent:
        def read(key: str) -> Any:
            return row.get(key) if isinstance(row, Mapping) else getattr(row, key)

        return RunEvent(
            run_id=str(read("run_id")),
            event_seq=int(read("event_seq")),
            state_version=int(read("state_version")),
            event_type=str(read("event_type")),
            payload=deepcopy(read("payload") or {}),
        )

    async def _read_revision(
        self,
        session: Any,
        run_id: str,
        models: _DbModels,
        *,
        missing_none: bool = False,
    ) -> int | None:
        result = await session.execute(
            models.revisions.select().where(models.revisions.c.run_id == run_id)
        )
        row = result.mappings().first()
        if row is None:
            return None if missing_none else 0
        return int(row["revision"])

    async def _insert_actor_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        rows = self._actor_rows(run)
        if rows:
            await session.execute(models.actor_state.__table__.insert(), rows)

    async def _replace_actor_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        await session.execute(
            models.actor_state.__table__.delete().where(models.actor_state.run_id == run.run_id)
        )
        await self._insert_actor_rows(session, run, models)

    def _actor_rows(self, run: Run) -> builtins.list[dict[str, Any]]:
        rows: builtins.list[dict[str, Any]] = []
        actor_ids = (
            set(run.actor_states)
            | set(run.positions)
            | set(run.daily_think_minutes)
            | set(run.thought_days)
            | set(run.actor_world_state)
            | set(run.fresh_event_context)
        )
        for actor_id in actor_ids:
            rows.append(
                {
                    "run_id": run.run_id,
                    "actor_id": actor_id,
                    "external_status": run.actor_states.get(actor_id, {}).get("status", "present"),
                    "position_x": int(run.positions.get(actor_id, {}).get("x", 0)),
                    "position_y": int(run.positions.get(actor_id, {}).get("y", 0)),
                    "daily_think_minute": run.daily_think_minutes.get(actor_id),
                    "thought_days": sorted(run.thought_days.get(actor_id, set())),
                    "actor_world_state": deepcopy(run.actor_world_state.get(actor_id, {})),
                    "fresh_event_memory_ids": list(run.fresh_event_context.get(actor_id, [])),
                }
            )
        return rows

    async def _insert_schedule_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        rows = [
            {
                "run_id": run.run_id,
                "world_day": int(day),
                "npc_id": npc_id,
                "slot_minute": int(slot),
                "slot_order": index,
                "thought_status": "started" if int(day) in run.thought_days.get(npc_id, set()) else "scheduled",
            }
            for day, schedule in run.daily_think_schedule.items()
            for index, (npc_id, slot) in enumerate(sorted(schedule.items(), key=lambda item: item[1]))
        ]
        if rows:
            await session.execute(models.daily_schedule.__table__.insert(), rows)

    async def _replace_schedule_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        await session.execute(
            models.daily_schedule.__table__.delete().where(models.daily_schedule.run_id == run.run_id)
        )
        await self._insert_schedule_rows(session, run, models)

    async def _insert_event_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        rows = [self._event_row(event, run, models) for event in run.events]
        if rows:
            await session.execute(models.event.__table__.insert(), rows)

    def _event_row(self, event: RunEvent, run: Run, models: _DbModels) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "event_seq": event.event_seq,
            "state_version": event.state_version,
            "event_type": event.event_type,
            "payload": deepcopy(event.payload),
            "world_day": run.clock.current.day,
            "world_minute": _minute_of_day(run),
        }

    async def _insert_missing_events(self, session: Any, run: Run, models: _DbModels) -> None:
        result = await session.execute(
            models.event.__table__.select()
            .with_only_columns(models.event.event_seq)
            .where(models.event.run_id == run.run_id)
        )
        existing = {int(row[0]) for row in result}
        rows = [
            self._event_row(event, run, models)
            for event in run.events
            if event.event_seq not in existing
        ]
        if rows:
            await session.execute(models.event.__table__.insert(), rows)

    async def _replace_command_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        await session.execute(
            models.command.__table__.delete().where(models.command.run_id == run.run_id)
        )
        rows = [
            {
                "run_id": run.run_id,
                "command_id": command_id,
                "fingerprint": record.fingerprint,
                "result": deepcopy(record.result),
                "state_version": run.state_version,
            }
            for command_id, record in run.command_records.items()
        ]
        if rows:
            await session.execute(models.command.__table__.insert(), rows)

    async def _insert_command_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        await self._replace_command_rows(session, run, models)

    async def _insert_world_event_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        rows = [self._world_event_row(run, event_id, event, models) for event_id, event in run.world_events.items()]
        if rows:
            await session.execute(models.world_event.__table__.insert(), rows)
            observers = [
                {
                    "run_id": run.run_id,
                    "event_id": event_id,
                    "actor_id": actor_id,
                    "observed_world_day": int(event.get("worldDay", run.clock.current.day)),
                    "observed_world_minute": self._event_minute(event, run),
                }
                for event_id, event in run.world_events.items()
                for actor_id in event.get("visibleActorIds", [])
            ]
            if observers:
                await session.execute(models.world_event_observer.__table__.insert(), observers)

    @staticmethod
    def _event_minute(event: Mapping[str, Any], run: Run) -> int:
        at = str(event.get("at", ""))
        if ":" not in at:
            return _minute_of_day(run)
        hour, minute = (int(item) for item in at.split(":", 1))
        return hour * 60 + minute

    async def _replace_world_event_rows(self, session: Any, run: Run, models: _DbModels) -> None:
        from sqlalchemy.dialects.postgresql import insert

        await session.execute(
            models.world_event_observer.__table__.delete().where(
                models.world_event_observer.run_id == run.run_id
            )
        )
        rows = [
            self._world_event_row(run, event_id, event, models)
            for event_id, event in run.world_events.items()
        ]
        if rows:
            statement = insert(models.world_event.__table__).values(rows)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["run_id", "event_id"],
                    set_={
                        "world_day": statement.excluded.world_day,
                        "world_minute": statement.excluded.world_minute,
                        "event_kind": statement.excluded.event_kind,
                        "source_label": statement.excluded.source_label,
                        "summary": statement.excluded.summary,
                        "topic_ids": statement.excluded.topic_ids,
                        "visible_actor_ids": statement.excluded.visible_actor_ids,
                        "neutral_payload": statement.excluded.neutral_payload,
                    },
                )
            )
            observers = [
                {
                    "run_id": run.run_id,
                    "event_id": event_id,
                    "actor_id": actor_id,
                    "observed_world_day": int(event.get("worldDay", run.clock.current.day)),
                    "observed_world_minute": self._event_minute(event, run),
                }
                for event_id, event in run.world_events.items()
                for actor_id in event.get("visibleActorIds", [])
            ]
            if observers:
                await session.execute(
                    models.world_event_observer.__table__.insert(), observers
                )

    def _world_event_row(
        self,
        run: Run,
        event_id: str,
        event: Mapping[str, Any],
        models: _DbModels,
    ) -> dict[str, Any]:
        world_day = int(event.get("worldDay", run.clock.current.day))
        at = str(event.get("at", "00:00"))
        hour, minute = (int(item) for item in at.split(":", 1))
        return {
            "run_id": run.run_id,
            "event_id": event_id,
            "world_day": world_day,
            "world_minute": hour * 60 + minute,
            "event_kind": str(event.get("visibility", "public")),
            "source_label": str(event.get("sourceLabel", "")),
            "summary": str(event.get("summary", "")),
            "topic_ids": list(event.get("topicIds", [])),
            "visible_actor_ids": list(event.get("visibleActorIds", [])),
            "neutral_payload": deepcopy(dict(event)),
        }

    def _state_rows(self, run: Run) -> builtins.list[dict[str, Any]]:
        data = serialize_run(run)
        rows: builtins.list[dict[str, Any]] = []

        def append(section: str, item_key: Any, value: Any) -> None:
            rows.append(
                {
                    "run_id": run.run_id,
                    "section": section,
                    "item_key": _json_key(item_key) if not isinstance(item_key, str) else item_key,
                    "value": _json_value(value),
                }
            )

        append("meta", "clock", data["clock"])
        # ``run_state_items.value`` is intentionally NOT NULL.  Missing
        # recovery markers mean ``None`` and are represented by absence of
        # their row rather than a nullable JSON value.
        if data.get("pendingDayEnd") is not None:
            append("meta", "pendingDayEnd", data["pendingDayEnd"])
        if data.get("pendingChapterEventId") is not None:
            append("meta", "pendingChapterEventId", data["pendingChapterEventId"])
        for section in ("conversations",):
            for item in data[section]:
                append(section, str(item["conversationId"]), item)
        for section in ("goals", "memories", "invitations", "joinRequests"):
            for item_key, value in dict(data.get(section, {})).items():
                append(section, str(item_key), value)
        for section in (
            "messages",
            "segments",
            "conversationDrafts",
            "conversationRoundStates",
            "idleCounts",
        ):
            for item_key, value in dict(data.get(section, {})).items():
                append(section, str(item_key), value)
        for section in ("relationships", "consolidationStatus", "chapterAgendaStances"):
            for item in data.get(section, []):
                if section == "relationships":
                    key = [item["fromActorId"], item["toActorId"]]
                    value = item.get("value", {})
                elif section == "consolidationStatus":
                    key = [item["conversationId"], item["npcId"]]
                    value = item.get("value", {})
                else:
                    key = [item["agendaId"], item["npcId"]]
                    value = item.get("stance", "unknown")
                append(section, key, value)
        for index, value in enumerate(data.get("memoryLinks", [])):
            append("memoryLinks", str(index), value)
        for index, value in enumerate(data.get("memoryCache", [])):
            append("memoryCache", str(index), value)
        for actor_id, stance in dict(data.get("chapterActorStances", {})).items():
            append("chapterActorStances", str(actor_id), stance)
        for event_id in data.get("firedEventIds", []):
            append("firedEventIds", str(event_id), True)
        return rows

    async def _replace_state_items(self, session: Any, run: Run, models: _DbModels) -> None:
        await session.execute(
            models.state_items.delete().where(models.state_items.c.run_id == run.run_id)
        )
        rows = self._state_rows(run)
        if rows:
            await session.execute(models.state_items.insert(), rows)


__all__ = ["SQLAlchemyRunRepository"]
