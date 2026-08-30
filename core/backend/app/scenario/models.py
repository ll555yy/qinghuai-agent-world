"""Immutable scenario data records.

The loader is the only code that turns YAML dictionaries into these records.
All mutable YAML collections are converted to tuples or read-only mappings
before a registry is exposed to a running world.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def freeze_value(value: Any) -> Any:
    """Recursively freeze YAML-shaped values for safe registry storage."""

    if isinstance(value, dict):
        return MappingProxyType({str(key): freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ActorDefinition:
    actor_id: str
    kind: str
    name: str
    role: str
    public_background: str
    public_impression: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NpcPersonaDefinition:
    actor_id: str
    persona_summary: str
    traits: tuple[str, ...]
    values: tuple[str, ...]
    initiative: str
    directness: str
    openness: str
    conflict_style: str
    speech_tone: str
    speech_length: str
    speech_habits: tuple[str, ...]
    boundaries: tuple[str, ...]
    core_secrets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpeechExampleDefinition:
    example_id: str
    npc_id: str
    situation: str
    intended_move: str
    reply: str


@dataclass(frozen=True, slots=True)
class TopicDefinition:
    topic_id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoalDefinition:
    goal_id: str
    owner_npc_id: str
    horizon: str
    disclosure: str
    description: str
    target_actor_ids: tuple[str, ...]
    topic_ids: tuple[str, ...]
    importance: int
    status: str


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    from_actor_id: str
    to_actor_id: str
    social_roles: tuple[str, ...]
    familiarity: int
    trust: int
    affinity: int
    tension: int
    interaction_count: int


@dataclass(frozen=True, slots=True)
class MemoryDefinition:
    memory_id: str
    owner_npc_id: str
    memory_type: str
    content: str
    actor_ids: tuple[str, ...]
    topic_ids: tuple[str, ...]
    importance: int
    confidence: str
    source: str
    evidence_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgendaDefinition:
    agenda_id: str
    owner_npc_id: str
    public_goal_id: str
    title: str
    public_summary: str


@dataclass(frozen=True, slots=True)
class WorldEventDefinition:
    event_id: str
    world_day: int
    at: str
    visibility: str
    visible_actor_ids: tuple[str, ...] | str
    source_label: str
    summary: str
    topic_ids: tuple[str, ...]
    current_world_state_changes: tuple[tuple[str, Any], ...] = ()
    trigger_condition: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioRegistry:
    """The complete read-only scenario used by all in-memory Runs."""

    actors: Mapping[str, ActorDefinition]
    npcs: tuple[ActorDefinition, ...]
    npc_personas: Mapping[str, NpcPersonaDefinition]
    speech_examples: Mapping[str, SpeechExampleDefinition]
    goals: Mapping[str, GoalDefinition]
    topics: Mapping[str, TopicDefinition]
    relationships: Mapping[tuple[str, str], RelationshipDefinition]
    memories: Mapping[str, MemoryDefinition]
    agendas: Mapping[str, AgendaDefinition]
    events: tuple[WorldEventDefinition, ...]
    chapter_id: str
    chapter_name: str
    start_day: int
    start_hour: int
    start_minute: int
    end_day: int
    end_hour: int
    end_minute: int
    active_start_minutes: int
    active_end_minutes: int
    virtual_hours_per_real_minute: float
    real_seconds_per_virtual_minute: int

    @property
    def player(self) -> ActorDefinition:
        for actor in self.actors.values():
            if actor.kind == "player":
                return actor
        raise LookupError("Scenario has no player actor")

    @property
    def player_actor_id(self) -> str:
        return self.player.actor_id

    @property
    def public_agendas(self) -> tuple[AgendaDefinition, ...]:
        return tuple(self.agendas.values())

    def actor(self, actor_id: str) -> ActorDefinition | None:
        return self.actors.get(actor_id)

    def goal(self, goal_id: str) -> GoalDefinition | None:
        return self.goals.get(goal_id)

    def agenda(self, agenda_id: str) -> AgendaDefinition | None:
        return self.agendas.get(agenda_id)

    def public_actor(self, actor_id: str) -> dict[str, Any]:
        """Return only the fields allowed in a player-facing actor card."""

        actor = self.actors[actor_id]
        return {
            "actorId": actor.actor_id,
            "kind": actor.kind,
            "name": actor.name,
            "role": actor.role,
            "publicBackground": actor.public_background,
            "publicImpression": list(actor.public_impression),
        }

    def public_agenda(self, agenda_id: str) -> dict[str, str]:
        agenda = self.agendas[agenda_id]
        return {
            "agendaId": agenda.agenda_id,
            "ownerNpcId": agenda.owner_npc_id,
            "title": agenda.title,
            "publicSummary": agenda.public_summary,
        }

    def validate_copy_isolated(self) -> None:
        """A small runtime assertion useful to tests and diagnostics."""

        if type(self.actors) is not type(MappingProxyType({})):
            raise TypeError("ScenarioRegistry.actors must be read-only")

    def copy(self) -> ScenarioRegistry:
        """Return an isolated, mutable-container copy for tests and tooling.

        The registry used by a Run stays immutable; this explicit copy is the
        safe way for scenario editors or tests to experiment with replacements
        without mutating the authoritative mappings.
        """

        from copy import deepcopy

        return ScenarioRegistry(
            actors=deepcopy(dict(self.actors)),
            npcs=deepcopy(self.npcs),
            npc_personas=deepcopy(dict(self.npc_personas)),
            speech_examples=deepcopy(dict(self.speech_examples)),
            goals=deepcopy(dict(self.goals)),
            topics=deepcopy(dict(self.topics)),
            relationships=deepcopy(dict(self.relationships)),
            memories=deepcopy(dict(self.memories)),
            agendas=deepcopy(dict(self.agendas)),
            events=deepcopy(self.events),
            chapter_id=self.chapter_id,
            chapter_name=self.chapter_name,
            start_day=self.start_day,
            start_hour=self.start_hour,
            start_minute=self.start_minute,
            end_day=self.end_day,
            end_hour=self.end_hour,
            end_minute=self.end_minute,
            active_start_minutes=self.active_start_minutes,
            active_end_minutes=self.active_end_minutes,
            virtual_hours_per_real_minute=self.virtual_hours_per_real_minute,
            real_seconds_per_virtual_minute=self.real_seconds_per_virtual_minute,
        )

    def __deepcopy__(self, _memo: dict[int, Any]) -> ScenarioRegistry:
        return self.copy()
