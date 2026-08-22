import type { PublicAgenda, RunSnapshot } from '../../../core/frontend/src/api/types'

export const agendas: PublicAgenda[] = [
  {
    agendaId: 'agenda_001_literary_society',
    ownerNpcId: 'npc_001',
    title: '青槐文社',
    publicSummary: '以公益书法课和老街故事会为核心。',
  },
]

export function snapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    runId: 'run_test',
    stateVersion: 2,
    eventSeq: 2,
    worldTime: {
      day: 1,
      hour: 9,
      minute: 0,
      time: '09:00',
      label: 'Day1 09:00',
      status: 'running',
    },
    playerAgendaId: 'agenda_001_literary_society',
    actors: [
      {
        actorId: 'player_001',
        kind: 'player',
        name: '玩家',
        role: '旧书店兼职帮手',
        publicBackground: '半个月前搬来青槐巷。',
        publicImpression: ['做事稳妥'],
      },
      {
        actorId: 'npc_001',
        kind: 'npc',
        name: '林慧兰',
        role: '退休语文教师',
        publicBackground: '常在社区教书法。',
        publicImpression: ['懂事知礼的长辈'],
      },
      {
        actorId: 'npc_002',
        kind: 'npc',
        name: '沈星遥',
        role: '自由插画师',
        publicBackground: '搬来本地半年。',
        publicImpression: ['安静'],
      },
    ],
    actorStates: {
      player_001: { status: 'present', position: { x: 1, y: 2 } },
      npc_001: { status: 'waiting', position: { x: 0, y: 0 } },
      npc_002: { status: 'chatting', position: { x: 2, y: 0 } },
    },
    conversations: [],
    pendingInvitations: [],
    pendingJoinRequests: [],
    worldEvents: [],
    currentWorldState: {},
    chapterEnded: false,
    chapterResolution: null,
    ...overrides,
  }
}
