export const PLAYER_ACTOR_ID = 'player_001'

export type JsonRecord = Record<string, unknown>

export interface Position {
  x: number
  y: number
}

export interface WorldTime {
  day: number
  hour: number
  minute: number
  time: string
  label: string
  status: 'running' | 'paused' | 'chapter_ended' | string
}

export interface PublicActor {
  actorId: string
  kind: 'npc' | 'player' | string
  name: string
  role: string
  publicBackground: string
  publicImpression: string[]
}

export interface ActorState {
  status: string
  position: Position
}

export interface PublicAgenda {
  agendaId: string
  ownerNpcId: string
  title: string
  publicSummary: string
}

export interface PublicConversation {
  conversationId: string
  creationSeq: number
  participants: string[]
  status: 'open' | 'closed'
  closeReason?: string
}

export interface PublicMessage {
  messageId: string
  conversationId: string
  authorActorId: string
  text: string
  createdAt?: string
  segmentId?: string
  system?: boolean
  systemActorId?: string
  systemAction?: 'joined' | 'left'
}

export interface PublicWorldEvent {
  eventId: string
  worldDay: number
  at: string
  visibility: string
  sourceLabel: string
  summary: string
}

export interface ChapterResolution {
  chapterId: string
  branch: 'consensus_submitted' | 'compromise_submitted' | 'no_submission' | string
  agendaResults: Record<string, 'core_adopted' | 'partially_adopted' | 'not_adopted' | string>
  playerTaskResult: 'completed' | 'partial' | 'failed' | null
  actorStances?: Record<string, string>
  playerHighlights?: Array<{
    messageId: string
    conversationId: string
    text: string
    createdAt?: string
  }>
}

export interface RunSnapshot {
  runId: string
  stateVersion: number
  eventSeq: number
  worldTime: WorldTime
  playerAgendaId: string | null
  actors: PublicActor[]
  actorStates: Record<string, ActorState>
  conversations: PublicConversation[]
  pendingInvitations: PublicInvitation[]
  pendingJoinRequests: PublicJoinRequest[]
  worldEvents: PublicWorldEvent[]
  currentWorldState: JsonRecord
  chapterEnded: boolean
  chapterResolution: ChapterResolution | null
}

export interface RunEvent {
  runId: string
  eventSeq: number
  stateVersion: number
  eventType: string
  payload: JsonRecord
}

export interface PublicInvitation {
  invitationId: string
  initiatorActorId: string
  targetActorId: string
  status: 'pending' | 'accepted' | 'refused' | 'expired' | string
  requestedAt?: string
  respondedAt?: string
  conversationId?: string
}

export interface PublicJoinRequest {
  joinRequestId: string
  conversationId: string
  applicantActorId: string
  status: 'pending' | 'accepted' | 'refused' | 'expired' | string
  requestedAt?: string
  approverActorIds: string[]
  pendingPlayerDecision: boolean
  resolvedAt?: string
  expiredAt?: string
}

export interface ScenarioMetadata {
  chapter: {
    chapterId: string
    name: string
    startDay: number
    endDay: number
    endsAt: string
  }
  agendas: PublicAgenda[]
  actors: PublicActor[]
}

export interface HealthStatus {
  status: 'ok' | 'degraded'
  processAlive: boolean
  scenarioLoaded: boolean
  persistence?: string
  storageHealthy?: boolean
}

export interface CommandResponse {
  run: RunSnapshot
  conversation?: PublicConversation
  invitation?: PublicInvitation
  joinRequest?: PublicJoinRequest
  messages?: PublicMessage[]
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isRunEvent(value: unknown): value is RunEvent {
  return (
    isRecord(value) &&
    typeof value.runId === 'string' &&
    typeof value.eventSeq === 'number' &&
    typeof value.stateVersion === 'number' &&
    typeof value.eventType === 'string' &&
    isRecord(value.payload)
  )
}

export function isRunSnapshot(value: unknown): value is RunSnapshot {
  return (
    isRecord(value) &&
    typeof value.runId === 'string' &&
    typeof value.stateVersion === 'number' &&
    typeof value.eventSeq === 'number' &&
    isRecord(value.worldTime) &&
    Array.isArray(value.actors) &&
    isRecord(value.actorStates) &&
    Array.isArray(value.conversations) &&
    Array.isArray(value.worldEvents)
  )
}
