"""Synchronize immutable YAML scenario definitions into relational tables."""

from __future__ import annotations

from typing import Any

from ..scenario.models import ScenarioRegistry
from .models import Actor, Agenda, GoalDefinition, NpcProfile, Topic


async def sync_scenario(session_factory: Any, registry: ScenarioRegistry) -> None:
    """Upsert the small immutable scenario catalog after Alembic has run.

    Alembic owns table creation.  This function only copies validated YAML
    records so runtime rows can use real foreign keys.
    """

    async with session_factory() as session:
        async with session.begin():
            for actor in registry.actors.values():
                await session.merge(
                    Actor(
                        actor_id=actor.actor_id,
                        kind=actor.kind,
                        name=actor.name,
                        role=actor.role,
                        public_background=actor.public_background,
                        public_impression=list(actor.public_impression),
                    )
                )
            for persona in registry.npc_personas.values():
                await session.merge(
                    NpcProfile(
                        actor_id=persona.actor_id,
                        persona_summary=persona.persona_summary,
                        traits=list(persona.traits),
                        values=list(persona.values),
                        social_style={
                            "initiative": persona.initiative,
                            "directness": persona.directness,
                            "openness": persona.openness,
                            "conflictStyle": persona.conflict_style,
                        },
                        speech_style={
                            "tone": persona.speech_tone,
                            "length": persona.speech_length,
                            "habits": list(persona.speech_habits),
                        },
                        boundaries=list(persona.boundaries),
                        core_secrets=list(persona.core_secrets),
                    )
                )
            for topic in registry.topics.values():
                await session.merge(
                    Topic(
                        topic_id=topic.topic_id,
                        name=topic.name,
                        aliases=list(topic.aliases),
                    )
                )
            for goal in registry.goals.values():
                await session.merge(
                    GoalDefinition(
                        goal_id=goal.goal_id,
                        owner_npc_id=goal.owner_npc_id,
                        horizon=goal.horizon,
                        disclosure=goal.disclosure,
                        description=goal.description,
                        importance=goal.importance,
                        target_actor_ids=list(goal.target_actor_ids),
                        topic_ids=list(goal.topic_ids),
                        initial_status=goal.status,
                    )
                )
            for agenda in registry.agendas.values():
                await session.merge(
                    Agenda(
                        agenda_id=agenda.agenda_id,
                        chapter_id=registry.chapter_id,
                        owner_npc_id=agenda.owner_npc_id,
                        public_goal_id=agenda.public_goal_id,
                        title=agenda.title,
                        public_summary=agenda.public_summary,
                    )
                )


__all__ = ["sync_scenario"]
