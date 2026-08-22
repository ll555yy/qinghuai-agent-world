import type { WorldTime } from '../api/types'

export type TimeVisualStage = 'daylight' | 'golden' | 'closing' | 'countdown' | 'ended'

export interface TimeVisual {
  stage: TimeVisualStage
  color: number
  alpha: number
  label: string | null
}

export function timeVisual(worldTime: WorldTime): TimeVisual {
  const minutes = worldTime.hour * 60 + worldTime.minute
  if (minutes >= 18 * 60) return { stage: 'ended', color: 0x1f2430, alpha: 0.52, label: '今日闭店' }
  if (minutes >= 17 * 60 + 50) return { stage: 'countdown', color: 0x563a42, alpha: 0.26, label: `距闭店 ${18 * 60 - minutes} 分钟` }
  if (minutes >= 17 * 60) return { stage: 'closing', color: 0xb56a39, alpha: 0.17, label: '已停止发起新聊天' }
  if (minutes >= 16 * 60) return { stage: 'golden', color: 0xe0a04b, alpha: 0.12, label: null }
  return { stage: 'daylight', color: 0xfff2cf, alpha: 0.04, label: null }
}
