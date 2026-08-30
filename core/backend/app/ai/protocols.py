"""Small structured contracts used by the playable world loop.

The model is deliberately only allowed to return semantic decisions.  Run and
message identifiers, timestamps, owners and relationship values are supplied
and validated by the backend.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .models import AIContractModel


class DailyActionDecision(AIContractModel):
    action: Literal["seek_chat", "wait"]
    goal_id: str | None = Field(default=None, alias="goalId")
    target_actor_id: str | None = Field(default=None, alias="targetActorId")
    intent: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> DailyActionDecision:
        if self.action == "seek_chat" and (not self.goal_id or not self.target_actor_id):
            raise ValueError("seek_chat requires goalId and targetActorId")
        if self.action == "wait" and (
            self.goal_id is not None
            or self.target_actor_id is not None
            or self.intent is not None
        ):
            raise ValueError("wait cannot include goalId, targetActorId or intent")
        return self


class InvitationDecision(AIContractModel):
    decision: Literal["accept", "refuse"]


class MemoryQuery(AIContractModel):
    query_text: str = Field(default="", alias="queryText")
    actor_ids: list[str] = Field(default_factory=list, alias="actorIds")
    topic_hints: list[str] = Field(default_factory=list, alias="topicHints")
    goal_ids: list[str] = Field(default_factory=list, alias="goalIds")
    limit: int = Field(default=8, ge=1, le=8)

    @model_validator(mode="after")
    def validate_query(self) -> MemoryQuery:
        if not (self.query_text.strip() or self.actor_ids or self.topic_hints or self.goal_ids):
            raise ValueError("memoryQuery requires at least one search criterion")
        return self


class GoalUpdate(AIContractModel):
    goal_id: str = Field(alias="goalId")
    new_status: Literal["active", "blocked", "achieved", "abandoned"] = Field(alias="newStatus")
    reason: str = ""
    evidence_message_ids: list[str] = Field(min_length=1, alias="evidenceMessageIds")


class RelationshipUpdate(AIContractModel):
    target_actor_id: str = Field(alias="targetActorId")
    dimension: Literal["trust", "affinity", "tension"]
    direction: Literal["increase", "decrease"]
    reason: str = ""
    evidence_message_ids: list[str] = Field(min_length=1, alias="evidenceMessageIds")


class PendingGoal(AIContractModel):
    description: str = Field(min_length=1)
    parent_goal_id: str | None = Field(default=None, alias="parentGoalId")
    target_actor_ids: list[str] = Field(default_factory=list, alias="targetActorIds")
    topic_hints: list[str] = Field(default_factory=list, alias="topicHints")
    importance: int = Field(default=2, ge=1, le=5)
    evidence_message_ids: list[str] = Field(min_length=1, alias="evidenceMessageIds")


class ChapterEffect(AIContractModel):
    kind: Literal["overall_stance", "zhou_authorization", "agenda_stance"]
    agenda_id: str | None = Field(default=None, alias="agendaId")
    value: str
    evidence_message_ids: list[str] = Field(default_factory=list, alias="evidenceMessageIds")

    @model_validator(mode="after")
    def validate_effect(self) -> ChapterEffect:
        stance_values = {"unknown", "support", "conditional", "oppose", "withdrawn"}
        authorization_values = {"none", "approved", "conditional", "rejected"}
        if self.kind == "zhou_authorization":
            if self.agenda_id is not None or self.value not in authorization_values:
                raise ValueError("zhou_authorization has an invalid shape")
        elif self.kind == "agenda_stance":
            if self.agenda_id is None or self.value not in stance_values:
                raise ValueError("agenda_stance requires agendaId and a stance value")
        elif self.agenda_id is not None or self.value not in stance_values:
            raise ValueError("overall_stance has an invalid shape")
        return self


class ChatDecision(AIContractModel):
    result: Literal["need_memory", "decided"]
    memory_query: MemoryQuery | None = Field(default=None, alias="memoryQuery")
    action: Literal["speak", "wait", "leave_chat"] | None = None
    response_desire: int = Field(default=0, ge=0, le=3, alias="responseDesire")
    target_actor_id: str | None = Field(default=None, alias="targetActorId")
    intent: str | None = None
    leave_chat_after_speaking: bool = Field(default=False, alias="leaveChatAfterSpeaking")
    goal_updates: list[GoalUpdate] = Field(default_factory=list, alias="goalUpdates")
    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list, alias="relationshipUpdates")
    pending_goal: PendingGoal | None = Field(default=None, alias="pendingGoal")
    chapter_effects: list[ChapterEffect] = Field(default_factory=list, alias="chapterEffects")

    @model_validator(mode="after")
    def validate_shape(self) -> ChatDecision:
        if self.result == "need_memory":
            if self.memory_query is None:
                raise ValueError("need_memory requires memoryQuery")
            if (
                self.action is not None
                or self.goal_updates
                or self.relationship_updates
                or self.pending_goal
                or self.chapter_effects
                or self.target_actor_id is not None
                or self.intent is not None
                or self.leave_chat_after_speaking
            ):
                raise ValueError("need_memory cannot include an action or state changes")
        else:
            if self.action is None:
                raise ValueError("decided requires action")
            if self.memory_query is not None:
                raise ValueError("decided cannot include memoryQuery")
            if self.action != "speak" and (
                self.response_desire != 0 or self.leave_chat_after_speaking
            ):
                raise ValueError("only speak can request a response or leave after speaking")
        return self


class SpeechGeneration(AIContractModel):
    text: str = Field(min_length=1, max_length=300)
    addressed_actor_ids: list[str] = Field(
        default_factory=list,
        alias="addressedActorIds",
    )

    @field_validator("addressed_actor_ids")
    @classmethod
    def deduplicate_addressed_actor_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class SegmentSummary(AIContractModel):
    claims: list[str] = Field(default_factory=list)
    commitments: list[str] = Field(default_factory=list)
    revealed_facts: list[str] = Field(default_factory=list, alias="revealedFacts")
    open_questions: list[str] = Field(default_factory=list, alias="openQuestions")
    actor_ids: list[str] = Field(default_factory=list, alias="actorIds")
    topic_hints: list[str] = Field(default_factory=list, alias="topicHints")


class MemoryExtraction(AIContractModel):
    ref: str
    type: Literal["event", "belief", "commitment", "relationship_change", "goal_change", "reflection"] = Field(alias="type")
    content: str = Field(min_length=1)
    actor_ids: list[str] = Field(default_factory=list, alias="actorIds")
    topic_hints: list[str] = Field(default_factory=list, alias="topicHints")
    importance: int = Field(default=2, ge=1, le=5)
    confidence: Literal["low", "medium", "high"] = "medium"
    evidence_message_ids: list[str] = Field(min_length=1, alias="evidenceMessageIds")
    goal_ids: list[str] = Field(default_factory=list, alias="goalIds")


class NewShortGoal(AIContractModel):
    ref: str
    description: str = Field(min_length=1)
    parent_goal_id: str | None = Field(default=None, alias="parentGoalId")
    target_actor_ids: list[str] = Field(default_factory=list, alias="targetActorIds")
    topic_hints: list[str] = Field(default_factory=list, alias="topicHints")
    importance: int = Field(default=2, ge=1, le=5)
    trigger_memory_refs: list[str] = Field(min_length=1, alias="triggerMemoryRefs")


class ExitConsolidation(AIContractModel):
    memories: list[MemoryExtraction] = Field(default_factory=list)
    goal_updates: list[GoalUpdate] = Field(default_factory=list, alias="goalUpdates")
    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list, alias="relationshipUpdates")
    new_short_goals: list[NewShortGoal] = Field(default_factory=list, alias="newShortGoals")
    chapter_effects: list[ChapterEffect] = Field(default_factory=list, alias="chapterEffects")


__all__ = [
    "ChatDecision",
    "ChapterEffect",
    "DailyActionDecision",
    "ExitConsolidation",
    "GoalUpdate",
    "InvitationDecision",
    "MemoryExtraction",
    "MemoryQuery",
    "NewShortGoal",
    "PendingGoal",
    "RelationshipUpdate",
    "SegmentSummary",
    "SpeechGeneration",
]
