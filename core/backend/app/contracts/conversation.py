"""Conversation command contracts."""

from __future__ import annotations

from pydantic import Field

from .common import ContractModel


class CreateConversationRequest(ContractModel):
    participant_ids: list[str] = Field(alias="participantIds")
    command_id: str | None = Field(default=None, alias="commandId")


class ParticipantCommandRequest(ContractModel):
    actor_id: str = Field(alias="actorId")
    command_id: str | None = Field(default=None, alias="commandId")


class LeaveCommandRequest(ContractModel):
    command_id: str | None = Field(default=None, alias="commandId")


class ConversationResponse(ContractModel):
    conversation: dict[str, object]
    run: dict[str, object]


class PlayerInvitationRequest(ContractModel):
    target_actor_id: str = Field(alias="targetActorId")
    command_id: str | None = Field(default=None, alias="commandId")


class InvitationResponseRequest(ContractModel):
    accepted: bool
    command_id: str | None = Field(default=None, alias="commandId")


class JoinRequestResponseRequest(ContractModel):
    accepted: bool
    command_id: str | None = Field(default=None, alias="commandId")


class PlayerMessageRequest(ContractModel):
    text: str = Field(min_length=1, max_length=2000)
    command_id: str | None = Field(default=None, alias="commandId")
