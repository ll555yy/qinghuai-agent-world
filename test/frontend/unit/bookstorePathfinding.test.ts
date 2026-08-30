import {
  BOOKSTORE_ACTOR_CLEARANCE,
  BOOKSTORE_ACTOR_CLEARANCE_Y,
  BOOKSTORE_OBSTACLES,
  findBookstoreActorSlot,
  findBookstorePath,
  isBookstoreWalkable,
  nearestBookstoreWalkablePoint,
} from '../../../core/frontend/src/game/bookstorePathfinding'

function expectOrthogonal(path: readonly { x: number; y: number }[]) {
  for (let index = 1; index < path.length; index += 1) {
    const previous = path[index - 1]
    const current = path[index]
    expect(current.x === previous.x || current.y === previous.y).toBe(true)
  }
}

describe('bookstorePathfinding', () => {
  it('recognizes furniture, divider walls and the only central doorway', () => {
    expect(isBookstoreWalkable({ x: 400, y: 350 })).toBe(false)
    expect(isBookstoreWalkable({ x: 300, y: 254 })).toBe(false)
    expect(isBookstoreWalkable({ x: 440, y: 254 })).toBe(true)
    expect(isBookstoreWalkable({ x: 280, y: 190 })).toBe(true)
    expect(isBookstoreWalkable({ x: 440, y: 454 })).toBe(true)
    expect(isBookstoreWalkable({ x: 230, y: 454 })).toBe(false)
    expect(isBookstoreWalkable({ x: 810, y: 340 })).toBe(false)
    expect(isBookstoreWalkable({ x: 550, y: 430 })).toBe(false)
    expect(isBookstoreWalkable({ x: 300, y: 420 })).toBe(true)
  })

  it('routes between rooms through the doorway without diagonal flight', () => {
    const path = findBookstorePath({ x: 280, y: 190 }, { x: 440, y: 454 })

    expect(path.length).toBeGreaterThan(4)
    expect(path[0]).toEqual({ x: 280, y: 190 })
    expect(path.at(-1)).toEqual({ x: 440, y: 454 })
    expect(path.some((point) => point.x >= 400 && point.x <= 480 && point.y >= 220 && point.y <= 296)).toBe(true)
    expectOrthogonal(path)
    for (const point of path.slice(1, -1)) expect(isBookstoreWalkable(point)).toBe(true)
  })

  it('walks around the central reading table inside the front room', () => {
    const path = findBookstorePath({ x: 440, y: 454 }, { x: 240, y: 340 })

    expect(path.length).toBeGreaterThan(2)
    expectOrthogonal(path)
    for (const point of path.slice(1, -1)) expect(isBookstoreWalkable(point)).toBe(true)
  })

  it('uses temporary actor footprints as dynamic collision obstacles', () => {
    const actorObstacle = { left: 418, right: 462, top: 274, bottom: 310 }
    const path = findBookstorePath(
      { x: 230, y: 286 },
      { x: 560, y: 286 },
      { obstacles: [...BOOKSTORE_OBSTACLES, actorObstacle] },
    )

    expectOrthogonal(path)
    expect(path.some((point) => point.y !== 286)).toBe(true)
    for (const point of path.slice(1, -1)) {
      expect(isBookstoreWalkable(point, { obstacles: [...BOOKSTORE_OBSTACLES, actorObstacle] })).toBe(true)
    }
  })

  it('moves an invalid furniture target to the nearest walkable floor cell', () => {
    const point = nearestBookstoreWalkablePoint({ x: 400, y: 350 })

    expect(point).not.toEqual({ x: 400, y: 350 })
    expect(isBookstoreWalkable(point)).toBe(true)
  })

  it('assigns three conversation participants distinct walkable slots', () => {
    const roomBounds = { left: 96, right: 830, top: 88, bottom: 210 }
    const preferred = { x: 280, y: 190 }
    const occupied: Array<{ x: number; y: number }> = []

    for (let index = 0; index < 3; index += 1) {
      occupied.push(findBookstoreActorSlot(preferred, roomBounds, occupied))
    }

    expect(new Set(occupied.map(({ x, y }) => `${x}:${y}`)).size).toBe(3)
    expect(occupied.every((point) => isBookstoreWalkable(point))).toBe(true)
    expect(Math.hypot(occupied[0].x - occupied[1].x, occupied[0].y - occupied[1].y)).toBeLessThanOrEqual(60)
    for (let first = 0; first < occupied.length; first += 1) {
      for (let second = first + 1; second < occupied.length; second += 1) {
        expect(Math.hypot(occupied[first].x - occupied[second].x, occupied[first].y - occupied[second].y)).toBeGreaterThanOrEqual(52)
      }
    }
  })

  it('keeps actor bodies clear of furniture at their final standing slots', () => {
    const slot = findBookstoreActorSlot(
      { x: 240, y: 340 },
      { left: 96, right: 780, top: 298, bottom: 470 },
      [],
    )

    expect(slot).not.toEqual({ x: 240, y: 340 })
    expect(isBookstoreWalkable(slot, {
      clearanceX: BOOKSTORE_ACTOR_CLEARANCE,
      clearanceY: BOOKSTORE_ACTOR_CLEARANCE_Y,
    })).toBe(true)
  })

  it('finds a free reachable approach point even when the preferred area is crowded', () => {
    const roomBounds = { left: 96, right: 830, top: 88, bottom: 210 }
    const occupied: Array<{ x: number; y: number }> = []

    for (let index = 0; index < 6; index += 1) {
      const slot = findBookstoreActorSlot({ x: 280, y: 190 }, roomBounds, occupied, 52)
      expect(isBookstoreWalkable(slot)).toBe(true)
      expect(occupied.every((other) => Math.hypot(slot.x - other.x, slot.y - other.y) >= 52)).toBe(true)
      expect(findBookstorePath({ x: 720, y: 205 }, slot).at(-1)).toEqual(slot)
      occupied.push(slot)
    }

    expect(new Set(occupied.map(({ x, y }) => `${x}:${y}`)).size).toBe(6)
  })

  it('returns the same replayable path for the same endpoints', () => {
    const start = { x: 720, y: 205 }
    const end = { x: 440, y: 454 }

    expect(findBookstorePath(start, end)).toEqual(findBookstorePath(start, end))
  })
})
