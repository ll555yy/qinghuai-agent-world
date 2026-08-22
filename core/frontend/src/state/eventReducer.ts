import type {
  ActorState,
  PublicConversation,
  PublicInvitation,
  PublicJoinRequest,
  PublicMessage,
  PublicWorldEvent,
  RunEvent,
  RunSnapshot,
  WorldTime,
} from '../api/types'

export interface WorldData {
  snapshot: RunSnapshot | null
  messages: Record<string, PublicMessage[]>
  invitations: Record<string, PublicInvitation>
  joinRequests: Record<string, PublicJoinRequest>
}

function payloadObject<T>(payload: Record<string, unknown>, key: string): T | undefined {
  const value = payload[key]
  return typeof value === 'object' && value !== null ? (value as T) : undefined
}

function replaceConversation(
  conversations: PublicConversation[],
  conversation: PublicConversation,
): PublicConversation[] {
  const rest = conversations.filter((item) => item.conversationId !== conversation.conversationId)
  return [...rest, conversation].sort((a, b) => a.creationSeq - b.creationSeq)
}

function appendUniqueMessage(messages: PublicMessage[], message: PublicMessage): PublicMessage[] {
  return messages.some((item) => item.messageId === message.messageId)
    ? messages
    : [...messages, message]
}

export function reduceRunEvent(state: WorldData, event: RunEvent): WorldData {
  if (!state.snapshot || event.eventSeq <= state.snapshot.eventSeq) return state

  let snapshot: RunSnapshot = {
    ...state.snapshot,
    eventSeq: event.eventSeq,
    stateVersion: event.stateVersion,
  }
  let messages = state.messages
  let invitations = state.invitations
  let joinRequests = state.joinRequests
  const payload = event.payload
  const worldTime = payloadObject<WorldTime>(payload, 'worldTime')
  if (worldTime) snapshot = { ...snapshot, worldTime }

  if (event.eventType === 'actor_movement_completed') {
    const actorId = payload.actorId
    const position = payloadObject<ActorState['position']>(payload, 'position')
    if (typeof actorId === 'string' && position) {
      const previous = snapshot.actorStates[actorId]
      snapshot = {
        ...snapshot,
        actorStates: {
          ...snapshot.actorStates,
          [actorId]: { status: previous?.status ?? 'present', position },
        },
      }
    }
  }

  if (
    ['conversation_created', 'conversation_participant_joined', 'conversation_participant_left', 'conversation_closed'].includes(
      event.eventType,
    )
  ) {
    const conversation = payloadObject<PublicConversation>(payload, 'conversation')
    if (conversation) {
      snapshot = {
        ...snapshot,
        conversations: replaceConversation(snapshot.conversations, conversation),
      }
      if (
        event.eventType === 'conversation_participant_joined' ||
        event.eventType === 'conversation_participant_left'
      ) {
        const action = event.eventType === 'conversation_participant_joined' ? 'joined' : 'left'
        const actorId = payload[action === 'joined' ? 'actorJoined' : 'actorLeft']
        if (typeof actorId === 'string') {
          const systemMessage: PublicMessage = {
            messageId: `event_${event.eventSeq}`,
            conversationId: conversation.conversationId,
            authorActorId: 'system',
            text: action === 'joined' ? '加入了聊天' : '离开了聊天',
            system: true,
            systemActorId: actorId,
            systemAction: action,
          }
          messages = {
            ...messages,
            [conversation.conversationId]: appendUniqueMessage(
              messages[conversation.conversationId] ?? [],
              systemMessage,
            ),
          }
        }
      }
    }
  }

  if (event.eventType === 'message_created') {
    const { conversationId, messageId, authorActorId, text } = payload
    if (
      typeof conversationId === 'string' &&
      typeof messageId === 'string' &&
      typeof authorActorId === 'string' &&
      typeof text === 'string'
    ) {
      const message: PublicMessage = { conversationId, messageId, authorActorId, text }
      messages = {
        ...messages,
        [conversationId]: appendUniqueMessage(messages[conversationId] ?? [], message),
      }
    }
  }

  if (event.eventType === 'invitation_requested') {
    const { invitationId, initiatorActorId, targetActorId } = payload
    if (
      typeof invitationId === 'string' &&
      typeof initiatorActorId === 'string' &&
      typeof targetActorId === 'string'
    ) {
      invitations = {
        ...invitations,
        [invitationId]: {
          invitationId,
          initiatorActorId,
          targetActorId,
          status: 'pending',
        },
      }
    }
  }

  if (['invitation_accepted', 'invitation_refused', 'invitation_expired'].includes(event.eventType)) {
    const invitationId = payload.invitationId
    if (typeof invitationId === 'string' && invitations[invitationId]) {
      const status =
        event.eventType === 'invitation_accepted'
          ? 'accepted'
          : event.eventType === 'invitation_refused'
            ? 'refused'
            : 'expired'
      invitations = {
        ...invitations,
        [invitationId]: {
          ...invitations[invitationId],
          status,
          conversationId:
            typeof payload.conversationId === 'string' ? payload.conversationId : undefined,
        },
      }
    }
  }

  if (event.eventType === 'join_request_created') {
    const joinRequest = payloadObject<PublicJoinRequest>(payload, 'joinRequest')
    if (joinRequest) joinRequests = { ...joinRequests, [joinRequest.joinRequestId]: joinRequest }
  }

  if (event.eventType === 'join_request_resolved') {
    const joinRequestId = payload.joinRequestId
    if (typeof joinRequestId === 'string' && joinRequests[joinRequestId]) {
      joinRequests = {
        ...joinRequests,
        [joinRequestId]: {
          ...joinRequests[joinRequestId],
          status: typeof payload.status === 'string' ? payload.status : 'expired',
          resolvedAt: typeof payload.resolvedAt === 'string' ? payload.resolvedAt : undefined,
          expiredAt: typeof payload.expiredAt === 'string' ? payload.expiredAt : undefined,
          pendingPlayerDecision: false,
        },
      }
    }
  }

  if (event.eventType === 'world_event_occurred') {
    const worldEvent = payloadObject<PublicWorldEvent>(payload, 'event')
    if (worldEvent && !snapshot.worldEvents.some((item) => item.eventId === worldEvent.eventId)) {
      snapshot = { ...snapshot, worldEvents: [...snapshot.worldEvents, worldEvent] }
    }
  }

  if (event.eventType === 'chapter_resolved') {
    snapshot = {
      ...snapshot,
      chapterEnded: true,
      chapterResolution: payload as unknown as RunSnapshot['chapterResolution'],
      worldTime: { ...snapshot.worldTime, status: 'chapter_ended' },
    }
  }

  return { snapshot, messages, invitations, joinRequests }
}
