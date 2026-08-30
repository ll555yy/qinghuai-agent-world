import type { RunSnapshot } from '../api/types'

export type BookstoreRoomId = 'front' | 'study'

export interface BookstoreRoomDefinition {
  id: BookstoreRoomId
  name: string
  description: string
}

export interface BookstoreDoorway {
  id: string
  kind: 'interior'
  fromRoom: BookstoreRoomId
  toRoom: BookstoreRoomId
  label: string
}

/** The two player-facing rooms in the continuous bookstore map. */
export const BOOKSTORE_ROOMS: Record<BookstoreRoomId, BookstoreRoomDefinition> = {
  front: {
    id: 'front',
    name: '前厅旧书店',
    description: '入口、公共阅读桌、公告板与经营柜台',
  },
  study: {
    id: 'study',
    name: '后书房',
    description: '书法桌、绘画角、古籍修复台与茶席',
  },
}

/** There is intentionally one wide central connection, not a second exit. */
export const BOOKSTORE_DOORWAYS: readonly BookstoreDoorway[] = [
  {
    id: 'front-study-doorway',
    kind: 'interior',
    fromRoom: 'front',
    toRoom: 'study',
    label: '中央门洞',
  },
]

/** Stable home rooms for the five NPCs and the player. */
export const DEFAULT_ACTOR_ROOMS: Record<string, BookstoreRoomId> = {
  npc_001: 'study',
  npc_002: 'study',
  npc_003: 'front',
  npc_004: 'study',
  npc_005: 'front',
  player_001: 'front',
}

export const NPC_ROOM_CAPACITY = 3

/**
 * Maps the server's compact logical grid to a room.  The backend defaults are
 * x=0/2/6 in the study and x=4/8 in the front hall; y>=1 is open front-hall
 * floor (including the player's x=1,y=2 anchor).  Unknown x values choose the
 * nearest side so movement events remain visible without changing state.
 */
export function roomForPosition(position: { x: number; y: number }): BookstoreRoomId {
  if (position.y >= 1) return 'front'
  const x = Math.round(position.x)
  if (x === 0 || x === 2 || x === 6) return 'study'
  if (x === 4 || x === 8) return 'front'
  return x <= 3 ? 'study' : 'front'
}

export function roomForActor(actorId: string): BookstoreRoomId {
  return DEFAULT_ACTOR_ROOMS[actorId] ?? 'front'
}

/** Count only NPCs; the player's presence does not consume the NPC capacity. */
export function roomNpcCounts(snapshot: RunSnapshot): Record<BookstoreRoomId, number> {
  const counts: Record<BookstoreRoomId, number> = { front: 0, study: 0 }
  for (const actor of snapshot.actors) {
    if (actor.kind !== 'npc') continue
    const position = snapshot.actorStates[actor.actorId]?.position
    const room = position ? roomForPosition(position) : roomForActor(actor.actorId)
    counts[room] += 1
  }
  return counts
}
