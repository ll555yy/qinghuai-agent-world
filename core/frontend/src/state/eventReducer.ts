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

function isPendingMessage(message: PublicMessage): boolean {
  return message.deliveryStatus === 'sending' || message.deliveryStatus === 'failed'
}

function canReconcilePendingMessage(
  pending: PublicMessage,
  incoming: PublicMessage,
): boolean {
  if (
    !isPendingMessage(pending) ||
    incoming.deliveryStatus !== undefined ||
    pending.authorActorId !== incoming.authorActorId ||
    pending.text !== incoming.text
  ) {
    return false
  }

  // A history response can race an in-flight send.  When both sides have a
  // timestamp, avoid replacing a newer optimistic message with an older
  // message that happens to have the same text.
  return !pending.createdAt || !incoming.createdAt || pending.createdAt === incoming.createdAt
}

function sameMessage(left: PublicMessage, right: PublicMessage): boolean {
  const leftReplyIds = left.replyToMessageIds
  const rightReplyIds = right.replyToMessageIds
  return (
    left.messageId === right.messageId &&
    left.conversationId === right.conversationId &&
    left.authorActorId === right.authorActorId &&
    left.text === right.text &&
    left.createdAt === right.createdAt &&
    left.segmentId === right.segmentId &&
    left.roundId === right.roundId &&
    left.roundSequence === right.roundSequence &&
    left.system === right.system &&
    left.systemActorId === right.systemActorId &&
    left.systemAction === right.systemAction &&
    left.deliveryStatus === right.deliveryStatus &&
    leftReplyIds?.length === rightReplyIds?.length &&
    leftReplyIds?.every((messageId, index) => messageId === rightReplyIds?.[index]) !== false
  )
}

function mergeMessage(existing: PublicMessage, incoming: PublicMessage): PublicMessage {
  const merged: PublicMessage = { ...existing, ...incoming }
  // A server message is authoritative.  Do not leave a local delivery state
  // attached after the canonical message has arrived.
  if (incoming.deliveryStatus === undefined) delete merged.deliveryStatus
  if (sameMessage(existing, merged)) return existing
  return merged
}

/**
 * Append one message in arrival order while keeping message IDs idempotent.
 * A canonical server message can replace its matching local optimistic
 * message, but it never gets keyed by round metadata: several messages may
 * legitimately belong to one round.
 */
export function appendConversationMessage(
  messages: PublicMessage[],
  incoming: PublicMessage,
): PublicMessage[] {
  const existingIndex = messages.findIndex((message) => message.messageId === incoming.messageId)
  if (existingIndex >= 0) return messages

  const pendingIndex = messages.findIndex((message) => canReconcilePendingMessage(message, incoming))
  if (pendingIndex >= 0) {
    const result = [...messages]
    result[pendingIndex] = incoming
    return result
  }
  return [...messages, incoming]
}

/**
 * Merge a REST history/command response without losing live events that won
 * the race with the response.  Existing messages keep their event-arrival
 * order; new messages in the response are appended in the response's order.
 * This also works when a caller supplies only a response delta rather than a
 * complete history list.
 */
export function mergeConversationMessages(
  existing: PublicMessage[],
  incoming: PublicMessage[],
): PublicMessage[] {
  if (!incoming.length) return existing

  const consumedPendingIds = new Set<string>()
  let result = existing
  let changed = false

  for (const message of incoming) {
    const existingIndex = result.findIndex((candidate) => candidate.messageId === message.messageId)
    if (existingIndex >= 0) {
      const previous = result[existingIndex]
      const merged = mergeMessage(previous, message)
      if (merged !== previous) {
        if (result === existing) result = [...existing]
        result[existingIndex] = merged
        changed = true
      }
      continue
    }

    const pendingIndex = result.findIndex(
      (candidate) =>
        !consumedPendingIds.has(candidate.messageId) &&
        canReconcilePendingMessage(candidate, message),
    )
    if (pendingIndex >= 0) {
      consumedPendingIds.add(result[pendingIndex].messageId)
      if (result === existing) result = [...existing]
      result[pendingIndex] = message
      changed = true
    } else {
      if (result === existing) result = [...existing]
      result.push(message)
      changed = true
    }
  }

  return changed ? result : existing
}

/**
 * Confirm one optimistic send using the authoritative message ID returned by
 * the command response. This remains exact even when the world clock advances
 * between the local click and the server write, so reconciliation never has
 * to guess from text or timestamps.
 */
export function confirmAcceptedMessage(
  messages: PublicMessage[],
  pendingMessageId: string,
  acceptedMessageId: string,
  canonical?: PublicMessage,
): PublicMessage[] {
  const pendingIndex = messages.findIndex((message) => message.messageId === pendingMessageId)
  const acceptedIndex = messages.findIndex((message) => message.messageId === acceptedMessageId)

  if (pendingIndex < 0) {
    if (acceptedIndex < 0 || !canonical) return messages
    const merged = mergeMessage(messages[acceptedIndex], canonical)
    if (merged === messages[acceptedIndex]) return messages
    const result = [...messages]
    result[acceptedIndex] = merged
    return result
  }

  const pending = messages[pendingIndex]
  const result = messages.filter((message) => message.messageId !== pendingMessageId)
  const retainedAcceptedIndex = result.findIndex(
    (message) => message.messageId === acceptedMessageId,
  )
  if (retainedAcceptedIndex >= 0) {
    if (canonical) {
      result[retainedAcceptedIndex] = mergeMessage(result[retainedAcceptedIndex], canonical)
    }
    return result
  }

  const replacement: PublicMessage = canonical ?? {
    ...pending,
    messageId: acceptedMessageId,
  }
  if (replacement.deliveryStatus !== undefined) delete replacement.deliveryStatus
  result.splice(Math.min(pendingIndex, result.length), 0, replacement)
  return result
}

function replaceConversation(
  conversations: PublicConversation[],
  conversation: PublicConversation,
): PublicConversation[] {
  const rest = conversations.filter((item) => item.conversationId !== conversation.conversationId)
  return [...rest, conversation].sort((a, b) => a.creationSeq - b.creationSeq)
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
      const participantAction =
        event.eventType === 'conversation_participant_joined'
          ? 'joined'
          : event.eventType === 'conversation_participant_left' ||
              (event.eventType === 'conversation_closed' && typeof payload.actorLeft === 'string')
            ? 'left'
            : null
      if (participantAction) {
        const actorId = payload[participantAction === 'joined' ? 'actorJoined' : 'actorLeft']
        if (typeof actorId === 'string') {
          const systemMessage: PublicMessage = {
            messageId: `event_${event.eventSeq}`,
            conversationId: conversation.conversationId,
            authorActorId: 'system',
            text: participantAction === 'joined' ? '加入了聊天' : '已经离开对话',
            system: true,
            systemActorId: actorId,
            systemAction: participantAction,
          }
          messages = {
            ...messages,
            [conversation.conversationId]: appendConversationMessage(
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
      const message: PublicMessage = {
        conversationId,
        messageId,
        authorActorId,
        text,
        ...(typeof payload.createdAt === 'string' ? { createdAt: payload.createdAt } : {}),
        ...(typeof payload.segmentId === 'string' ? { segmentId: payload.segmentId } : {}),
        ...(typeof payload.roundId === 'string' ? { roundId: payload.roundId } : {}),
        ...(typeof payload.roundSequence === 'number' && Number.isFinite(payload.roundSequence)
          ? { roundSequence: payload.roundSequence }
          : {}),
        ...(Array.isArray(payload.replyToMessageIds) &&
        payload.replyToMessageIds.every((item): item is string => typeof item === 'string')
          ? { replyToMessageIds: [...payload.replyToMessageIds] }
          : {}),
      }
      const previous = messages[conversationId] ?? []
      const next = appendConversationMessage(previous, message)
      if (next !== previous) messages = { ...messages, [conversationId]: next }
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

  if (event.eventType === 'conversation_experience_recorded') {
    const experience = payloadObject<RunSnapshot['conversationExperiences'][number]>(payload, 'experience')
    if (experience) {
      const existing = snapshot.conversationExperiences ?? []
      snapshot = {
        ...snapshot,
        conversationExperiences: [
          ...existing.filter((item) => item.experienceId !== experience.experienceId),
          experience,
        ],
      }
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
