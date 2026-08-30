from __future__ import annotations

import pytest
from core.backend.app.domain.conversation import Conversation
from core.backend.app.domain.errors import (
    ActorAlreadyInConversationError,
    ConversationFullError,
    InvalidConversationParticipantsError,
)


def test_participant_limit_and_duplicate() -> None:
    conversation = Conversation("conv_1", 1, ["a", "b"])
    conversation.add_participant("c")
    with pytest.raises(ConversationFullError):
        conversation.add_participant("d")
    with pytest.raises(ActorAlreadyInConversationError):
        conversation.add_participant("a")


def test_leaving_below_two_closes_conversation() -> None:
    conversation = Conversation("conv_1", 1, ["a", "b"])
    conversation.remove_participant("a")
    assert conversation.status == "closed"
    assert conversation.close_reason == "fewer_than_two_participants"
    assert conversation.participants == ["b"]


def test_conversation_requires_two_different_actors() -> None:
    with pytest.raises(InvalidConversationParticipantsError):
        Conversation("conv_1", 1, ["a", "a"])

