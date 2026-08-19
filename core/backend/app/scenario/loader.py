"""Strict loader and cross-reference validator for the eight scenario YAMLs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .models import (
    ActorDefinition,
    AgendaDefinition,
    GoalDefinition,
    MemoryDefinition,
    NpcPersonaDefinition,
    RelationshipDefinition,
    ScenarioRegistry,
    TopicDefinition,
    WorldEventDefinition,
    freeze_value,
)

SCENARIO_FILES = (
    "NPC_PERSONAS.yaml",
    "PLAYER_PROFILE.yaml",
    "INITIAL_TOPICS.yaml",
    "INITIAL_GOALS.yaml",
    "INITIAL_RELATIONSHIPS.yaml",
    "INITIAL_MEMORIES.yaml",
    "WORLD_EVENTS_DAY1_7.yaml",
    "CHAPTER_AGENDAS.yaml",
)

_DAY_TIME_RE = re.compile(r"^Day(?P<day>[1-7])\s+(?P<hour>\d{2}):(?P<minute>\d{2})$")
_CLOCK_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})$")


class ScenarioValidationError(ValueError):
    """Raised when a scenario file or cross-reference is invalid."""

    def __init__(self, message: str, *, file: str | None = None, field: str | None = None) -> None:
        self.file = file
        self.field = field
        location = ""
        if file:
            location += file
        if field:
            location += f".{field}" if location else field
        super().__init__(f"{location}: {message}" if location else message)


def _mapping(value: Any, *, file: str, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioValidationError("must be a mapping", file=file, field=field)
    return value


def _list(value: Any, *, file: str, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScenarioValidationError("must be a list", file=file, field=field)
    return value


def _str(value: Any, *, file: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError("must be a non-empty string", file=file, field=field)
    return value


def _int(value: Any, *, file: str, field: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioValidationError("must be an integer", file=file, field=field)
    if minimum is not None and value < minimum:
        raise ScenarioValidationError(f"must be >= {minimum}", file=file, field=field)
    if maximum is not None and value > maximum:
        raise ScenarioValidationError(f"must be <= {maximum}", file=file, field=field)
    return value


def _enum(value: Any, allowed: set[str], *, file: str, field: str) -> str:
    parsed = _str(value, file=file, field=field)
    if parsed not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ScenarioValidationError(f"must be one of: {choices}", file=file, field=field)
    return parsed


def _string_list(value: Any, *, file: str, field: str, unique: bool = True) -> tuple[str, ...]:
    values = _list(value, file=file, field=field)
    parsed = tuple(_str(item, file=file, field=f"{field}[{index}]") for index, item in enumerate(values))
    if unique and len(set(parsed)) != len(parsed):
        raise ScenarioValidationError("must not contain duplicate IDs", file=file, field=field)
    return parsed


def _parse_day_time(value: Any, *, file: str, field: str) -> tuple[int, int, int]:
    text = _str(value, file=file, field=field)
    match = _DAY_TIME_RE.fullmatch(text)
    if match is None:
        raise ScenarioValidationError("must use the format DayN HH:MM", file=file, field=field)
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ScenarioValidationError("contains an invalid clock time", file=file, field=field)
    return day, hour, minute


def _parse_clock(value: Any, *, file: str, field: str) -> tuple[int, int]:
    text = _str(value, file=file, field=field)
    match = _CLOCK_RE.fullmatch(text)
    if match is None:
        raise ScenarioValidationError("must use the format HH:MM", file=file, field=field)
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ScenarioValidationError("contains an invalid clock time", file=file, field=field)
    return hour, minute


def _root(data: Any, *, file: str) -> Mapping[str, Any]:
    root = _mapping(data, file=file, field="root")
    if "status" in root:
        _enum(root.get("status"), {"confirmed"}, file=file, field="status")
    return root


class ScenarioLoader:
    """Load and validate the canonical scenario directory."""

    def __init__(self, scenario_dir: str | Path) -> None:
        self.scenario_dir = Path(scenario_dir)

    def _read(self, filename: str) -> Mapping[str, Any]:
        path = self.scenario_dir / filename
        if not path.is_file():
            raise ScenarioValidationError("file does not exist", file=filename)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ScenarioValidationError(f"invalid YAML: {exc}", file=filename) from exc
        return _root(data, file=filename)

    def load(self) -> ScenarioRegistry:
        files = {filename: self._read(filename) for filename in SCENARIO_FILES}
        actors, npcs, npc_personas = self._load_actors(
            files["NPC_PERSONAS.yaml"], files["PLAYER_PROFILE.yaml"]
        )
        topics = self._load_topics(files["INITIAL_TOPICS.yaml"])
        goals = self._load_goals(files["INITIAL_GOALS.yaml"], actors, topics)
        relationships = self._load_relationships(files["INITIAL_RELATIONSHIPS.yaml"], actors)
        memories = self._load_memories(files["INITIAL_MEMORIES.yaml"], actors, topics)
        self._validate_relationship_memories(relationships, memories)
        chapter, events = self._load_events(files["WORLD_EVENTS_DAY1_7.yaml"], actors, topics)
        agendas = self._load_agendas(files["CHAPTER_AGENDAS.yaml"], actors, goals, chapter["chapter_id"])

        return ScenarioRegistry(
            actors=actors,
            npcs=npcs,
            npc_personas=npc_personas,
            goals=goals,
            topics=topics,
            relationships=relationships,
            memories=memories,
            agendas=agendas,
            events=events,
            chapter_id=chapter["chapter_id"],
            chapter_name=chapter["chapter_name"],
            start_day=chapter["start_day"],
            start_hour=chapter["start_hour"],
            start_minute=chapter["start_minute"],
            end_day=chapter["end_day"],
            end_hour=chapter["end_hour"],
            end_minute=chapter["end_minute"],
            active_start_minutes=chapter["active_start_minutes"],
            active_end_minutes=chapter["active_end_minutes"],
            virtual_hours_per_real_minute=chapter["virtual_hours_per_real_minute"],
        )

    @staticmethod
    def _validate_relationship_memories(
        relationships: Mapping[tuple[str, str], RelationshipDefinition],
        memories: Mapping[str, MemoryDefinition],
    ) -> None:
        """Require a private opening belief for every directed relationship.

        Relationship numbers remain authoritative state.  This invariant only
        guarantees that the owning NPC can also recall its subjective view of
        the target through the Memory tool.
        """

        covered = {
            (memory.owner_npc_id, target_actor_id)
            for memory in memories.values()
            if memory.source == "scenario_seed"
            for target_actor_id in memory.actor_ids
        }
        missing = sorted(set(relationships) - covered)
        if missing:
            source, target = missing[0]
            raise ScenarioValidationError(
                f"missing scenario_seed memory for directed relationship {source}->{target}",
                file="INITIAL_MEMORIES.yaml",
                field="memories",
            )

    @staticmethod
    def _load_actors(
        npc_data: Mapping[str, Any], player_data: Mapping[str, Any]
    ) -> tuple[
        Mapping[str, ActorDefinition],
        tuple[ActorDefinition, ...],
        Mapping[str, NpcPersonaDefinition],
    ]:
        npc_file = "NPC_PERSONAS.yaml"
        npc_rows = _list(npc_data.get("npcs"), file=npc_file, field="npcs")
        actors: dict[str, ActorDefinition] = {}
        npcs: list[ActorDefinition] = []
        npc_personas: dict[str, NpcPersonaDefinition] = {}
        for index, raw in enumerate(npc_rows):
            row = _mapping(raw, file=npc_file, field=f"npcs[{index}]")
            actor_id = _str(row.get("npcId"), file=npc_file, field=f"npcs[{index}].npcId")
            if actor_id in actors:
                raise ScenarioValidationError("duplicate actor ID", file=npc_file, field=f"npcs[{index}].npcId")
            identity = _mapping(row.get("identity"), file=npc_file, field=f"npcs[{index}].identity")
            actor = ActorDefinition(
                actor_id=actor_id,
                kind="npc",
                name=_str(identity.get("name"), file=npc_file, field=f"npcs[{index}].identity.name"),
                role=_str(identity.get("role"), file=npc_file, field=f"npcs[{index}].identity.role"),
                public_background=_str(
                    identity.get("publicBackground"),
                    file=npc_file,
                    field=f"npcs[{index}].identity.publicBackground",
                ),
            )
            social_style = _mapping(
                row.get("socialStyle"), file=npc_file, field=f"npcs[{index}].socialStyle"
            )
            parsed_social_style: dict[str, str] = {}
            for key, allowed in (
                ("initiative", {"low", "medium", "high"}),
                ("directness", {"low", "medium", "high"}),
                ("openness", {"low", "medium", "high"}),
                ("conflictStyle", {"avoidant", "balanced", "confrontational"}),
            ):
                parsed_social_style[key] = _enum(
                    social_style.get(key),
                    allowed,
                    file=npc_file,
                    field=f"npcs[{index}].socialStyle.{key}",
                )
            speech_style = _mapping(row.get("speechStyle"), file=npc_file, field=f"npcs[{index}].speechStyle")
            persona = NpcPersonaDefinition(
                actor_id=actor_id,
                persona_summary=_str(
                    row.get("personaSummary"),
                    file=npc_file,
                    field=f"npcs[{index}].personaSummary",
                ),
                traits=_string_list(
                    row.get("traits"), file=npc_file, field=f"npcs[{index}].traits", unique=False
                ),
                values=_string_list(
                    row.get("values"), file=npc_file, field=f"npcs[{index}].values", unique=False
                ),
                initiative=parsed_social_style["initiative"],
                directness=parsed_social_style["directness"],
                openness=parsed_social_style["openness"],
                conflict_style=parsed_social_style["conflictStyle"],
                speech_tone=_str(
                    speech_style.get("tone"),
                    file=npc_file,
                    field=f"npcs[{index}].speechStyle.tone",
                ),
                speech_length=_enum(
                    speech_style.get("length"),
                    {"short", "medium", "long"},
                    file=npc_file,
                    field=f"npcs[{index}].speechStyle.length",
                ),
                speech_habits=_string_list(
                    speech_style.get("habits"),
                    file=npc_file,
                    field=f"npcs[{index}].speechStyle.habits",
                    unique=False,
                ),
                boundaries=_string_list(
                    row.get("boundaries"),
                    file=npc_file,
                    field=f"npcs[{index}].boundaries",
                    unique=False,
                ),
                core_secrets=_string_list(
                    row.get("coreSecrets"),
                    file=npc_file,
                    field=f"npcs[{index}].coreSecrets",
                    unique=False,
                ),
            )
            npcs.append(actor)
            actors[actor_id] = actor
            npc_personas[actor_id] = persona

        player_file = "PLAYER_PROFILE.yaml"
        player = _mapping(player_data.get("player"), file=player_file, field="player")
        player_id = _str(player.get("actorId"), file=player_file, field="player.actorId")
        if player_id in actors:
            raise ScenarioValidationError("duplicate actor ID", file=player_file, field="player.actorId")
        identity = _mapping(player.get("identity"), file=player_file, field="player.identity")
        player_actor = ActorDefinition(
            actor_id=player_id,
            kind="player",
            name="玩家",
            role=_str(identity.get("role"), file=player_file, field="player.identity.role"),
            public_background=_str(
                identity.get("publicBackground"), file=player_file, field="player.identity.publicBackground"
            ),
            public_impression=_string_list(
                player.get("publicImpression"), file=player_file, field="player.publicImpression"
            ),
        )
        if _enum(player.get("control"), {"human"}, file=player_file, field="player.control") != "human":
            raise AssertionError("unreachable")
        gameplay = _mapping(player.get("gameplayBoundary"), file=player_file, field="player.gameplayBoundary")
        for key in ("freeTextInput", "fixedPersonaEnforced", "backgroundWorkIsFlavorOnly", "hiddenBackstoryPreset"):
            if not isinstance(gameplay.get(key), bool):
                raise ScenarioValidationError(
                    "must be a boolean", file=player_file, field=f"player.gameplayBoundary.{key}"
                )
        _enum(
            gameplay.get("backstoryMode"),
            {"emergent_from_player_dialogue"},
            file=player_file,
            field="player.gameplayBoundary.backstoryMode",
        )
        actors[player_id] = player_actor
        return (
            MappingProxyType(dict(actors)),
            tuple(npcs),
            MappingProxyType(dict(npc_personas)),
        )

    @staticmethod
    def _load_topics(data: Mapping[str, Any]) -> Mapping[str, TopicDefinition]:
        filename = "INITIAL_TOPICS.yaml"
        rows = _list(data.get("topics"), file=filename, field="topics")
        topics: dict[str, TopicDefinition] = {}
        for index, raw in enumerate(rows):
            row = _mapping(raw, file=filename, field=f"topics[{index}]")
            topic_id = _str(row.get("topicId"), file=filename, field=f"topics[{index}].topicId")
            if topic_id in topics:
                raise ScenarioValidationError("duplicate topic ID", file=filename, field=f"topics[{index}].topicId")
            topics[topic_id] = TopicDefinition(
                topic_id=topic_id,
                name=_str(row.get("name"), file=filename, field=f"topics[{index}].name"),
                aliases=_string_list(row.get("aliases"), file=filename, field=f"topics[{index}].aliases"),
            )
        return MappingProxyType(dict(topics))

    @staticmethod
    def _load_goals(
        data: Mapping[str, Any], actors: Mapping[str, ActorDefinition], topics: Mapping[str, TopicDefinition]
    ) -> Mapping[str, GoalDefinition]:
        filename = "INITIAL_GOALS.yaml"
        rows = _list(data.get("goals"), file=filename, field="goals")
        goals: dict[str, GoalDefinition] = {}
        for index, raw in enumerate(rows):
            row = _mapping(raw, file=filename, field=f"goals[{index}]")
            field = f"goals[{index}]"
            goal_id = _str(row.get("goalId"), file=filename, field=f"{field}.goalId")
            if goal_id in goals:
                raise ScenarioValidationError("duplicate goal ID", file=filename, field=f"{field}.goalId")
            owner = _str(row.get("ownerNpcId"), file=filename, field=f"{field}.ownerNpcId")
            if owner not in actors or actors[owner].kind != "npc":
                raise ScenarioValidationError("must reference an existing NPC", file=filename, field=f"{field}.ownerNpcId")
            target_ids = _string_list(row.get("targetActorIds"), file=filename, field=f"{field}.targetActorIds")
            for target_index, target_id in enumerate(target_ids):
                if target_id not in actors:
                    raise ScenarioValidationError("unknown actor reference", file=filename, field=f"{field}.targetActorIds[{target_index}]")
            topic_ids = _string_list(row.get("topicIds"), file=filename, field=f"{field}.topicIds")
            for topic_index, topic_id in enumerate(topic_ids):
                if topic_id not in topics:
                    raise ScenarioValidationError("unknown topic reference", file=filename, field=f"{field}.topicIds[{topic_index}]")
            goals[goal_id] = GoalDefinition(
                goal_id=goal_id,
                owner_npc_id=owner,
                horizon=_enum(row.get("horizon"), {"long_term", "short_term"}, file=filename, field=f"{field}.horizon"),
                disclosure=_enum(row.get("disclosure"), {"shareable", "guarded"}, file=filename, field=f"{field}.disclosure"),
                description=_str(row.get("description"), file=filename, field=f"{field}.description"),
                target_actor_ids=target_ids,
                topic_ids=topic_ids,
                importance=_int(row.get("importance"), file=filename, field=f"{field}.importance", minimum=1, maximum=5),
                status=_enum(row.get("status"), {"active", "blocked", "achieved", "abandoned"}, file=filename, field=f"{field}.status"),
            )
        return MappingProxyType(dict(goals))

    @staticmethod
    def _load_relationships(
        data: Mapping[str, Any], actors: Mapping[str, ActorDefinition]
    ) -> Mapping[tuple[str, str], RelationshipDefinition]:
        filename = "INITIAL_RELATIONSHIPS.yaml"
        rows = _list(data.get("relationships"), file=filename, field="relationships")
        relationships: dict[tuple[str, str], RelationshipDefinition] = {}
        for index, raw in enumerate(rows):
            row = _mapping(raw, file=filename, field=f"relationships[{index}]")
            field = f"relationships[{index}]"
            from_id = _str(row.get("fromActorId"), file=filename, field=f"{field}.fromActorId")
            to_id = _str(row.get("toActorId"), file=filename, field=f"{field}.toActorId")
            if from_id not in actors:
                raise ScenarioValidationError("unknown actor reference", file=filename, field=f"{field}.fromActorId")
            if to_id not in actors:
                raise ScenarioValidationError("unknown actor reference", file=filename, field=f"{field}.toActorId")
            if from_id == to_id:
                raise ScenarioValidationError("relationship cannot point to itself", file=filename, field=field)
            key = (from_id, to_id)
            if key in relationships:
                raise ScenarioValidationError("duplicate relationship edge", file=filename, field=field)
            relationships[key] = RelationshipDefinition(
                from_actor_id=from_id,
                to_actor_id=to_id,
                social_roles=_string_list(row.get("socialRoles"), file=filename, field=f"{field}.socialRoles"),
                familiarity=_int(row.get("familiarity"), file=filename, field=f"{field}.familiarity", minimum=0, maximum=3),
                trust=_int(row.get("trust"), file=filename, field=f"{field}.trust", minimum=-2, maximum=2),
                affinity=_int(row.get("affinity"), file=filename, field=f"{field}.affinity", minimum=-2, maximum=2),
                tension=_int(row.get("tension"), file=filename, field=f"{field}.tension", minimum=0, maximum=2),
                interaction_count=_int(row.get("interactionCount"), file=filename, field=f"{field}.interactionCount", minimum=0),
            )
        return MappingProxyType(dict(relationships))

    @staticmethod
    def _load_memories(
        data: Mapping[str, Any], actors: Mapping[str, ActorDefinition], topics: Mapping[str, TopicDefinition]
    ) -> Mapping[str, MemoryDefinition]:
        filename = "INITIAL_MEMORIES.yaml"
        rows = _list(data.get("memories"), file=filename, field="memories")
        memories: dict[str, MemoryDefinition] = {}
        for index, raw in enumerate(rows):
            row = _mapping(raw, file=filename, field=f"memories[{index}]")
            field = f"memories[{index}]"
            memory_id = _str(row.get("memoryId"), file=filename, field=f"{field}.memoryId")
            if memory_id in memories:
                raise ScenarioValidationError("duplicate memory ID", file=filename, field=f"{field}.memoryId")
            owner = _str(row.get("ownerNpcId"), file=filename, field=f"{field}.ownerNpcId")
            if owner not in actors or actors[owner].kind != "npc":
                raise ScenarioValidationError("must reference an existing NPC", file=filename, field=f"{field}.ownerNpcId")
            actor_ids = _string_list(row.get("actorIds"), file=filename, field=f"{field}.actorIds")
            for actor_index, actor_id in enumerate(actor_ids):
                if actor_id not in actors:
                    raise ScenarioValidationError("unknown actor reference", file=filename, field=f"{field}.actorIds[{actor_index}]")
            topic_ids = _string_list(row.get("topicIds"), file=filename, field=f"{field}.topicIds")
            for topic_index, topic_id in enumerate(topic_ids):
                if topic_id not in topics:
                    raise ScenarioValidationError("unknown topic reference", file=filename, field=f"{field}.topicIds[{topic_index}]")
            memories[memory_id] = MemoryDefinition(
                memory_id=memory_id,
                owner_npc_id=owner,
                memory_type=_enum(row.get("type"), {"event", "belief", "commitment", "relationship_change", "goal_change", "reflection"}, file=filename, field=f"{field}.type"),
                content=_str(row.get("content"), file=filename, field=f"{field}.content"),
                actor_ids=actor_ids,
                topic_ids=topic_ids,
                importance=_int(row.get("importance"), file=filename, field=f"{field}.importance", minimum=1, maximum=5),
                confidence=_enum(row.get("confidence"), {"low", "medium", "high"}, file=filename, field=f"{field}.confidence"),
                source=_str(row.get("source"), file=filename, field=f"{field}.source"),
                evidence_message_ids=_string_list(row.get("evidenceMessageIds"), file=filename, field=f"{field}.evidenceMessageIds"),
            )
        return MappingProxyType(dict(memories))

    @staticmethod
    def _load_events(
        data: Mapping[str, Any], actors: Mapping[str, ActorDefinition], topics: Mapping[str, TopicDefinition]
    ) -> tuple[dict[str, Any], tuple[WorldEventDefinition, ...]]:
        filename = "WORLD_EVENTS_DAY1_7.yaml"
        chapter = _mapping(data.get("chapter"), file=filename, field="chapter")
        chapter_id = _str(chapter.get("chapterId"), file=filename, field="chapter.chapterId")
        chapter_name = _str(chapter.get("name"), file=filename, field="chapter.name")
        start_day, start_hour, start_minute = _parse_day_time(chapter.get("startsAt"), file=filename, field="chapter.startsAt")
        end_day, end_hour, end_minute = _parse_day_time(chapter.get("endsAt"), file=filename, field="chapter.endsAt")
        _enum(
            chapter.get("overnightMode"),
            {"jump_to_next_day_0800"},
            file=filename,
            field="chapter.overnightMode",
        )
        if not isinstance(chapter.get("chapterEndsAfterResolution"), bool):
            raise ScenarioValidationError(
                "must be a boolean", file=filename, field="chapter.chapterEndsAfterResolution"
            )
        external_policy = _mapping(
            data.get("externalSourcePolicy"), file=filename, field="externalSourcePolicy"
        )
        for key in ("createsActorNodes", "canBeConversationTarget"):
            if not isinstance(external_policy.get(key), bool):
                raise ScenarioValidationError(
                    "must be a boolean", file=filename, field=f"externalSourcePolicy.{key}"
                )
        active_window = _str(chapter.get("activeDayWindow"), file=filename, field="chapter.activeDayWindow")
        window_parts = active_window.split("-")
        if len(window_parts) != 2:
            raise ScenarioValidationError("must use HH:MM-HH:MM", file=filename, field="chapter.activeDayWindow")
        start_h, start_m = _parse_clock(window_parts[0], file=filename, field="chapter.activeDayWindow.start")
        end_h, end_m = _parse_clock(window_parts[1], file=filename, field="chapter.activeDayWindow.end")
        active_start = start_h * 60 + start_m
        active_end = end_h * 60 + end_m
        if active_start >= active_end:
            raise ScenarioValidationError("active window must be increasing", file=filename, field="chapter.activeDayWindow")
        events_rows = _list(data.get("events"), file=filename, field="events")
        events: list[WorldEventDefinition] = []
        event_ids: set[str] = set()
        for index, raw in enumerate(events_rows):
            row = _mapping(raw, file=filename, field=f"events[{index}]")
            field = f"events[{index}]"
            event_id = _str(row.get("eventId"), file=filename, field=f"{field}.eventId")
            if event_id in event_ids:
                raise ScenarioValidationError("duplicate event ID", file=filename, field=f"{field}.eventId")
            event_ids.add(event_id)
            day = _int(row.get("worldDay"), file=filename, field=f"{field}.worldDay", minimum=1, maximum=7)
            at = _str(row.get("at"), file=filename, field=f"{field}.at")
            _parse_clock(at, file=filename, field=f"{field}.at")
            visibility = _enum(row.get("visibility"), {"public", "observed"}, file=filename, field=f"{field}.visibility")
            _enum(
                row.get("reactionPolicy"),
                {"dynamic", "branch_evaluation"},
                file=filename,
                field=f"{field}.reactionPolicy",
            )
            visible = row.get("visibleActorIds")
            if visible == "dynamic_by_event_radius":
                visible_ids: tuple[str, ...] | str = visible
            else:
                visible_ids = _string_list(visible, file=filename, field=f"{field}.visibleActorIds")
                for actor_index, actor_id in enumerate(visible_ids):
                    if actor_id not in actors:
                        raise ScenarioValidationError("unknown actor reference", file=filename, field=f"{field}.visibleActorIds[{actor_index}]")
            topic_ids = _string_list(row.get("topicIds"), file=filename, field=f"{field}.topicIds")
            for topic_index, topic_id in enumerate(topic_ids):
                if topic_id not in topics:
                    raise ScenarioValidationError("unknown topic reference", file=filename, field=f"{field}.topicIds[{topic_index}]")
            raw_state_changes = row.get("currentWorldStateChanges", [])
            state_changes: list[tuple[str, Any]] = []
            for change_index, raw_change in enumerate(
                _list(raw_state_changes, file=filename, field=f"{field}.currentWorldStateChanges")
            ):
                change = _mapping(
                    raw_change,
                    file=filename,
                    field=f"{field}.currentWorldStateChanges[{change_index}]",
                )
                key = _str(
                    change.get("key"),
                    file=filename,
                    field=f"{field}.currentWorldStateChanges[{change_index}].key",
                )
                if "value" not in change:
                    raise ScenarioValidationError(
                        "must contain value",
                        file=filename,
                        field=f"{field}.currentWorldStateChanges[{change_index}].value",
                    )
                state_changes.append((key, freeze_value(change["value"])))
            trigger_condition = row.get("triggerCondition")
            if trigger_condition is not None:
                trigger_condition = _str(
                    trigger_condition,
                    file=filename,
                    field=f"{field}.triggerCondition",
                )
            events.append(
                WorldEventDefinition(
                    event_id=event_id,
                    world_day=day,
                    at=at,
                    visibility=visibility,
                    visible_actor_ids=visible_ids,
                    source_label=_str(row.get("sourceLabel"), file=filename, field=f"{field}.sourceLabel"),
                    summary=_str(row.get("summary"), file=filename, field=f"{field}.summary"),
                    topic_ids=topic_ids,
                    current_world_state_changes=tuple(state_changes),
                    trigger_condition=trigger_condition,
                )
            )
        if (start_day, start_hour, start_minute) != (1, 9, 0):
            raise ScenarioValidationError("first phase must start at Day1 09:00", file=filename, field="chapter.startsAt")
        if (end_day, end_hour, end_minute) != (7, 18, 0):
            raise ScenarioValidationError("first phase must end at Day7 18:00", file=filename, field="chapter.endsAt")
        return (
            {
                "chapter_id": chapter_id,
                "chapter_name": chapter_name,
                "start_day": start_day,
                "start_hour": start_hour,
                "start_minute": start_minute,
                "end_day": end_day,
                "end_hour": end_hour,
                "end_minute": end_minute,
                "active_start_minutes": active_start,
                "active_end_minutes": active_end,
                "virtual_hours_per_real_minute": _int(chapter.get("virtualHoursPerRealMinute"), file=filename, field="chapter.virtualHoursPerRealMinute", minimum=1),
            },
            tuple(events),
        )

    @staticmethod
    def _load_agendas(
        data: Mapping[str, Any],
        actors: Mapping[str, ActorDefinition],
        goals: Mapping[str, GoalDefinition],
        chapter_id: str,
    ) -> Mapping[str, AgendaDefinition]:
        filename = "CHAPTER_AGENDAS.yaml"
        file_chapter_id = _str(data.get("chapterId"), file=filename, field="chapterId")
        if file_chapter_id != chapter_id:
            raise ScenarioValidationError("does not match world chapterId", file=filename, field="chapterId")
        resolution = _mapping(data.get("resolution"), file=filename, field="resolution")
        resolution_statuses = _string_list(
            resolution.get("statuses"), file=filename, field="resolution.statuses"
        )
        for status in resolution_statuses:
            if status not in {"core_adopted", "partially_adopted", "not_adopted"}:
                raise ScenarioValidationError(
                    "contains an unsupported resolution status",
                    file=filename,
                    field="resolution.statuses",
                )
        rows = _list(data.get("agendas"), file=filename, field="agendas")
        agendas: dict[str, AgendaDefinition] = {}
        for index, raw in enumerate(rows):
            row = _mapping(raw, file=filename, field=f"agendas[{index}]")
            field = f"agendas[{index}]"
            agenda_id = _str(row.get("agendaId"), file=filename, field=f"{field}.agendaId")
            if agenda_id in agendas:
                raise ScenarioValidationError("duplicate agenda ID", file=filename, field=f"{field}.agendaId")
            owner = _str(row.get("ownerNpcId"), file=filename, field=f"{field}.ownerNpcId")
            if owner not in actors or actors[owner].kind != "npc":
                raise ScenarioValidationError("must reference an existing NPC", file=filename, field=f"{field}.ownerNpcId")
            goal_id = _str(row.get("publicGoalId"), file=filename, field=f"{field}.publicGoalId")
            goal = goals.get(goal_id)
            if goal is None:
                raise ScenarioValidationError("must reference an existing Goal", file=filename, field=f"{field}.publicGoalId")
            if goal.owner_npc_id != owner or goal.horizon != "long_term" or goal.disclosure != "shareable":
                raise ScenarioValidationError("must connect the owner's public long-term Goal", file=filename, field=f"{field}.publicGoalId")
            agendas[agenda_id] = AgendaDefinition(
                agenda_id=agenda_id,
                owner_npc_id=owner,
                public_goal_id=goal_id,
                title=_str(row.get("title"), file=filename, field=f"{field}.title"),
                public_summary=_str(row.get("publicSummary"), file=filename, field=f"{field}.publicSummary"),
            )
        return MappingProxyType(dict(agendas))


def load_scenario_registry(scenario_dir: str | Path) -> ScenarioRegistry:
    """Convenience function used by application startup and tests."""

    return ScenarioLoader(scenario_dir).load()


__all__ = ["SCENARIO_FILES", "ScenarioLoader", "ScenarioValidationError", "load_scenario_registry"]
