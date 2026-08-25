import { buildRelationshipGraph } from '../../../core/frontend/src/graph/relationshipGraph'
import {
  BOOKSTORE_DOORWAYS,
  BOOKSTORE_ROOMS,
  DEFAULT_ACTOR_ROOMS,
  NPC_ROOM_CAPACITY,
  roomForActor,
  roomForPosition,
  roomNpcCounts,
  type BookstoreRoomId,
} from '../../../core/frontend/src/game/roomLayout'

import { snapshot } from './fixtures'

const DEFAULT_POSITIONS = {
  npc_001: { x: 0, y: 0 },
  npc_002: { x: 2, y: 0 },
  npc_003: { x: 4, y: 0 },
  npc_004: { x: 6, y: 0 },
  npc_005: { x: 8, y: 0 },
  player_001: { x: 1, y: 2 },
} as const

function fiveNpcSnapshot() {
  const base = snapshot({
    actors: [
      ...snapshot().actors,
      { actorId: 'npc_003', kind: 'npc', name: '赵磊', role: '运营顾问', publicBackground: '负责老街运营。', publicImpression: ['利落'] },
      { actorId: 'npc_004', kind: 'npc', name: '陈月', role: '社区护士', publicBackground: '关注邻里健康。', publicImpression: ['细致'] },
      { actorId: 'npc_005', kind: 'npc', name: '周慎之', role: '旧书店店主', publicBackground: '守着一间旧书店。', publicImpression: ['稳重'] },
    ],
    actorStates: Object.fromEntries(
      Object.entries(DEFAULT_POSITIONS).map(([actorId, position]) => [actorId, { status: 'waiting', position }]),
    ),
  })
  return base
}

describe('two-room bookstore layout', () => {
  it('keeps exactly two named rooms and one interior connection', () => {
    const roomIds: BookstoreRoomId[] = ['front', 'study']
    expect(new Set(roomIds)).toEqual(new Set(['front', 'study']))
    expect(Object.keys(BOOKSTORE_ROOMS).sort()).toEqual(['front', 'study'])
    expect(BOOKSTORE_ROOMS.front.name).toBe('前厅旧书店')
    expect(BOOKSTORE_ROOMS.study.name).toBe('后书房')
    expect(BOOKSTORE_DOORWAYS).toHaveLength(1)

    const [doorway] = BOOKSTORE_DOORWAYS
    expect(doorway.kind).toBe('interior')
    expect(new Set([doorway.fromRoom, doorway.toRoom])).toEqual(new Set(roomIds))
  })

  it('maps task-role actors to stable default rooms through their backend anchors', () => {
    expect(DEFAULT_ACTOR_ROOMS).toMatchObject({
      npc_001: 'study',
      npc_002: 'study',
      npc_003: 'front',
      npc_004: 'study',
      npc_005: 'front',
      player_001: 'front',
    })

    for (const [actorId, room] of Object.entries(DEFAULT_ACTOR_ROOMS)) {
      expect(roomForActor(actorId)).toBe(room)
    }
  })

  it('keeps backend default anchors in their intended room and caps each room at three NPCs', () => {
    expect(roomForPosition(DEFAULT_POSITIONS.npc_001)).toBe('study')
    expect(roomForPosition(DEFAULT_POSITIONS.npc_002)).toBe('study')
    expect(roomForPosition(DEFAULT_POSITIONS.npc_004)).toBe('study')
    expect(roomForPosition(DEFAULT_POSITIONS.npc_003)).toBe('front')
    expect(roomForPosition(DEFAULT_POSITIONS.npc_005)).toBe('front')
    expect(roomForPosition(DEFAULT_POSITIONS.player_001)).toBe('front')

    const counts = roomNpcCounts(fiveNpcSnapshot())
    expect(counts).toEqual({ front: 2, study: 3 })
    expect(Math.max(...Object.values(counts))).toBeLessThanOrEqual(NPC_ROOM_CAPACITY)
  })

  it('classifies actors by their current position, so moving to one anchor shares its room', () => {
    const moved = fiveNpcSnapshot()
    moved.actorStates.npc_005 = { status: 'waiting', position: DEFAULT_POSITIONS.npc_002 }
    moved.actorStates.npc_003 = { status: 'waiting', position: DEFAULT_POSITIONS.npc_004 }

    expect(roomForPosition(moved.actorStates.npc_005.position)).toBe('study')
    expect(roomForPosition(moved.actorStates.npc_003.position)).toBe('study')
    expect(roomNpcCounts(moved)).toEqual({ front: 0, study: 5 })
  })

  it('does not rewrite conversation or public relationship data while assigning rooms', () => {
    const input = fiveNpcSnapshot()
    input.conversations = [{
      conversationId: 'public-chat',
      creationSeq: 1,
      participants: ['player_001', 'npc_001', 'npc_003'],
      status: 'open',
    }]
    input.currentWorldState = {
      publicRelationshipNote: '共同参与过书店讨论',
      privateRelationships: [{ source: 'npc_001', target: 'npc_003', label: '不可见' }],
    }
    const before = structuredClone(input)

    roomNpcCounts(input)
    const graph = buildRelationshipGraph(input)

    expect(input).toEqual(before)
    expect(graph.links).toHaveLength(3)
    expect(JSON.stringify(graph)).not.toContain('不可见')
  })
})
