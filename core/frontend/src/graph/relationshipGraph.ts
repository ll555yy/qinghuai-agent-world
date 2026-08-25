import { PLAYER_ACTOR_ID, type RunSnapshot } from '../api/types'

/** The two views supported by the relationship graph. */
export type RelationshipGraphFilter = 'all' | 'player'

export type RelationshipNodeGroup = 'player' | 'npc'

/**
 * Keep the initial force-graph layout independent from snapshot array order.
 *
 * The server currently exposes at most five NPCs.  Keeping five fixed slots
 * means an NPC does not move simply because another NPC becomes visible in a
 * later snapshot.  Additional NPCs are placed on outer rings while retaining
 * the same five angular slots.
 */
export const RELATIONSHIP_LAYOUT = {
  player: { fx: 0, fy: 0 },
  npcRingRadius: 105,
  npcRingSlots: 5,
} as const

export interface RelationshipNode {
  id: string
  label: string
  role: string
  kind: string
  group: RelationshipNodeGroup
  color: string
  nodeValue: number
  /** Deterministic initial coordinates consumed by react-force-graph. */
  fx: number
  fy: number
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
  const [source, target] = first <= second ? [first, second] : [second, first]
  // JSON avoids collisions for actor IDs that themselves contain "::".
  return JSON.stringify([source, target])
}

function npcInitialPosition(index: number): { fx: number; fy: number } {
  const { npcRingRadius, npcRingSlots } = RELATIONSHIP_LAYOUT
  const ring = Math.floor(index / npcRingSlots)
  const slot = index % npcRingSlots
  const angle = -Math.PI / 2 + (slot * 2 * Math.PI) / npcRingSlots
  const radius = npcRingRadius * (ring + 1)

  return {
    fx: radius * Math.cos(angle),
    fy: radius * Math.sin(angle),
  }
}

function compareActorIds(first: { actorId: string }, second: { actorId: string }): number {
  return first.actorId < second.actorId ? -1 : first.actorId > second.actorId ? 1 : 0
}

function cloneRelationshipGraph(data: RelationshipGraphData): RelationshipGraphData {
  return {
    nodes: data.nodes.map((node) => ({ ...node })),
    links: data.links.map((link) => ({ ...link })),
  }
}

/**
 * Return either the complete public graph or only direct player
 * relationships.  The function never mutates its argument, which is
 * important because force-graph may annotate node objects during rendering.
 */
export function filterRelationshipGraph(
  data: RelationshipGraphData,
  mode: RelationshipGraphFilter = 'all',
): RelationshipGraphData {
  if (mode !== 'player') return cloneRelationshipGraph(data)

  const playerIds = new Set(
    data.nodes
      .filter((node) => node.group === 'player' || node.kind === 'player' || node.id === PLAYER_ACTOR_ID)
      .map((node) => node.id),
  )
  const relatedIds = new Set(playerIds)
  const links = data.links.filter((link) => {
    const related = playerIds.has(link.source) || playerIds.has(link.target)
    if (related) {
      relatedIds.add(link.source)
      relatedIds.add(link.target)
    }
    return related
  })

  return {
    nodes: data.nodes.filter((node) => relatedIds.has(node.id)).map((node) => ({ ...node })),
    links: links.map((link) => ({ ...link })),
  }
}

/** Build only the relationship history the player has actually observed. */
export function buildRelationshipGraph(snapshot: RunSnapshot): RelationshipGraphData {
  const aggregated = new Map<string, AggregatedLink>()
  const publicActorIds = new Set(snapshot.actors.map((actor) => actor.actorId))

  for (const conversation of snapshot.conversations) {
    const participants = [...new Set(conversation.participants)]
      .filter((actorId) => publicActorIds.has(actorId))
    for (let firstIndex = 0; firstIndex < participants.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < participants.length; secondIndex += 1) {
        const first = participants[firstIndex]
        const second = participants[secondIndex]
        const key = pairKey(first, second)
        const previous = aggregated.get(key)
        aggregated.set(key, {
          // Keep the first observed direction for API compatibility.  The
          // edge is semantically undirected, while callers may still rely on
          // the historical participant order in source/target.
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

  const actors = [
    ...snapshot.actors.filter((actor) => actor.kind === 'player').sort(compareActorIds),
    ...snapshot.actors.filter((actor) => actor.kind !== 'player').sort(compareActorIds),
  ]
  let npcIndex = 0

  return {
    nodes: actors.map((actor) => {
      const group: RelationshipNodeGroup = actor.kind === 'player' ? 'player' : 'npc'
      const position = group === 'player' ? RELATIONSHIP_LAYOUT.player : npcInitialPosition(npcIndex++)
      return {
        id: actor.actorId,
        label: group === 'player' ? '你' : actor.name,
        role: actor.role,
        kind: actor.kind,
        group,
        color: group === 'player' ? '#a86f4b' : '#667a5a',
        nodeValue: (group === 'player' ? 8 : 6) + Math.min(degree.get(actor.actorId) ?? 0, 4),
        ...position,
      }
    }),
    links: [...aggregated.values()].map((link) => ({
      ...link,
      label: `${link.conversationCount} 次共同聊天${link.active ? ' · 正在进行' : ''}`,
    })),
  }
}
