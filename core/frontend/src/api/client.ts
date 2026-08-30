import type {
  CommandResponse,
  HealthStatus,
  PublicActor,
  PublicMessage,
  RunSnapshot,
  ScenarioMetadata,
} from './types'
import { isRunSnapshot } from './types'

interface ErrorEnvelope {
  error?: {
    code?: string
    message?: string
    details?: Record<string, unknown>
  }
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  const payload = (await response.json().catch(() => ({}))) as T & ErrorEnvelope
  if (!response.ok) {
    throw new ApiError(
      payload.error?.code ?? 'request_failed',
      payload.error?.message ?? `请求失败（${response.status}）`,
      response.status,
      payload.error?.details,
    )
  }
  return payload
}

function commandId(): string {
  return crypto.randomUUID()
}

function assertSnapshot(value: unknown): RunSnapshot {
  if (!isRunSnapshot(value)) {
    throw new ApiError('invalid_snapshot', '服务器返回的世界快照格式不正确。', 502)
  }
  return value
}

export const api = {
  health: () => requestJson<HealthStatus>('/health'),
  scenario: () => requestJson<ScenarioMetadata>('/scenario/agendas'),

  async createRun(agendaId: string | null): Promise<RunSnapshot> {
    return assertSnapshot(
      await requestJson<unknown>('/runs', {
        method: 'POST',
        body: JSON.stringify({ agendaId }),
      }),
    )
  },

  async getRun(runId: string): Promise<RunSnapshot> {
    return assertSnapshot(await requestJson<unknown>(`/runs/${runId}`))
  },

  actor: (runId: string, actorId: string) =>
    requestJson<PublicActor & { status: string; position: { x: number; y: number } }>(
      `/runs/${runId}/actors/${actorId}`,
    ),

  stepWorld: (runId: string) =>
    requestJson<{ run: RunSnapshot }>(`/runs/${runId}/world/step`, {
      method: 'POST',
      body: JSON.stringify({ realSeconds: 2, commandId: commandId() }),
    }),

  invite: (runId: string, targetActorId: string) =>
    requestJson<CommandResponse>(`/runs/${runId}/invitations`, {
      method: 'POST',
      body: JSON.stringify({ targetActorId, commandId: commandId() }),
    }),

  respondInvitation: (runId: string, invitationId: string, accepted: boolean) =>
    requestJson<CommandResponse>(`/runs/${runId}/invitations/${invitationId}/respond`, {
      method: 'POST',
      body: JSON.stringify({ accepted, commandId: commandId() }),
    }),

  joinConversation: (runId: string, conversationId: string) =>
    requestJson<CommandResponse>(`/runs/${runId}/conversations/${conversationId}/join`, {
      method: 'POST',
      body: JSON.stringify({ commandId: commandId() }),
    }),

  respondJoinRequest: (runId: string, joinRequestId: string, accepted: boolean) =>
    requestJson<CommandResponse>(`/runs/${runId}/join-requests/${joinRequestId}/respond`, {
      method: 'POST',
      body: JSON.stringify({ accepted, commandId: commandId() }),
    }),

  messages: (runId: string, conversationId: string) =>
    requestJson<{ conversationId: string; messages: PublicMessage[] }>(
      `/runs/${runId}/conversations/${conversationId}/messages`,
    ),

  sendMessage: (runId: string, conversationId: string, text: string) =>
    requestJson<CommandResponse>(`/runs/${runId}/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text, commandId: commandId() }),
    }),

  leaveConversation: (runId: string, conversationId: string) =>
    requestJson<CommandResponse>(
      `/runs/${runId}/conversations/${conversationId}/participants/player_001`,
      {
        method: 'DELETE',
        body: JSON.stringify({ commandId: commandId() }),
      },
    ),
}
