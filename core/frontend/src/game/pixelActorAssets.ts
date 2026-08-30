/**
 * Runtime manifest for the full-body, 4x4 pixel actor sheets.
 *
 * The high-resolution source art stays under `project/visual-concepts`.
 * Browser-facing copies are nearest-neighbour runtime sheets: Lin and Zhou
 * use 360x360 (90x90 cells), while the player and other NPCs use 256x384
 * (64x96 cells). Named frames keep direction/action mapping explicit and let
 * the scene fall back cleanly when one texture fails to load.
 */

export const PIXEL_ACTOR_DIRECTIONS = ['down', 'up', 'left', 'right'] as const
export type PixelActorDirection = (typeof PIXEL_ACTOR_DIRECTIONS)[number]

export const PIXEL_ACTOR_ACTIONS = ['idle', 'walkA', 'pass', 'walkB'] as const
export type PixelActorAction = (typeof PIXEL_ACTOR_ACTIONS)[number]

/** Explicit row/column contract shared by all six sheets. */
export const PIXEL_ACTOR_FRAME_LAYOUT = {
  rows: { down: 0, up: 1, left: 2, right: 3 },
  columns: { idle: 0, walkA: 1, pass: 2, walkB: 3 },
} as const

export const PIXEL_ACTOR_IDS = ['player_001', 'npc_001', 'npc_002', 'npc_003', 'npc_004', 'npc_005'] as const
export type PixelActorId = (typeof PIXEL_ACTOR_IDS)[number]

export interface PixelActorSourceSize {
  readonly width: number
  readonly height: number
}

export interface PixelActorGrid {
  readonly columns: 4
  readonly rows: 4
}

/**
 * The target box and bottom anchor used by every full-body actor in the world
 * scene.  `originY` leaves a small amount of room below the feet for a ground
 * shadow or interaction ring while keeping actor positions on the same grid.
 */
export interface PixelActorDisplayLayout {
  readonly width: number
  readonly height: number
  readonly originX: number
  readonly originY: number
}

export interface PixelActorManifestEntry {
  readonly actorId: PixelActorId
  readonly slug: string
  readonly textureKey: string
  readonly url: string
  readonly sourceFile: string
  readonly sourceSize: PixelActorSourceSize
  readonly grid: PixelActorGrid
  readonly display: PixelActorDisplayLayout
  /** Main connected figure centre/feet for each row-major animation frame. */
  readonly frameAnchors: readonly PixelActorFrameAnchor[]
  /** Non-transparent bounds of the down/idle frame, relative to its cell. */
  readonly graphPortraitCrop: PixelActorFrameRect
}

export interface PixelActorFrameAnchor {
  readonly x: number
  readonly y: number
}

const TALL_PIXEL_DISPLAY: PixelActorDisplayLayout = {
  width: 44,
  height: 66,
  originX: 0.5,
  originY: 0.95,
}

const SQUARE_PIXEL_DISPLAY: PixelActorDisplayLayout = {
  width: 66,
  height: 66,
  originX: 0.5,
  originY: 0.95,
}

const PIXEL_GRID: PixelActorGrid = { columns: 4, rows: 4 }

/**
 * Every URL below is a versioned runtime derivative of the matching source
 * under `project/visual-concepts`. Existing portrait assets are intentionally
 * not replaced and remain available to dialogue/panel UI.
 */
export const PIXEL_ACTOR_ASSETS = {
  player_001: {
    actorId: 'player_001',
    slug: 'player',
    textureKey: 'actor-pixel-player-v1',
    url: '/assets/actors/pixel/player-pixel-runtime-v1.png',
    sourceFile: 'player-pixel-spritesheet-draft-v1.png',
    sourceSize: { width: 256, height: 384 },
    grid: PIXEL_GRID,
    display: TALL_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 30, y: 91 }, { x: 30, y: 93 }, { x: 31, y: 93 }, { x: 31, y: 93 },
      { x: 30, y: 83 }, { x: 31, y: 82 }, { x: 32, y: 83 }, { x: 31, y: 83 },
      { x: 32, y: 70 }, { x: 29, y: 68 }, { x: 30, y: 69 }, { x: 31, y: 69 },
      { x: 29, y: 61 }, { x: 29, y: 61 }, { x: 30, y: 61 }, { x: 30, y: 61 },
    ],
    graphPortraitCrop: { x: 11, y: 16, width: 39, height: 77 },
  },
  npc_001: {
    actorId: 'npc_001',
    slug: 'lin-huilan',
    textureKey: 'actor-pixel-lin-huilan-v1',
    url: '/assets/actors/pixel/lin-huilan-pixel-runtime-v1.png',
    sourceFile: 'lin-huilan-pixel-spritesheet-v1.png',
    sourceSize: { width: 360, height: 360 },
    grid: PIXEL_GRID,
    display: SQUARE_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 57, y: 89 }, { x: 51, y: 89 }, { x: 42, y: 89 }, { x: 34, y: 89 },
      { x: 58, y: 84 }, { x: 50, y: 83 }, { x: 42, y: 84 }, { x: 34, y: 84 },
      { x: 59, y: 69 }, { x: 50, y: 69 }, { x: 43, y: 69 }, { x: 35, y: 69 },
      { x: 55, y: 56 }, { x: 49, y: 56 }, { x: 41, y: 56 }, { x: 33, y: 56 },
    ],
    graphPortraitCrop: { x: 39, y: 17, width: 37, height: 73 },
  },
  npc_002: {
    actorId: 'npc_002',
    slug: 'shen-xingyao',
    textureKey: 'actor-pixel-shen-xingyao-v1',
    url: '/assets/actors/pixel/shen-xingyao-pixel-runtime-v1.png',
    sourceFile: 'shen-xingyao-pixel-spritesheet-draft-v1.png',
    sourceSize: { width: 256, height: 384 },
    grid: PIXEL_GRID,
    display: TALL_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 32, y: 95 }, { x: 31, y: 95 }, { x: 28, y: 95 }, { x: 29, y: 95 },
      { x: 32, y: 91 }, { x: 31, y: 91 }, { x: 29, y: 93 }, { x: 30, y: 92 },
      { x: 33, y: 85 }, { x: 30, y: 83 }, { x: 30, y: 85 }, { x: 29, y: 84 },
      { x: 31, y: 79 }, { x: 30, y: 78 }, { x: 28, y: 79 }, { x: 29, y: 78 },
    ],
    graphPortraitCrop: { x: 11, y: 12, width: 45, height: 84 },
  },
  npc_003: {
    actorId: 'npc_003',
    slug: 'zhao-lei',
    textureKey: 'actor-pixel-zhao-lei-v1',
    url: '/assets/actors/pixel/zhao-lei-pixel-runtime-v1.png',
    sourceFile: 'zhao-lei-pixel-spritesheet-draft-v1.png',
    sourceSize: { width: 256, height: 384 },
    grid: PIXEL_GRID,
    display: TALL_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 32, y: 88 }, { x: 32, y: 90 }, { x: 32, y: 90 }, { x: 31, y: 90 },
      { x: 32, y: 80 }, { x: 32, y: 80 }, { x: 31, y: 81 }, { x: 32, y: 81 },
      { x: 31, y: 70 }, { x: 31, y: 71 }, { x: 31, y: 70 }, { x: 30, y: 71 },
      { x: 31, y: 62 }, { x: 32, y: 63 }, { x: 31, y: 63 }, { x: 31, y: 63 },
    ],
    graphPortraitCrop: { x: 15, y: 12, width: 37, height: 80 },
  },
  npc_004: {
    actorId: 'npc_004',
    slug: 'chen-yue',
    textureKey: 'actor-pixel-chen-yue-v1',
    url: '/assets/actors/pixel/chen-yue-pixel-runtime-v1.png',
    sourceFile: 'chen-yue-pixel-spritesheet-draft-v1.png',
    sourceSize: { width: 256, height: 384 },
    grid: PIXEL_GRID,
    display: TALL_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 32, y: 89 }, { x: 32, y: 90 }, { x: 32, y: 90 }, { x: 32, y: 90 },
      { x: 32, y: 89 }, { x: 32, y: 90 }, { x: 32, y: 89 }, { x: 32, y: 90 },
      { x: 31, y: 83 }, { x: 31, y: 82 }, { x: 31, y: 83 }, { x: 31, y: 83 },
      { x: 30, y: 76 }, { x: 31, y: 75 }, { x: 31, y: 75 }, { x: 31, y: 76 },
    ],
    graphPortraitCrop: { x: 10, y: 12, width: 43, height: 80 },
  },
  npc_005: {
    actorId: 'npc_005',
    slug: 'zhou-shenzhi',
    textureKey: 'actor-pixel-zhou-shenzhi-v1',
    url: '/assets/actors/pixel/zhou-shenzhi-pixel-runtime-v1.png',
    sourceFile: 'zhou-shenzhi-pixel-spritesheet-v1.png',
    sourceSize: { width: 360, height: 360 },
    grid: PIXEL_GRID,
    display: SQUARE_PIXEL_DISPLAY,
    frameAnchors: [
      { x: 70, y: 89 }, { x: 56, y: 89 }, { x: 41, y: 89 }, { x: 26, y: 89 },
      { x: 70, y: 78 }, { x: 55, y: 78 }, { x: 40, y: 78 }, { x: 26, y: 78 },
      { x: 69, y: 65 }, { x: 57, y: 64 }, { x: 41, y: 64 }, { x: 27, y: 64 },
      { x: 69, y: 50 }, { x: 54, y: 49 }, { x: 38, y: 49 }, { x: 24, y: 49 },
    ],
    graphPortraitCrop: { x: 55, y: 20, width: 31, height: 70 },
  },
} satisfies Record<PixelActorId, PixelActorManifestEntry>

export const PIXEL_ACTOR_MANIFEST: readonly PixelActorManifestEntry[] = Object.values(PIXEL_ACTOR_ASSETS)

export function getPixelActorAsset(actorId: string): PixelActorManifestEntry | undefined {
  return PIXEL_ACTOR_ASSETS[actorId as PixelActorId]
}

export interface PixelActorFrameRect {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface PixelActorFrameDefinition {
  readonly actorId: PixelActorId
  readonly textureKey: string
  /** A named frame key, scoped to `textureKey`, e.g. `npc_001:down:idle`. */
  readonly frameKey: string
  readonly index: number
  readonly row: number
  readonly column: number
  readonly direction: PixelActorDirection
  readonly action: PixelActorAction
  readonly rect: PixelActorFrameRect
}

/**
 * Return integer grid edges even when a source dimension is not divisible by
 * four. The final cell receives the remainder, so the complete source image
 * is covered exactly once.
 */
function gridEdge(length: number, index: number, count: number): number {
  return Math.floor((length * index) / count)
}

export function pixelActorCellRect(
  asset: PixelActorManifestEntry,
  row: number,
  column: number,
  sourceSize: PixelActorSourceSize = asset.sourceSize,
): PixelActorFrameRect {
  if (!Number.isInteger(row) || row < 0 || row >= asset.grid.rows) throw new RangeError(`Invalid pixel actor row: ${row}`)
  if (!Number.isInteger(column) || column < 0 || column >= asset.grid.columns) throw new RangeError(`Invalid pixel actor column: ${column}`)
  const x = gridEdge(sourceSize.width, column, asset.grid.columns)
  const y = gridEdge(sourceSize.height, row, asset.grid.rows)
  return {
    x,
    y,
    width: gridEdge(sourceSize.width, column + 1, asset.grid.columns) - x,
    height: gridEdge(sourceSize.height, row + 1, asset.grid.rows) - y,
  }
}

export function pixelActorFrameKey(actorId: string, direction: PixelActorDirection, action: PixelActorAction): string {
  return `${actorId}:${direction}:${action}`
}

function frameDefinition(
  asset: PixelActorManifestEntry,
  direction: PixelActorDirection,
  action: PixelActorAction,
  sourceSize: PixelActorSourceSize = asset.sourceSize,
): PixelActorFrameDefinition {
  const row = PIXEL_ACTOR_FRAME_LAYOUT.rows[direction]
  const column = PIXEL_ACTOR_FRAME_LAYOUT.columns[action]
  return {
    actorId: asset.actorId,
    textureKey: asset.textureKey,
    frameKey: pixelActorFrameKey(asset.actorId, direction, action),
    index: row * asset.grid.columns + column,
    row,
    column,
    direction,
    action,
    rect: pixelActorCellRect(asset, row, column, sourceSize),
  }
}

export function pixelActorFrame(
  actorId: string,
  direction: PixelActorDirection = 'down',
  action: PixelActorAction = 'idle',
): PixelActorFrameDefinition | undefined {
  const asset = getPixelActorAsset(actorId)
  return asset ? frameDefinition(asset, direction, action) : undefined
}

/** Return the visible character bounds used by circular graph portraits. */
export function pixelActorGraphPortraitRect(
  actorId: string,
  sourceSize?: PixelActorSourceSize,
): PixelActorFrameRect | undefined {
  const asset = getPixelActorAsset(actorId)
  if (!asset) return undefined
  const runtimeCell = pixelActorCellRect(asset, 0, 0, sourceSize ?? asset.sourceSize)
  const manifestCell = pixelActorCellRect(asset, 0, 0, asset.sourceSize)
  const scaleX = runtimeCell.width / manifestCell.width
  const scaleY = runtimeCell.height / manifestCell.height
  const crop = asset.graphPortraitCrop
  return {
    x: runtimeCell.x + Math.round(crop.x * scaleX),
    y: runtimeCell.y + Math.round(crop.y * scaleY),
    width: Math.max(1, Math.round(crop.width * scaleX)),
    height: Math.max(1, Math.round(crop.height * scaleY)),
  }
}

export interface PixelActorSpriteConfig {
  readonly textureKey: string
  readonly frame: string
}

export function pixelActorSpriteConfig(
  actorId: string,
  direction: PixelActorDirection = 'down',
  action: PixelActorAction = 'idle',
): PixelActorSpriteConfig | undefined {
  const frame = pixelActorFrame(actorId, direction, action)
  return frame ? { textureKey: frame.textureKey, frame: frame.frameKey } : undefined
}

export interface PixelActorDisplaySize {
  readonly width: number
  readonly height: number
}

/**
 * Fit a source cell into the shared display box without distorting pixel art.
 * The square Lin/Zhou cells therefore render as 64x64 inside the 64x96 box,
 * while the 2:3 sheets (including the player) render at 64x96.
 */
export function pixelActorDisplaySize(
  actorId: string,
  sourceSize?: PixelActorSourceSize,
): PixelActorDisplaySize | undefined {
  const asset = getPixelActorAsset(actorId)
  if (!asset) return undefined
  const cell = pixelActorCellRect(asset, 0, 0, sourceSize ?? asset.sourceSize)
  const scale = Math.min(asset.display.width / cell.width, asset.display.height / cell.height)
  return {
    width: Math.round(cell.width * scale),
    height: Math.round(cell.height * scale),
  }
}

export interface PixelActorFrameOffset {
  readonly x: number
  readonly y: number
}

/**
 * Align the main connected figure in every AI-generated frame to the stable
 * actor-container origin. Isolated transparent-edge noise is intentionally not
 * part of these anchors, so it cannot pull the visible feet away from shadow.
 */
export function pixelActorFrameOffset(
  actorId: string,
  direction: PixelActorDirection,
  action: PixelActorAction,
): PixelActorFrameOffset {
  const asset = getPixelActorAsset(actorId)
  const displaySize = pixelActorDisplaySize(actorId)
  if (!asset || !displaySize) return { x: 0, y: 0 }
  const cell = pixelActorCellRect(asset, 0, 0, asset.sourceSize)
  const frameIndex = PIXEL_ACTOR_FRAME_LAYOUT.rows[direction] * asset.grid.columns
    + PIXEL_ACTOR_FRAME_LAYOUT.columns[action]
  const anchor = asset.frameAnchors[frameIndex]
  if (!anchor) return { x: 0, y: 0 }
  const opaqueCenter = ((anchor.x + 0.5) / cell.width) * displaySize.width
  const opaqueBottom = ((anchor.y + 1) / cell.height) * displaySize.height
  const anchoredCenter = asset.display.originX * displaySize.width
  const anchoredBottom = asset.display.originY * displaySize.height
  const x = Math.round(anchoredCenter - opaqueCenter)
  return {
    x: x === 0 ? 0 : x,
    y: Math.round(anchoredBottom - opaqueBottom),
  }
}

/** Minimal loader shape so the manifest remains straightforward to unit test. */
export interface PixelActorImageLoader {
  image: (key: string, url: string) => unknown
}

/** Queue the six map-character images in Phaser Scene.preload(). */
export function queuePixelActorSheets(
  loader: PixelActorImageLoader,
  assets: readonly PixelActorManifestEntry[] = PIXEL_ACTOR_MANIFEST,
): void {
  for (const asset of assets) loader.image(asset.textureKey, asset.url)
}

export interface PixelActorTextureSource {
  readonly width: number
  readonly height: number
}

/** The small portion of Phaser.Textures.Texture used by frame registration. */
export interface PixelActorTexture {
  readonly source: readonly PixelActorTextureSource[]
  has: (name: string) => boolean
  add: (name: string, sourceIndex: number, x: number, y: number, width: number, height: number) => unknown
}

/** The small portion of Phaser.Textures.TextureManager used by frame registration. */
export interface PixelActorTextureManager {
  exists: (key: string) => boolean
  get: (key: string) => PixelActorTexture
}

export interface PixelActorFrameRegistrationReport {
  readonly registered: readonly PixelActorFrameDefinition[]
  readonly existing: readonly PixelActorFrameDefinition[]
  readonly missingTextures: readonly string[]
  readonly failed: readonly PixelActorFrameDefinition[]
}

/**
 * Add named 4x4 frames after the queued images have loaded. Runtime source
 * dimensions are preferred, with manifest dimensions as a deterministic
 * fallback for test doubles or texture implementations without a source.
 * Calling this more than once is safe: existing named frames are skipped.
 */
export function registerPixelActorFrames(
  textureManager: PixelActorTextureManager,
  assets: readonly PixelActorManifestEntry[] = PIXEL_ACTOR_MANIFEST,
): PixelActorFrameRegistrationReport {
  const registered: PixelActorFrameDefinition[] = []
  const existing: PixelActorFrameDefinition[] = []
  const missingTextures: string[] = []
  const failed: PixelActorFrameDefinition[] = []

  for (const asset of assets) {
    if (!textureManager.exists(asset.textureKey)) {
      missingTextures.push(asset.textureKey)
      continue
    }
    const texture = textureManager.get(asset.textureKey)
    const runtimeSource = texture.source[0]
    const sourceSize = runtimeSource?.width > 0 && runtimeSource?.height > 0
      ? { width: runtimeSource.width, height: runtimeSource.height }
      : asset.sourceSize

    for (const direction of PIXEL_ACTOR_DIRECTIONS) {
      for (const action of PIXEL_ACTOR_ACTIONS) {
        const frame = frameDefinition(asset, direction, action, sourceSize)
        if (texture.has(frame.frameKey)) {
          existing.push(frame)
          continue
        }
        const result = texture.add(frame.frameKey, 0, frame.rect.x, frame.rect.y, frame.rect.width, frame.rect.height)
        if (result) registered.push(frame)
        else failed.push(frame)
      }
    }
  }

  return { registered, existing, missingTextures, failed }
}

export interface PixelActorDisplayObject {
  setOrigin: (x: number, y: number) => unknown
  setDisplaySize: (width: number, height: number) => unknown
}

/** Apply the shared size and bottom anchor to a Phaser Sprite or Image. */
export function applyPixelActorLayout<T extends PixelActorDisplayObject>(target: T, actorId: string): T {
  const asset = getPixelActorAsset(actorId)
  if (!asset) return target
  target.setOrigin(asset.display.originX, asset.display.originY)
  const displaySize = pixelActorDisplaySize(actorId)
  target.setDisplaySize(displaySize?.width ?? asset.display.width, displaySize?.height ?? asset.display.height)
  return target
}
