import {
  buildTilemapObstacles,
  TILEMAP_BOUNDS,
  TILEMAP_WALKABLE_AREAS,
} from './bookstoreTilemap'

export interface NavigationPoint {
  x: number
  y: number
}

export interface NavigationRect {
  left: number
  right: number
  top: number
  bottom: number
}

export interface BookstorePathOptions {
  clearance?: number
  clearanceX?: number
  clearanceY?: number
  obstacles?: readonly NavigationRect[]
}

const GRID_SIZE = 14
const DEFAULT_ACTOR_CLEARANCE = 10
export const BOOKSTORE_ACTOR_CLEARANCE = 20
export const BOOKSTORE_ACTOR_CLEARANCE_Y = 36
// Bounds represent the actor's foot centre, inset from the painted outer wall
// by enough room for the full sprite body.
const MAP_BOUNDS = TILEMAP_BOUNDS

/**
 * Collision footprints derived directly from the tilemap furniture grid, so
 * rendered furniture and pathfinding obstacles can never drift apart. They
 * cover furniture bases rather than decorative upper pixels so actors can
 * still appear visually behind shelves while their feet remain on floor.
 */
export const BOOKSTORE_OBSTACLES: readonly NavigationRect[] = buildTilemapObstacles()

/** Only the central door connects the two walkable room polygons. */
export const BOOKSTORE_WALKABLE_AREAS: readonly NavigationRect[] = TILEMAP_WALKABLE_AREAS

const ACTOR_SLOT_OFFSETS: readonly NavigationPoint[] = [
  { x: 0, y: 0 },
  { x: -56, y: 0 }, { x: 56, y: 0 }, { x: 0, y: -56 }, { x: 0, y: 56 },
  { x: -40, y: -40 }, { x: 40, y: -40 }, { x: -40, y: 40 }, { x: 40, y: 40 },
  { x: -112, y: 0 }, { x: 112, y: 0 }, { x: 0, y: -112 }, { x: 0, y: 112 },
  { x: -80, y: -80 }, { x: 80, y: -80 }, { x: -80, y: 80 }, { x: 80, y: 80 },
]

function inside(point: NavigationPoint, rect: NavigationRect, inset = 0): boolean {
  return point.x >= rect.left + inset && point.x <= rect.right - inset && point.y >= rect.top + inset && point.y <= rect.bottom - inset
}

function insideExpanded(point: NavigationPoint, rect: NavigationRect, amountX: number, amountY: number): boolean {
  return point.x >= rect.left - amountX && point.x <= rect.right + amountX
    && point.y >= rect.top - amountY && point.y <= rect.bottom + amountY
}

export function isBookstoreWalkable(
  point: NavigationPoint,
  options: BookstorePathOptions = {},
): boolean {
  // The navigation point is the centre of the actor's feet, not the whole
  // sprite. Keep enough room for the visible body near furniture edges.
  const clearanceX = options.clearanceX ?? options.clearance ?? DEFAULT_ACTOR_CLEARANCE
  const clearanceY = options.clearanceY ?? options.clearance ?? DEFAULT_ACTOR_CLEARANCE
  const obstacles = options.obstacles ?? BOOKSTORE_OBSTACLES
  if (!inside(point, MAP_BOUNDS)) return false
  if (!BOOKSTORE_WALKABLE_AREAS.some((area) => inside(point, area))) return false
  return !obstacles.some((obstacle) => insideExpanded(point, obstacle, clearanceX, clearanceY))
}

function gridPoint(column: number, row: number): NavigationPoint {
  return {
    x: MAP_BOUNDS.left + column * GRID_SIZE,
    y: MAP_BOUNDS.top + row * GRID_SIZE,
  }
}

function gridKey(column: number, row: number): string {
  return `${column}:${row}`
}

function parseGridKey(key: string): { column: number; row: number } {
  const [column, row] = key.split(':').map(Number)
  return { column, row }
}

function nearestWalkableGrid(point: NavigationPoint, options: BookstorePathOptions): { column: number; row: number } | null {
  const maxColumn = Math.floor((MAP_BOUNDS.right - MAP_BOUNDS.left) / GRID_SIZE)
  const maxRow = Math.floor((MAP_BOUNDS.bottom - MAP_BOUNDS.top) / GRID_SIZE)
  const originColumn = Math.round((point.x - MAP_BOUNDS.left) / GRID_SIZE)
  const originRow = Math.round((point.y - MAP_BOUNDS.top) / GRID_SIZE)

  for (let radius = 0; radius <= 8; radius += 1) {
    for (let rowOffset = -radius; rowOffset <= radius; rowOffset += 1) {
      for (let columnOffset = -radius; columnOffset <= radius; columnOffset += 1) {
        if (Math.max(Math.abs(columnOffset), Math.abs(rowOffset)) !== radius) continue
        const column = originColumn + columnOffset
        const row = originRow + rowOffset
        if (column < 0 || column > maxColumn || row < 0 || row > maxRow) continue
        if (isBookstoreWalkable(gridPoint(column, row), options)) return { column, row }
      }
    }
  }
  return null
}

export function nearestBookstoreWalkablePoint(
  point: NavigationPoint,
  options: BookstorePathOptions = {},
): NavigationPoint {
  const cell = nearestWalkableGrid(point, options)
  return cell ? gridPoint(cell.column, cell.row) : point
}

function clampToRange(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

/** Pick a collision-free conversation slot near an authoritative position. */
export function findBookstoreActorSlot(
  preferred: NavigationPoint,
  roomBounds: NavigationRect,
  occupied: readonly NavigationPoint[],
  minimumDistance = 52,
): NavigationPoint {
  const base = isBookstoreWalkable(preferred) ? preferred : nearestBookstoreWalkablePoint(preferred)
  for (const offset of ACTOR_SLOT_OFFSETS) {
    const candidate = {
      x: clampToRange(base.x + offset.x, roomBounds.left, roomBounds.right),
      y: clampToRange(base.y + offset.y, roomBounds.top, roomBounds.bottom),
    }
    if (!isBookstoreWalkable(candidate, {
      clearanceX: BOOKSTORE_ACTOR_CLEARANCE,
      clearanceY: BOOKSTORE_ACTOR_CLEARANCE_Y,
    })) continue
    if (occupied.every((other) => Math.hypot(candidate.x - other.x, candidate.y - other.y) >= minimumDistance)) return candidate
  }

  // Fixed rings are fast for the normal two/three-person case. When several
  // actors approach the same compact server coordinate, scan the room floor
  // deterministically instead of returning an already occupied fallback.
  const roomCandidates: NavigationPoint[] = []
  for (let y = roomBounds.top; y <= roomBounds.bottom; y += GRID_SIZE) {
    for (let x = roomBounds.left; x <= roomBounds.right; x += GRID_SIZE) {
      const candidate = { x, y }
      if (isBookstoreWalkable(candidate, {
        clearanceX: BOOKSTORE_ACTOR_CLEARANCE,
        clearanceY: BOOKSTORE_ACTOR_CLEARANCE_Y,
      })) roomCandidates.push(candidate)
    }
  }
  roomCandidates.sort((first, second) => {
    const firstDistance = (first.x - base.x) ** 2 + (first.y - base.y) ** 2
    const secondDistance = (second.x - base.x) ** 2 + (second.y - base.y) ** 2
    return firstDistance - secondDistance || first.y - second.y || first.x - second.x
  })
  const available = roomCandidates.find((candidate) => occupied.every(
    (other) => Math.hypot(candidate.x - other.x, candidate.y - other.y) >= minimumDistance,
  ))
  if (available) return available
  return base
}

function manhattan(first: { column: number; row: number }, second: { column: number; row: number }): number {
  return Math.abs(first.column - second.column) + Math.abs(first.row - second.row)
}

function reconstructPath(cameFrom: Map<string, string>, finalKey: string): NavigationPoint[] {
  const reversed: NavigationPoint[] = []
  let currentKey: string | undefined = finalKey
  while (currentKey) {
    const { column, row } = parseGridKey(currentKey)
    reversed.push(gridPoint(column, row))
    currentKey = cameFrom.get(currentKey)
  }
  return reversed.reverse()
}

function compressOrthogonalPath(points: readonly NavigationPoint[]): NavigationPoint[] {
  if (points.length <= 2) return [...points]
  const result: NavigationPoint[] = [points[0]]
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    const next = points[index + 1]
    const sameHorizontal = previous.y === current.y && current.y === next.y
    const sameVertical = previous.x === current.x && current.x === next.x
    if (!sameHorizontal && !sameVertical) result.push(current)
  }
  result.push(points[points.length - 1])
  return result
}

function addEndpointBridge(
  points: NavigationPoint[],
  endpoint: NavigationPoint,
  atStart: boolean,
  options: BookstorePathOptions,
): NavigationPoint[] {
  if (!points.length) return [endpoint]
  const anchor = atStart ? points[0] : points[points.length - 1]
  if (anchor.x === endpoint.x || anchor.y === endpoint.y) {
    return atStart ? [endpoint, ...points] : [...points, endpoint]
  }
  const horizontalFirst = { x: anchor.x, y: endpoint.y }
  const verticalFirst = { x: endpoint.x, y: anchor.y }
  if (isBookstoreWalkable(horizontalFirst, options)) {
    return atStart ? [endpoint, horizontalFirst, ...points] : [...points, horizontalFirst, endpoint]
  }
  if (isBookstoreWalkable(verticalFirst, options)) {
    return atStart ? [endpoint, verticalFirst, ...points] : [...points, verticalFirst, endpoint]
  }
  // Dense furniture can make both L-shaped corners unwalkable. Snap the
  // endpoint onto the route's first/last row or column instead of emitting a
  // corner point inside a shelf or in the wall gap between rooms.
  const rowStub = { x: endpoint.x, y: anchor.y }
  const colStub = { x: anchor.x, y: endpoint.y }
  return atStart
    ? [endpoint, colStub, ...points]
    : [...points, rowStub, endpoint]
}

/**
 * Four-direction A* pathfinding over the bookstore floor.  The returned route
 * contains only orthogonal segments, must cross the central doorway between
 * rooms, and keeps actor feet outside furniture collision footprints.
 */
export function findBookstorePath(
  start: NavigationPoint,
  end: NavigationPoint,
  options: BookstorePathOptions = {},
): NavigationPoint[] {
  if (Math.hypot(end.x - start.x, end.y - start.y) < 1) return [start]
  const startCell = nearestWalkableGrid(start, options)
  const endCell = nearestWalkableGrid(end, options)
  if (!startCell || !endCell) return [start, end]

  const startKey = gridKey(startCell.column, startCell.row)
  const endKey = gridKey(endCell.column, endCell.row)
  const open = new Set([startKey])
  const cameFrom = new Map<string, string>()
  const gScore = new Map<string, number>([[startKey, 0]])
  const fScore = new Map<string, number>([[startKey, manhattan(startCell, endCell)]])
  const directions = [
    { column: 1, row: 0 },
    { column: -1, row: 0 },
    { column: 0, row: 1 },
    { column: 0, row: -1 },
  ]

  while (open.size) {
    let currentKey = ''
    let currentScore = Number.POSITIVE_INFINITY
    for (const candidate of open) {
      const score = fScore.get(candidate) ?? Number.POSITIVE_INFINITY
      if (score < currentScore || (score === currentScore && candidate < currentKey)) {
        currentKey = candidate
        currentScore = score
      }
    }
    if (currentKey === endKey) {
      let route = compressOrthogonalPath(reconstructPath(cameFrom, currentKey))
      route = addEndpointBridge(route, start, true, options)
      route = addEndpointBridge(route, end, false, options)
      return compressOrthogonalPath(route)
    }

    open.delete(currentKey)
    const current = parseGridKey(currentKey)
    for (const direction of directions) {
      const neighbor = {
        column: current.column + direction.column,
        row: current.row + direction.row,
      }
      const point = gridPoint(neighbor.column, neighbor.row)
      if (!isBookstoreWalkable(point, options)) continue
      const neighborKey = gridKey(neighbor.column, neighbor.row)
      const tentative = (gScore.get(currentKey) ?? Number.POSITIVE_INFINITY) + 1
      if (tentative >= (gScore.get(neighborKey) ?? Number.POSITIVE_INFINITY)) continue
      cameFrom.set(neighborKey, currentKey)
      gScore.set(neighborKey, tentative)
      fScore.set(neighborKey, tentative + manhattan(neighbor, endCell))
      open.add(neighborKey)
    }
  }

  // A person standing in a narrow aisle can temporarily make the dynamic
  // occupancy grid unsolvable. Retry against permanent furniture only; this
  // is preferable to cutting through a wall or flying diagonally.
  if (options.obstacles && options.obstacles !== BOOKSTORE_OBSTACLES) {
    return findBookstorePath(start, end, { ...options, obstacles: BOOKSTORE_OBSTACLES })
  }

  // Fail closed if a future background revision disconnects the static map.
  return [start]
}
