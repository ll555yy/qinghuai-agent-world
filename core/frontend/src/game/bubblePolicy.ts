export type BubbleTone = 'npc' | 'player' | 'invite' | 'accept' | 'refuse' | 'join' | 'leave' | 'thinking' | 'closing' | 'system'

export interface BubbleCue {
  id: string
  actorId: string
  text: string
  tone: BubbleTone
}

export function truncateBubble(text: string, max = 34): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  return compact.length > max ? `${compact.slice(0, max - 1)}…` : compact
}

export function bubbleDuration(text: string, reducedMotion = false): number {
  if (reducedMotion) return 1_600
  return Math.max(1_900, Math.min(4_600, 1_400 + text.length * 75))
}

export function clampBubblePosition(x: number, y: number, width: number, layer = 0): { x: number; y: number } {
  return {
    x: Math.max(width / 2 + 12, Math.min(880 - width / 2 - 12, x)),
    y: Math.max(42, y - layer * 34),
  }
}

export class ActorBubbleQueue {
  private readonly pending = new Map<string, BubbleCue[]>()

  enqueue(cue: BubbleCue): boolean {
    const queue = this.pending.get(cue.actorId) ?? []
    if (queue.some((item) => item.id === cue.id)) return false
    queue.push(cue)
    this.pending.set(cue.actorId, queue)
    return true
  }

  shift(actorId: string): BubbleCue | undefined {
    const queue = this.pending.get(actorId)
    const next = queue?.shift()
    if (!queue?.length) this.pending.delete(actorId)
    return next
  }

  size(actorId: string): number {
    return this.pending.get(actorId)?.length ?? 0
  }

  clear(): void {
    this.pending.clear()
  }
}
