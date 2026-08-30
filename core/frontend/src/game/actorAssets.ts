import type { CSSProperties } from 'react'

export type PortraitState = 'neutral' | 'speaking' | 'tense'

export interface ActorAsset {
  key: string
  url: string
  frame: number
  fallback: string
}

const NPC_SHEETS: Record<string, { slug: string; fallback: string }> = {
  npc_001: { slug: 'lin-huilan', fallback: '林' },
  npc_002: { slug: 'shen-xingyao', fallback: '沈' },
  npc_003: { slug: 'zhao-lei', fallback: '赵' },
  npc_004: { slug: 'chen-yue', fallback: '陈' },
  npc_005: { slug: 'zhou-shenzhi', fallback: '周' },
}

const FRAME_BY_STATE: Record<PortraitState, number> = { neutral: 0, speaking: 1, tense: 2 }

export function selectPortraitState(status?: string, speaking = false): PortraitState {
  if (speaking) return 'speaking'
  if (status === 'refused' || status === 'tense' || status === 'departed') return 'tense'
  return 'neutral'
}

export function actorAsset(actorId: string, state: PortraitState = 'neutral'): ActorAsset {
  if (actorId === 'player_001') {
    return { key: 'portrait-player', url: '/assets/actors/player-neutral.png', frame: 0, fallback: '我' }
  }
  const entry = NPC_SHEETS[actorId]
  if (!entry) return { key: 'portrait-fallback', url: '', frame: 0, fallback: '?' }
  return {
    key: `portrait-${entry.slug}`,
    url: `/assets/actors/${entry.slug}-states.jpg`,
    frame: FRAME_BY_STATE[state],
    fallback: entry.fallback,
  }
}

export function actorPortraitCss(actorId: string, state: PortraitState = 'neutral'): CSSProperties {
  const asset = actorAsset(actorId, state)
  if (!asset.url) return {}
  if (actorId === 'player_001') return { backgroundImage: `url(${asset.url})`, backgroundPosition: 'center 28%' }
  return {
    backgroundImage: `url(${asset.url})`,
    backgroundSize: '300% 100%',
    backgroundPosition: `${asset.frame * 50}% 26%`,
  }
}

export const NPC_PORTRAIT_SHEETS = Object.keys(NPC_SHEETS).map((actorId) => actorAsset(actorId))
