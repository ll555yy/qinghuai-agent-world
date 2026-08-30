"""Domain errors shared by the orchestration and API layers.

The domain does not know about HTTP.  ``status_code`` is kept as a small
adapter hint so the API can map the same deterministic error to its REST
status without duplicating a large exception table.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """A deterministic business-rule failure."""

    code = "domain_error"
    status_code = 409
    default_message = "The requested operation is not allowed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class RunNotFoundError(DomainError):
    code = "run_not_found"
    status_code = 404
    default_message = "Run was not found."


class ActorNotFoundError(DomainError):
    code = "actor_not_found"
    status_code = 404
    default_message = "Actor was not found."


class AgendaNotFoundError(DomainError):
    code = "agenda_not_found"
    status_code = 404
    default_message = "Agenda was not found."


class ConversationNotFoundError(DomainError):
    code = "conversation_not_found"
    status_code = 404
    default_message = "Conversation was not found."


class ConversationFullError(DomainError):
    code = "conversation_full"
    default_message = "Conversation already has three participants."


class ConversationLimitReachedError(DomainError):
    code = "conversation_limit_reached"
    default_message = "The run already has two open conversations."


class ActorAlreadyInConversationError(DomainError):
    code = "actor_already_in_conversation"
    default_message = "Actor already belongs to an open conversation."


class ActorNotInConversationError(DomainError):
    code = "actor_not_in_conversation"
    default_message = "Actor is not a participant in this conversation."


class InvalidConversationParticipantsError(DomainError):
    code = "invalid_conversation_participants"
    default_message = "A conversation needs two or three different participants."


class InvalidTimeAdvanceError(DomainError):
    code = "invalid_time_advance"
    default_message = "The requested time advance is invalid."


class ChapterAlreadyEndedError(DomainError):
    code = "chapter_already_ended"
    default_message = "The chapter has already ended."


class DuplicateCommandError(DomainError):
    code = "duplicate_command"
    default_message = "The commandId was already used for another command."


class InvitationNotFoundError(DomainError):
    code = "invitation_not_found"
    status_code = 404
    default_message = "Invitation was not found."


class InvalidInvitationError(DomainError):
    code = "invalid_invitation"
    default_message = "The invitation is no longer valid."


class JoinRequestNotFoundError(DomainError):
    code = "join_request_not_found"
    status_code = 404
    default_message = "Join request was not found."


class InvalidJoinRequestError(DomainError):
    code = "invalid_join_request"
    default_message = "The join request is no longer valid."


class InvalidMessageError(DomainError):
    code = "invalid_message"
    default_message = "The message is invalid."


class WorldStepError(DomainError):
    code = "invalid_world_step"
    default_message = "The world step is invalid."


class PlayerAccessDeniedError(DomainError):
    code = "player_access_denied"
    status_code = 403
    default_message = "The player cannot access this resource."


class ConsolidationNotFoundError(DomainError):
    code = "consolidation_not_found"
    status_code = 404
    default_message = "No failed consolidation was found."
