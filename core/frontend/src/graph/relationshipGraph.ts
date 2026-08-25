import type { RunSnapshot } from '../api/types'

export interface RelationshipNode {
  id: string
  label: string
  role: string
  kind: string
  color: string
  nodeValue: number
}

export interface RelationshipLink {
  source: string
  target: string
  conversationCount: number
  active: boolean
  label: string
}

export interface RelationshipGraphData {
  nodes: RelationshipNode[]
  links: RelationshipLink[]
}

interface AggregatedLink {
  source: string
  target: string
  conversationCount: number
  active: boolean
}

function pairKey(first: string, second: string): string {
  return [first, second].sort().join('::')
}

/** Build only the relationship history the player has actually observed. */
export function buildRelationshipGraph(snapshot: RunSnapshot): RelationshipGraphData {
  const aggregated = new Map<string, AggregatedLink>()

  for (const conversation of snapshot.conversations) {
    const participants = [...new Set(conversation.participants)]
    for (let firstIndex = 0; firstIndex < participants.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < participants.length; secondIndex += 1) {
        const first = participants[firstIndex]
        const second = participants[secondIndex]
        const key = pairKey(first, second)
        const previous = aggregated.get(key)
        aggregated.set(key, {
          source: previous?.source ?? first,
          target: previous?.target ?? second,
          conversationCount: (previous?.conversationCount ?? 0) + 1,
          active: Boolean(previous?.active || conversation.status === 'open'),
        })
      }
    }
  }

  const degree = new Map<string, number>()
  for (const link of aggregated.values()) {
    degree.set(link.source, (degree.get(link.source) ?? 0) + link.conversationCount)
    degree.set(link.target, (degree.get(link.target) ?? 0) + link.conversationCount)
  }

  return {
    nodes: snapshot.actors.map((actor) => ({
      id: actor.actorId,
      label: actor.kind === 'player' ? '你' : actor.name,
      role: actor.role,
      kind: actor.kind,
      color: actor.kind === 'player' ? '#a86f4b' : '#667a5a',
      nodeValue: (actor.kind === 'player' ? 8 : 6) + Math.min(degree.get(actor.actorId) ?? 0, 4),
    })),
    links: [...aggregated.values()].map((link) => ({
      ...link,
      label: `${link.conversationCount} 次共同聊天${link.active ? ' · 正在进行' : ''}`,
    })),
  }
}
