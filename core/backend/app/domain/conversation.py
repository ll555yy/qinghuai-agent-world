"""Conversation lifecycle rules independent of HTTP and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .errors import (
    ActorAlreadyInConversationError,
    ActorNotInConversationError,
    ConversationFullError,
    InvalidConversationParticipantsError,
)

ConversationStatus = Literal["open", "closed"]


@dataclass(slots=True)
class Conversation:
    conversation_id: str
    creation_seq: int
    participants: list[str]
    status: ConversationStatus = "open"
    close_reason: str | None = None
    _seen_participants: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if len(self.participants) not in (2, 3) or len(set(self.participants)) != len(self.participants):
            raise InvalidConversationParticipantsError()
        self._seen_participants.update(self.participants)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def add_participant(self, actor_id: str) -> None:
        if not self.is_open:
            raise InvalidConversationParticipantsError("Cannot join a closed conversation.")
        if actor_id in self.participants:
            raise ActorAlreadyInConversationError()
        if len(self.participants) >= 3:
            raise ConversationFullError()
        self.participants.append(actor_id)
        self._seen_participants.add(actor_id)

    def remove_participant(self, actor_id: str) -> bool:
        if actor_id not in self.participants:
            raise ActorNotInConversationError()
        self.participants.remove(actor_id)
        if len(self.participants) < 2:
            self.close("fewer_than_two_participants")
        return True

    def close(self, reason: str = "closed_by_system") -> None:
        if self.status == "closed":
            return
        self.status = "closed"
        self.close_reason = reason

    def has_participant(self, actor_id: str) -> bool:
        return actor_id in self.participants

    def participant_history(self) -> frozenset[str]:
        return frozenset(self._seen_participants)

    def to_public_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "conversationId": self.conversation_id,
            "creationSeq": self.creation_seq,
            "participants": list(self.participants),
            "status": self.status,
        }
        if self.close_reason is not None:
            result["closeReason"] = self.close_reason
        return result
