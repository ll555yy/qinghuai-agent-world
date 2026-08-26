import { create } from 'zustand'

import type {
  PublicInvitation,
  PublicJoinRequest,
  PublicMessage,
  RunEvent,
  RunSnapshot,
} from '../api/types'
import {
  appendConversationMessage,
  mergeConversationMessages,
  reduceRunEvent,
  type WorldData,
} from './eventReducer'
import type { BubbleTone } from '../game/bubblePolicy'

export interface Notice {
  id: string
  text: string
  tone: 'info' | 'success' | 'warning' | 'error'
}

export interface SceneCue {
  id: string
  actorId: string
  text: string
  tone: BubbleTone
}

interface WorldStore extends WorldData {
  notices: Notice[]
  sceneCue: SceneCue | null
  socketStatus: 'connecting' | 'connected' | 'disconnected' | 'failed'
  busy: boolean
  error: string | null
  setSnapshot: (snapshot: RunSnapshot) => void
  applyEvent: (event: RunEvent) => void
  setConversationMessages: (conversationId: string, messages: PublicMessage[]) => void
  appendConversationMessage: (conversationId: string, message: PublicMessage) => void
  setMessageDeliveryStatus: (
    conversationId: string,
    messageId: string,
    status: PublicMessage['deliveryStatus'],
  ) => void
  setInvitation: (invitation: PublicInvitation) => void
  setJoinRequest: (request: PublicJoinRequest) => void
  setSocketStatus: (status: WorldStore['socketStatus']) => void
  setBusy: (busy: boolean) => void
  setError: (error: string | null) => void
  addNotice: (text: string, tone?: Notice['tone']) => void
  showSceneCue: (actorId: string, text: string, tone?: SceneCue['tone']) => void
  dismissNotice: (id: string) => void
  reset: () => void
}

const initialWorld: WorldData = {
  snapshot: null,
  messages: {},
  invitations: {},
  joinRequests: {},
}

function noticeFor(event: RunEvent): Omit<Notice, 'id'> | null {
  switch (event.eventType) {
    case 'invitation_refused':
      return { text: '对方拒绝了聊天邀请。', tone: 'warning' }
    case 'invitation_accepted':
      return { text: '聊天邀请已接受。', tone: 'success' }
    case 'invitation_expired':
      return { text: '邀请已过期，17:00 后不能开始新聊天。', tone: 'warning' }
    case 'conversation_participant_joined':
      return { text: '有新成员加入了聊天。', tone: 'info' }
    case 'conversation_participant_left':
      return { text: '一名成员离开了聊天。', tone: 'info' }
    case 'world_day_ended':
      return { text: '今天的活动已经结束。', tone: 'info' }
    case 'world_event_occurred':
      return { text: '青槐巷发生了新的事件。', tone: 'info' }
    default:
      return null
  }
}

function cueFor(event: RunEvent, invitations: Record<string, PublicInvitation>): SceneCue | null {
  const p = event.payload
  const actorId = typeof p.actorId === 'string' ? p.actorId : undefined
  const id = `event-${event.eventSeq}`
  if (event.eventType === 'message_created' && typeof p.authorActorId === 'string' && typeof p.text === 'string') {
    return { id, actorId: p.authorActorId, text: p.text, tone: p.authorActorId === 'player_001' ? 'player' : 'npc' }
  }
  if (event.eventType === 'npc_thought_started' && actorId) return { id, actorId, text: '正在想……', tone: 'thinking' }
  if (event.eventType === 'invitation_requested') {
    const invitationId = typeof p.invitationId === 'string' ? p.invitationId : ''
    const invitation = invitations[invitationId]
    if (invitation) return { id, actorId: invitation.initiatorActorId, text: '想和你聊聊', tone: 'invite' }
  }
  if (['invitation_accepted', 'invitation_refused', 'invitation_expired'].includes(event.eventType)) {
    const invitationId = typeof p.invitationId === 'string' ? p.invitationId : ''
    const invitation = invitations[invitationId]
    if (invitation) {
      if (event.eventType === 'invitation_accepted') return { id, actorId: invitation.targetActorId, text: '好，我们聊聊', tone: 'accept' }
      return { id, actorId: invitation.targetActorId, text: event.eventType === 'invitation_refused' ? '不了，我现在不想聊' : '来不及开始了', tone: 'refuse' }
    }
  }
  if (event.eventType === 'conversation_participant_joined' && typeof p.actorJoined === 'string') return { id, actorId: p.actorJoined, text: '加入了聊天', tone: 'join' }
  if (event.eventType === 'conversation_participant_left' && typeof p.actorLeft === 'string') return { id, actorId: p.actorLeft, text: '先告辞了', tone: 'leave' }
  return null
}

export const useWorldStore = create<WorldStore>((set) => ({
  ...initialWorld,
  notices: [],
  sceneCue: null,
  socketStatus: 'disconnected',
  busy: false,
  error: null,
  setSnapshot: (snapshot) =>
    set((state) => ({
      snapshot,
      invitations: {
        ...Object.fromEntries(
          Object.entries(state.invitations).filter(([, invitation]) => invitation.status !== 'pending'),
        ),
        ...Object.fromEntries(snapshot.pendingInvitations.map((item) => [item.invitationId, item])),
      },
      joinRequests: {
        ...Object.fromEntries(
          Object.entries(state.joinRequests).filter(([, request]) => request.status !== 'pending'),
        ),
        ...Object.fromEntries(snapshot.pendingJoinRequests.map((item) => [item.joinRequestId, item])),
      },
    })),
  applyEvent: (event) =>
    set((state) => {
      const reduced = reduceRunEvent(state, event)
      if (reduced === state) return state
      const notice = noticeFor(event)
      const duplicateMessage =
        event.eventType === 'message_created' &&
        typeof event.payload.conversationId === 'string' &&
        typeof event.payload.messageId === 'string' &&
        (state.messages[event.payload.conversationId] ?? []).some(
          (message) => message.messageId === event.payload.messageId,
        )
      const sceneCue = duplicateMessage ? state.sceneCue : cueFor(event, reduced.invitations) ?? state.sceneCue
      return {
        ...reduced,
        sceneCue,
        notices: notice
          ? [...state.notices.slice(-3), { ...notice, id: crypto.randomUUID() }]
          : state.notices,
      }
    }),
  setConversationMessages: (conversationId, conversationMessages) =>
    set((state) => {
      const previous = state.messages[conversationId] ?? []
      const merged = mergeConversationMessages(previous, conversationMessages)
      if (merged === previous) return state
      return { messages: { ...state.messages, [conversationId]: merged } }
    }),
  appendConversationMessage: (conversationId, message) =>
    set((state) => {
      const existing = state.messages[conversationId] ?? []
      const merged = appendConversationMessage(existing, message)
      if (merged === existing) return state
      return { messages: { ...state.messages, [conversationId]: merged } }
    }),
  setMessageDeliveryStatus: (conversationId, messageId, deliveryStatus) =>
    set((state) => {
      const previous = state.messages[conversationId]
      if (!previous) return state
      let changed = false
      const next = previous.map((message) => {
        if (message.messageId !== messageId || message.deliveryStatus === deliveryStatus) return message
        changed = true
        if (deliveryStatus === undefined) {
          const withoutStatus = { ...message }
          delete withoutStatus.deliveryStatus
          return withoutStatus
        }
        return { ...message, deliveryStatus }
      })
      return changed ? { messages: { ...state.messages, [conversationId]: next } } : state
    }),
  setInvitation: (invitation) =>
    set((state) => ({ invitations: { ...state.invitations, [invitation.invitationId]: invitation } })),
  setJoinRequest: (request) =>
    set((state) => ({ joinRequests: { ...state.joinRequests, [request.joinRequestId]: request } })),
  setSocketStatus: (socketStatus) => set({ socketStatus }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  addNotice: (text, tone = 'info') =>
    set((state) => ({
      notices: [...state.notices.slice(-3), { id: crypto.randomUUID(), text, tone }],
    })),
  showSceneCue: (actorId, text, tone = 'npc') =>
    set({ sceneCue: { id: crypto.randomUUID(), actorId, text, tone } }),
  dismissNotice: (id) =>
    set((state) => ({ notices: state.notices.filter((notice) => notice.id !== id) })),
  reset: () =>
    set({
      ...initialWorld,
      notices: [],
      sceneCue: null,
      socketStatus: 'disconnected',
      busy: false,
      error: null,
    }),
}))
