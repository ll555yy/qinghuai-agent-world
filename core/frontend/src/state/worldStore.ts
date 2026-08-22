import { create } from 'zustand'

import type {
  PublicInvitation,
  PublicJoinRequest,
  PublicMessage,
  RunEvent,
  RunSnapshot,
} from '../api/types'
import { reduceRunEvent, type WorldData } from './eventReducer'

export interface Notice {
  id: string
  text: string
  tone: 'info' | 'success' | 'warning' | 'error'
}

export interface SceneCue {
  id: string
  actorId: string
  text: string
  tone: 'paper' | 'refuse'
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
      const notice = noticeFor(event)
      const invitationId = event.payload.invitationId
      const invitation = typeof invitationId === 'string' ? reduced.invitations[invitationId] : undefined
      const sceneCue = event.eventType === 'invitation_requested' && invitation
        ? { id: `event-${event.eventSeq}`, actorId: invitation.initiatorActorId, text: '想和你聊聊', tone: 'paper' as const }
        : event.eventType === 'invitation_refused' && invitation
          ? { id: `event-${event.eventSeq}`, actorId: invitation.targetActorId, text: '不了，我现在不想聊', tone: 'refuse' as const }
          : state.sceneCue
      return {
        ...reduced,
        sceneCue,
        notices: notice
          ? [...state.notices.slice(-3), { ...notice, id: crypto.randomUUID() }]
          : state.notices,
      }
    }),
  setConversationMessages: (conversationId, conversationMessages) =>
    set((state) => ({ messages: { ...state.messages, [conversationId]: conversationMessages } })),
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
  showSceneCue: (actorId, text, tone = 'paper') =>
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
