/**
 * Tilemap layout for the two-room bookstore scene.
 *
 * The map is a 55x33 grid of 16px tiles (880x528 scene pixels). It is split
 * into three data sets so the renderer and the collision system stay in sync:
 *
 *   - ground:    floors, walls, rugs – always rendered behind actors
 *   - furniture: shelves, tables, counters … – rendered as individual sprites
 *                with y-sorted depth (Stardew Valley style occlusion)
 *   - overlays:  small props drawn on top of furniture (teapot on a desk…)
 *
 * Tile indices are 0-based positions inside tileset.png (8 tiles per row).
 * `putTilesAt` uses these indices directly; 0 is the transparent tile.
 */

export const TILE_SIZE = 16
export const MAP_COLS = 55 // 55 * 16 = 880px
export const MAP_ROWS = 33 // 33 * 16 = 528px

// Row 0 – floors
export const T_EMPTY = 0
export const T_STONE = 1
export const T_STONE_DARK = 2
export const T_WOOD = 3
export const T_WOOD_DARK = 4
export const T_STONE_WARM = 5
export const T_STONE_COOL = 6
export const T_WOOD_LIGHT = 7
// Row 1 – walls
export const T_WALL_TOP = 8
export const T_WALL_BODY = 9
export const T_WALL_CORNER = 10
export const T_DIVIDER = 11
export const T_DOORWAY = 12
export const T_WINDOW = 13
export const T_POSTER = 14
export const T_WALL_LANTERN = 15
// Row 2 – furniture
export const T_BOOKSHELF = 16
export const T_TABLE = 17
export const T_CHAIR = 18
export const T_PLANT = 19
export const T_LANTERN = 20
export const T_COUNTER = 21
export const T_RUG = 22
export const T_SCROLL_DESK = 23
// Row 3 – decor
export const T_POTTED_PLANT_LG = 24
export const T_HANGING_LANTERN = 25
export const T_WALL_CLOCK = 26
export const T_OPEN_BOOK = 27
export const T_TEA_SET = 28
export const T_INK_SET = 29
export const T_CUSHION = 30
export const T_FAN = 31

export interface TileOverlay {
  col: number
  row: number
  tile: number
}

export interface BookstoreTilemapData {
  ground: number[][]
  furniture: number[][]
  overlays: TileOverlay[]
}

export interface TileRect {
  left: number
  right: number
  top: number
  bottom: number
}

/** Furniture that blocks actor feet. Rugs/cushions/overlays stay walkable.
 *  Hanging lanterns hang above head height, so they never block the floor. */
export const SOLID_FURNITURE: ReadonlySet<number> = new Set([
  T_BOOKSHELF, T_TABLE, T_CHAIR, T_PLANT, T_LANTERN, T_COUNTER,
  T_SCROLL_DESK, T_POTTED_PLANT_LG, T_WALL_CLOCK, T_TEA_SET,
])

/** Interior bounds for the actor foot centre (walls excluded). */
export const TILEMAP_BOUNDS: TileRect = { left: 32, right: 810, top: 64, bottom: 480 }

/**
 * Walkable polygons: study floor, the central divider doorway and the front
 * hall floor. The doorway rect deliberately overlaps both rooms so A* can
 * cross the divider without leaving walkable space.
 */
export const TILEMAP_WALKABLE_AREAS: readonly TileRect[] = [
  { left: 36, right: 806, top: 68, bottom: 252 },
  { left: 400, right: 480, top: 248, bottom: 296 },
  // Top edge clears the divider's painted base beam (y 283-293) so actors
  // never stand with their feet visually inside the woodwork.
  { left: 36, right: 806, top: 296, bottom: 476 },
]

/** Deterministic floor variation so tiles do not look machine stamped. */
function floorVariant(base: number, variants: readonly number[], col: number, row: number): number {
  const hash = (col * 31 + row * 17 + col * row * 7) % 13
  return hash === 0 ? variants[(col + row) % variants.length] : base
}

export function buildBookstoreMap(): BookstoreTilemapData {
  const ground: number[][] = Array.from({ length: MAP_ROWS }, () => new Array<number>(MAP_COLS).fill(T_EMPTY))
  const furniture: number[][] = Array.from({ length: MAP_ROWS }, () => new Array<number>(MAP_COLS).fill(T_EMPTY))
  const overlays: TileOverlay[] = []

  const setG = (col: number, row: number, tile: number) => {
    if (row >= 0 && row < MAP_ROWS && col >= 0 && col < MAP_COLS) ground[row][col] = tile
  }
  const setF = (col: number, row: number, tile: number) => {
    if (row >= 0 && row < MAP_ROWS && col >= 0 && col < MAP_COLS) furniture[row][col] = tile
  }
  const hLineG = (row: number, c1: number, c2: number, tile: number) => {
    for (let c = c1; c <= c2; c++) setG(c, row, tile)
  }
  const fillG = (r1: number, c1: number, r2: number, c2: number, tile: number) => {
    for (let r = r1; r <= r2; r++) hLineG(r, c1, c2, tile)
  }
  const fillF = (r1: number, c1: number, r2: number, c2: number, tile: number) => {
    for (let r = r1; r <= r2; r++) for (let c = c1; c <= c2; c++) setF(c, r, tile)
  }

  // ── Floors with scattered variants ──────────────────────
  for (let r = 4; r <= 15; r++) {
    for (let c = 2; c <= 52; c++) setG(c, r, floorVariant(T_WOOD, [T_WOOD_DARK, T_WOOD_LIGHT], c, r))
  }
  for (let r = 18; r <= 29; r++) {
    for (let c = 2; c <= 52; c++) setG(c, r, floorVariant(T_STONE, [T_STONE_DARK, T_STONE_WARM, T_STONE_COOL], c, r))
  }

  // ── Walls ───────────────────────────────────────────────
  // Top wall: a deep plaster-and-timber band, like the approved background.
  hLineG(0, 0, MAP_COLS - 1, T_WALL_TOP)
  fillG(1, 0, 3, MAP_COLS - 1, T_WALL_BODY)
  // A broad central window, framed pictures and timber posts break up the
  // back wall instead of leaving one flat strip above an empty room.
  for (let c = 17; c <= 23; c++) setG(c, 2, T_WINDOW)
  for (const c of [13, 27, 38, 44]) setG(c, 2, T_POSTER)
  for (const c of [0, 15, 25, 40, 54]) {
    setG(c, 1, T_WALL_CORNER); setG(c, 2, T_WALL_CORNER); setG(c, 3, T_WALL_CORNER)
  }
  setG(16, 2, T_WALL_LANTERN); setG(24, 2, T_WALL_LANTERN); setG(47, 2, T_WALL_LANTERN)
  // Side walls.
  fillG(4, 0, 29, 1, T_WALL_BODY)
  fillG(4, 53, 29, 54, T_WALL_BODY)
  setG(0, 4, T_WALL_CORNER); setG(54, 4, T_WALL_CORNER)
  setG(0, 29, T_WALL_CORNER); setG(54, 29, T_WALL_CORNER)
  // Divider wall with a five-tile central doorway (x 400-480).
  hLineG(16, 2, 24, T_DIVIDER); hLineG(16, 30, 52, T_DIVIDER)
  hLineG(17, 2, 24, T_DIVIDER); hLineG(17, 30, 52, T_DIVIDER)
  hLineG(16, 25, 29, T_DOORWAY); hLineG(17, 25, 29, T_DOORWAY)
  // One-cell low south wall: it defines the boundary without blocking the
  // 2.5D view. The five-cell centre remains the front entrance.
  hLineG(30, 0, 24, T_WALL_BODY)
  hLineG(30, 30, 54, T_WALL_BODY)
  hLineG(30, 25, 29, T_DOORWAY)
  setG(0, 30, T_WALL_CORNER); setG(54, 30, T_WALL_CORNER)

  // ── Rugs & floor decor (walkable) ───────────────────────
  fillG(7, 6, 11, 14, T_RUG)      // study tea rug
  setG(14, 10, T_CUSHION)
  // Seat cushions around the tea table (outside the rug so they stay visible).
  setG(5, 8, T_CUSHION); setG(5, 10, T_CUSHION)
  setG(15, 8, T_CUSHION); setG(15, 10, T_CUSHION)
  // Study aisle gathering rug: conversations anchor here instead of floating
  // on bare floor, like the woven mats in Stardew's community centre.
  fillG(12, 20, 14, 34, T_RUG)
  fillG(21, 21, 26, 33, T_RUG)    // front reading rug under the big table
  fillG(28, 25, 29, 29, T_RUG)    // welcome mat at the entrance

  // ── Study furniture ─────────────────────────────────────
  // Tall cabinets across the back wall create the dense, lived-in study
  // silhouette of the original scene while keeping the centre walkable.
  fillF(3, 4, 5, 11, T_BOOKSHELF)
  fillF(3, 29, 5, 36, T_BOOKSHELF)
  fillF(3, 42, 5, 49, T_BOOKSHELF)
  fillF(4, 2, 13, 3, T_BOOKSHELF)      // left wall shelves
  fillF(4, 51, 13, 52, T_BOOKSHELF)    // right wall shelves
  fillF(8, 7, 10, 12, T_TEA_SET)       // low tea table on the rug
  fillF(8, 23, 10, 31, T_SCROLL_DESK)  // long calligraphy desk
  setF(27, 11, T_CHAIR)                // desk chair
  fillF(8, 41, 10, 48, T_COUNTER)      // writing desk against the wall
  setF(44, 11, T_CHAIR)
  setF(49, 3, T_WALL_CLOCK)
  setF(13, 4, T_PLANT); setF(39, 4, T_PLANT)
  setF(50, 5, T_PLANT)
  setF(5, 14, T_POTTED_PLANT_LG); setF(48, 14, T_POTTED_PLANT_LG)
  setF(15, 2, T_HANGING_LANTERN); setF(38, 2, T_HANGING_LANTERN)

  // ── Front furniture ─────────────────────────────────────
  fillF(19, 3, 21, 13, T_BOOKSHELF)    // left shelves, upper run
  fillF(25, 3, 27, 13, T_BOOKSHELF)    // left shelves, lower run
  fillF(19, 44, 20, 50, T_BOOKSHELF)   // display shelves top-right
  fillF(22, 22, 24, 31, T_TABLE)       // central reading table
  // Chairs sit at the table ends and below it (like the approved artwork).
  // The top stays open so the horizontal aisle above the table is never
  // pinched shut by collision clearance.
  setF(20, 22, T_CHAIR); setF(20, 24, T_CHAIR)
  setF(33, 22, T_CHAIR); setF(33, 24, T_CHAIR)
  setF(26, 25, T_CHAIR); setF(30, 25, T_CHAIR)
  fillF(22, 38, 23, 48, T_COUNTER)     // service counter
  setF(39, 21, T_LANTERN)              // lantern above the counter edge
  fillF(26, 32, 29, 36, T_BOOKSHELF)   // display shelves bottom-center
  setF(16, 18, T_PLANT); setF(40, 18, T_PLANT)
  setF(16, 29, T_POTTED_PLANT_LG); setF(40, 29, T_POTTED_PLANT_LG)
  setF(20, 18, T_HANGING_LANTERN); setF(35, 18, T_HANGING_LANTERN)

  // ── Overlays: small props sitting on furniture ──────────
  overlays.push({ col: 29, row: 8, tile: T_INK_SET })    // inkstone on scroll desk
  overlays.push({ col: 44, row: 8, tile: T_OPEN_BOOK })  // book on writing desk
  overlays.push({ col: 25, row: 23, tile: T_OPEN_BOOK }) // open book, big table
  overlays.push({ col: 29, row: 23, tile: T_TEA_SET })   // tea on the reading table
  overlays.push({ col: 46, row: 22, tile: T_FAN })       // fan on the counter

  return { ground, furniture, overlays }
}

/**
 * Collision rectangles derived from the furniture grid. Horizontal runs of
 * solid tiles merge into one rect per run, which keeps the A* obstacle list
 * short while matching the rendered furniture exactly.
 */
export function buildTilemapObstacles(): TileRect[] {
  const { furniture } = buildBookstoreMap()
  const rects: TileRect[] = []
  for (let row = 0; row < MAP_ROWS; row += 1) {
    let start = -1
    for (let col = 0; col <= MAP_COLS; col += 1) {
      const solid = col < MAP_COLS && SOLID_FURNITURE.has(furniture[row][col])
      if (solid && start === -1) start = col
      if (!solid && start !== -1) {
        rects.push({ left: start * TILE_SIZE, right: col * TILE_SIZE, top: row * TILE_SIZE, bottom: (row + 1) * TILE_SIZE })
        start = -1
      }
    }
  }
  return rects
}
