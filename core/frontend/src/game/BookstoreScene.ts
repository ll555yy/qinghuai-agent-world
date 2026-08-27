import Phaser from 'phaser'

import type { PublicActor, PublicConversation, RunSnapshot } from '../api/types'
import {
  BOOKSTORE_OBSTACLES,
  BOOKSTORE_ACTOR_CLEARANCE,
  BOOKSTORE_ACTOR_CLEARANCE_Y,
  findBookstoreActorSlot,
  findBookstorePath,
  type NavigationPoint,
} from './bookstorePathfinding'
import { ActorBubbleQueue, bubbleDuration, clampBubblePosition, truncateBubble, type BubbleTone } from './bubblePolicy'
import {
  buildBookstoreMap,
  MAP_COLS,
  MAP_ROWS,
  TILE_SIZE,
  T_DIVIDER,
  T_EMPTY,
  T_BOOKSHELF,
  T_CHAIR,
  T_COUNTER,
  T_HANGING_LANTERN,
  T_DOORWAY,
  T_LANTERN,
  T_PLANT,
  T_POSTER,
  T_POTTED_PLANT_LG,
  T_RUG,
  T_SCROLL_DESK,
  T_STONE,
  T_TABLE,
  T_TEA_SET,
  T_WALL_BODY,
  T_WALL_CORNER,
  T_WALL_LANTERN,
  T_WALL_CLOCK,
  T_WALL_TOP,
  T_WINDOW,
  T_WOOD,
} from './bookstoreTilemap'
import {
  applyPixelActorLayout,
  pixelActorFrameOffset,
  pixelActorSpriteConfig,
  queuePixelActorSheets,
  registerPixelActorFrames,
  type PixelActorAction,
  type PixelActorDirection,
} from './pixelActorAssets'
import { BOOKSTORE_DOORWAYS, roomForPosition } from './roomLayout'
import { timeVisual } from './timeVisuals'

interface ActorView {
  container: Phaser.GameObjects.Container
  name: Phaser.GameObjects.Text
  status: Phaser.GameObjects.Text
  sprite?: Phaser.GameObjects.Sprite
  ring: Phaser.GameObjects.Ellipse
  actorStatus: string
  direction: PixelActorDirection
  moving: boolean
  movementTarget: string | null
  movementToken: number
}

interface SceneCallbacks {
  onActorContext: (actorId: string, clientX: number, clientY: number) => void
  onConversationClick: (conversationId: string) => void
  onReady: () => void
}

export type SceneRoom = 'front' | 'study'
export const ROOM_DOORWAYS = BOOKSTORE_DOORWAYS

interface SceneTarget {
  x: number
  y: number
  room: SceneRoom
}

interface FurnitureOccluder {
  left: number
  right: number
  top: number
  bottom: number
  baseline: number
}

interface TileObjectRect {
  tile: number
  col: number
  row: number
  cols: number
  rows: number
}

function collectTileObjects(grid: readonly (readonly number[])[], tile: number): TileObjectRect[] {
  const objects: TileObjectRect[] = []
  let active = new Map<string, TileObjectRect>()
  for (let row = 0; row < grid.length; row += 1) {
    const runs: Array<{ col: number; cols: number }> = []
    for (let col = 0; col < (grid[row]?.length ?? 0);) {
      if (grid[row][col] !== tile) {
        col += 1
        continue
      }
      const start = col
      while (col < grid[row].length && grid[row][col] === tile) col += 1
      runs.push({ col: start, cols: col - start })
    }
    const next = new Map<string, TileObjectRect>()
    for (const run of runs) {
      const key = `${run.col}:${run.cols}`
      const continuing = active.get(key)
      next.set(key, continuing
        ? { ...continuing, rows: continuing.rows + 1 }
        : { tile, col: run.col, row, cols: run.cols, rows: 1 })
    }
    for (const [key, object] of active) if (!next.has(key)) objects.push(object)
    active = next
  }
  objects.push(...active.values())
  return objects
}

const FURNITURE_OCCLUDERS: readonly FurnitureOccluder[] = [
  { left: 132, right: 258, top: 145, bottom: 184, baseline: 184 },
  { left: 326, right: 449, top: 137, bottom: 166, baseline: 166 },
  { left: 516, right: 612, top: 128, bottom: 162, baseline: 162 },
  { left: 646, right: 768, top: 140, bottom: 176, baseline: 176 },
  { left: 344, right: 512, top: 340, bottom: 396, baseline: 396 },
  { left: 584, right: 790, top: 336, bottom: 376, baseline: 376 },
  { left: 115, right: 265, top: 418, bottom: 470, baseline: 470 },
  { left: 510, right: 590, top: 430, bottom: 470, baseline: 470 },
  { left: 600, right: 785, top: 428, bottom: 470, baseline: 470 },
]

const ROOM_BOUNDS: Record<SceneRoom, { left: number; right: number; top: number; bottom: number }> = {
  front: { left: 96, right: 780, top: 298, bottom: 470 },
  study: { left: 96, right: 780, top: 88, bottom: 246 },
}

const ACTOR_RADIUS = 26
// Characters deliberately stay above every architectural/furniture layer.
// This trades furniture occlusion for a stable, never-clipped presentation.
const ACTOR_RENDER_DEPTH = 50
const ROOM_DOORWAY = { left: 396, right: 484, top: 220, bottom: 296 }
const ACTOR_WALK_SPEED = 112

const WALL_TILES: ReadonlySet<number> = new Set([
  T_WALL_TOP, T_WALL_BODY, T_WALL_CORNER, T_DIVIDER, T_WINDOW, T_POSTER, T_WALL_LANTERN,
])

export function sceneRoom(position: { x: number; y: number }, kind: PublicActor['kind'] = 'npc'): SceneRoom {
  void kind
  return roomForPosition(position)
}

function movementDirection(
  from: { x: number; y: number },
  to: { x: number; y: number },
  fallback: PixelActorDirection,
): PixelActorDirection {
  const dx = to.x - from.x
  const dy = to.y - from.y
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return fallback
  if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? 'left' : 'right'
  return dy < 0 ? 'up' : 'down'
}

export function roomScenePosition(position: { x: number; y: number }, room: SceneRoom): { x: number; y: number } {
  if (room === 'study') {
    const anchors = [
      { logicalX: 0, x: 280, y: 190 },
      { logicalX: 2, x: 480, y: 190 },
      { logicalX: 6, x: 720, y: 205 },
    ]
    const nearest = anchors.reduce((best, candidate) => (
      Math.abs(candidate.logicalX - position.x) < Math.abs(best.logicalX - position.x) ? candidate : best
    ))
    return { x: nearest.x, y: nearest.y }
  }

  if (position.y >= 1 || position.x <= 2) return { x: 440, y: 454 }
  return position.x < 6 ? { x: 240, y: 340 } : { x: 300, y: 420 }
}

const ACTOR_COLORS = [0x5d7351, 0x486478, 0xa35a39, 0x944432, 0x475152, 0x627756]
const TONE_STYLE: Record<BubbleTone, { fill: number; text: string; stroke: number; shadow: number }> = {
  npc: { fill: 0xfbf7eb, text: '#2a241d', stroke: 0x8a7258, shadow: 0x241810 },
  player: { fill: 0xeef5eb, text: '#213022', stroke: 0x5b7852, shadow: 0x1a2618 },
  invite: { fill: 0xfdf4dc, text: '#4a3618', stroke: 0xb8883b, shadow: 0x33230c },
  accept: { fill: 0xebf5e7, text: '#293d25', stroke: 0x698c5b, shadow: 0x162414 },
  refuse: { fill: 0xb34d3b, text: '#fffbf5', stroke: 0x7a291b, shadow: 0x38100a },
  join: { fill: 0xe6eef0, text: '#1f3338', stroke: 0x567780, shadow: 0x142024 },
  leave: { fill: 0xede4d8, text: '#473a2d', stroke: 0x826c58, shadow: 0x261d15 },
  thinking: { fill: 0xf5eee0, text: '#5c4e3f', stroke: 0x9e8c78, shadow: 0x241d17 },
  closing: { fill: 0xf2d3b1, text: '#452914', stroke: 0xb56c35, shadow: 0x301908 },
  system: { fill: 0x3b3732, text: '#fff9ed', stroke: 0x1f1c19, shadow: 0x100e0d },
}

export function scenePosition(x: number, y: number): { x: number; y: number } {
  return { x: 126 + x * 82, y: 286 + y * 84 }
}

export class BookstoreScene extends Phaser.Scene {
  private readonly actorViews = new Map<string, ActorView>()
  private readonly conversationViews = new Map<string, { ring: Phaser.GameObjects.Graphics; label: Phaser.GameObjects.Text; bg: Phaser.GameObjects.Graphics }>()
  private readonly logicalPositions = new Map<string, { x: number; y: number }>()
  private readonly bubbleQueue = new ActorBubbleQueue()
  private readonly activeBubbleActors = new Set<string>()
  private readonly failedTextures = new Set<string>()
  private readonly foregroundPieces: Phaser.GameObjects.Image[] = []
  private readonly foregroundMasks: Phaser.GameObjects.Graphics[] = []
  private readonly activeBubbleObjects = new Map<string, Phaser.GameObjects.GameObject[]>()
  private pendingSnapshot: RunSnapshot | null = null
  private timeOverlay?: Phaser.GameObjects.Rectangle
  private timeLabel?: Phaser.GameObjects.Text
  private timeIcon?: Phaser.GameObjects.Text
  private timeCard?: Phaser.GameObjects.Graphics
  private reducedMotion = false

  constructor(private readonly callbacks: SceneCallbacks) { super('bookstore') }

  preload(): void {
    this.load.image('tileset', '/assets/scenes/tileset.png')
    this.load.image('two-room-background', '/assets/scenes/shenzhi-bookstore-two-room.png')
    this.load.image('bookstore-background', '/assets/scenes/shenzhi-bookstore-background.jpg')
    queuePixelActorSheets(this.load)
    this.load.on('loaderror', (file: Phaser.Loader.File) => this.failedTextures.add(file.key))
  }

  create(): void {
    this.reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    registerPixelActorFrames(this.textures)
    this.cameras.main.setBackgroundColor('#18100c')
    this.drawBookstore()

    // 氛围时间滤镜叠加层
    this.timeOverlay = this.add.rectangle(440, 267, 880, 534, 0xffebc4, 0.05)
      .setDepth(40)
      .setBlendMode(Phaser.BlendModes.MULTIPLY)

    // 右上角雅致仿古时间标签栏
    this.timeCard = this.add.graphics().setDepth(44).setVisible(false)
    this.timeLabel = this.add.text(848, 18, '', {
      fontFamily: 'STSong, "PingFang SC", "Microsoft YaHei", serif',
      fontSize: '13px',
      color: '#fbf4ea',
      fontStyle: '500',
    }).setOrigin(1, 0).setDepth(45).setVisible(false)

    if (this.pendingSnapshot) this.renderSnapshot(this.pendingSnapshot)
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => this.disposeVisuals())
    this.callbacks.onReady()
  }

  updateSnapshot(snapshot: RunSnapshot): void {
    this.pendingSnapshot = snapshot
    if (this.sys.isActive()) this.renderSnapshot(snapshot)
  }

  showBubble(actorId: string, text: string, tone: BubbleTone = 'npc', id = `${actorId}-${Date.now()}-${Math.random()}`): void {
    if (!this.bubbleQueue.enqueue({ id, actorId, text, tone })) return
    this.playNextBubble(actorId)
  }

  handleContextMenu(clientX: number, clientY: number): void {
    const bounds = this.game.canvas.getBoundingClientRect()
    const sceneX = ((clientX - bounds.left) / bounds.width) * this.scale.width
    const sceneY = ((clientY - bounds.top) / bounds.height) * this.scale.height
    let closest: { actorId: string; distance: number } | null = null
    for (const [actorId, view] of this.actorViews) {
      const dx = sceneX - view.container.x
      const dy = sceneY - view.container.y
      if (Math.abs(dx) > 54 || dy < -108 || dy > 64) continue
      const distance = dx * dx + dy * dy
      if (!closest || distance < closest.distance) closest = { actorId, distance }
    }
    if (closest) {
      this.callbacks.onActorContext(closest.actorId, clientX, clientY)
      return
    }

    let legacyClosest: { actorId: string; distance: number } | null = null
    for (const [actorId, position] of this.logicalPositions) {
      const legacy = scenePosition(position.x, position.y)
      const dx = sceneX - legacy.x
      const dy = sceneY - legacy.y
      if (Math.abs(dx) > 54 || Math.abs(dy) > 64) continue
      const distance = dx * dx + dy * dy
      if (!legacyClosest || distance < legacyClosest.distance) legacyClosest = { actorId, distance }
    }
    if (legacyClosest) this.callbacks.onActorContext(legacyClosest.actorId, clientX, clientY)
  }

  private drawBookstore(): void {
    if (!this.failedTextures.has('tileset') && this.textures.exists('tileset')) {
      this.drawTilemapBookstore()
      return
    }
    const backgroundKey = !this.failedTextures.has('two-room-background') && this.textures.exists('two-room-background')
      ? 'two-room-background'
      : 'bookstore-background'
    if (!this.failedTextures.has(backgroundKey) && this.textures.exists(backgroundKey)) {
      const background = this.add.image(440, 267, backgroundKey).setDepth(-2)
      const backgroundScale = Math.max(880 / background.width, 534 / background.height)
      background.setScale(backgroundScale)
      if (backgroundKey === 'two-room-background') this.drawFurnitureOccluders(backgroundKey, backgroundScale)
      this.add.rectangle(440, 267, 880, 534).setStrokeStyle(3, 0xd8c29d, 0.85).setDepth(-1)
      this.drawRoomGuide()
      return
    }

    // 雅致水墨古风线框蓝图备用渲染（当全部图片未命中时）
    const paper = this.add.graphics().setDepth(-3)
    paper.fillStyle(0xf2ebd9, 1).fillRoundedRect(24, 20, 832, 494, 16)
    paper.lineStyle(2, 0xc4af8b, 0.8).strokeRoundedRect(24, 20, 832, 494, 16)

    // 书斋与外堂地板底纹
    paper.fillStyle(0xe5d8bf, 0.6).fillRect(32, 28, 816, 228)
    paper.fillStyle(0xdfcfb4, 0.8).fillRect(32, 264, 816, 242)

    // 隔断墙
    paper.fillStyle(0x543725, 1).fillRect(32, 252, 364, 12)
    paper.fillRect(484, 252, 364, 12)
    paper.lineStyle(1, 0x8a6245, 1).strokeRect(32, 252, 364, 12).strokeRect(484, 252, 364, 12)

    // 长桌与案几
    paper.fillStyle(0x6e472e, 1).fillRoundedRect(340, 340, 168, 64, 6)
    paper.fillRoundedRect(132, 136, 124, 52, 6)
    paper.fillRoundedRect(646, 136, 124, 52, 6)
    this.add.text(424, 372, '书阁中堂大案', { fontFamily: 'STSong, serif', fontSize: '15px', color: '#f3e8d2' }).setOrigin(0.5)
    this.add.text(194, 162, '静心茶案', { fontFamily: 'STSong, serif', fontSize: '14px', color: '#f3e8d2' }).setOrigin(0.5)
    this.add.text(708, 162, '博古典籍柜', { fontFamily: 'STSong, serif', fontSize: '14px', color: '#f3e8d2' }).setOrigin(0.5)

    this.drawRoomGuide()
  }

  private ensureTilesetFrames(): void {
    const texture = this.textures.get('tileset')
    if (!texture || texture.has('tile-1')) return
    const tilesPerRow = 8
    for (let index = 1; index < 32; index += 1) {
      const col = index % tilesPerRow
      const row = Math.floor(index / tilesPerRow)
      texture.add(`tile-${index}`, 0, col * TILE_SIZE, row * TILE_SIZE, TILE_SIZE, TILE_SIZE)
    }
  }

  private baselineDepth(baselineY: number, extra = 0): number {
    return 10 + baselineY / 1000 + extra
  }

  private drawTilemapBookstore(): void {
    const { ground, furniture, overlays } = buildBookstoreMap()
    const tilemap = this.make.tilemap({
      tileWidth: TILE_SIZE,
      tileHeight: TILE_SIZE,
      width: MAP_COLS,
      height: MAP_ROWS,
    })
    const tileset = tilemap.addTilesetImage('tileset', 'tileset', TILE_SIZE, TILE_SIZE, 0, 0)!

    const groundLayerData = ground.map((row, rowIndex) => row.map((tile) => {
      if (WALL_TILES.has(tile)) return T_EMPTY
      // Doorway tiles carry a standalone frame for legacy maps. Repeating it
      // across a five-cell opening looks like a floating window grille, so the
      // object-layer architecture draws one coherent door over plain flooring.
      if (tile === T_DOORWAY) return rowIndex <= 16 ? T_WOOD : T_STONE
      if (tile === T_RUG) return rowIndex <= 15 ? T_WOOD : T_STONE
      return tile
    }))
    const groundLayer = tilemap.createBlankLayer('ground', tileset)!
    groundLayer.setDepth(-2)
    groundLayer.putTilesAt(groundLayerData, 0, 0)
    this.drawTilemapAtmosphere()

    this.ensureTilesetFrames()
    // Architecture is the sole visual source for walls. The wall cells remain
    // in the map as semantic/collision data, but drawing their 16 px sprites
    // here would fight the coherent 2.5D facades below and expose tile seams.
    this.drawTilemapArchitecture()
    for (const rug of collectTileObjects(ground, T_RUG)) this.drawTileObject(rug)
    const objectTiles = [
      T_BOOKSHELF, T_TABLE, T_CHAIR, T_PLANT, T_LANTERN, T_COUNTER,
      T_SCROLL_DESK, T_POTTED_PLANT_LG, T_HANGING_LANTERN, T_TEA_SET, T_WALL_CLOCK,
    ]
    for (const tile of objectTiles) {
      for (const object of collectTileObjects(furniture, tile)) this.drawTileObject(object)
    }
    for (const overlay of overlays) {
      this.add.image(overlay.col * TILE_SIZE, overlay.row * TILE_SIZE, 'tileset', `tile-${overlay.tile}`)
        .setOrigin(0, 0)
        .setDepth(this.baselineDepth((overlay.row + 1) * TILE_SIZE, 0.001))
    }
    this.drawRoomDetails()

    // 优雅金丝楠木/胡桃木外边框与内金线
    const border = this.add.graphics().setDepth(2)
    border.lineStyle(3, 0x482d1c, 1).strokeRect(1, 1, 878, 526)
    border.lineStyle(1, 0xc79e61, 0.65).strokeRect(4, 4, 872, 520)

    this.drawTilemapGuide()
  }

  private drawFurnitureOccluders(backgroundKey: string, backgroundScale: number): void {
    for (const area of FURNITURE_OCCLUDERS) {
      const maskGraphics = this.add.graphics()
      maskGraphics.fillStyle(0xffffff, 1).fillRect(area.left, area.top, area.right - area.left, area.bottom - area.top)
      maskGraphics.setVisible(false)
      const foreground = this.add.image(440, 267, backgroundKey)
        .setScale(backgroundScale)
        .setDepth(10 + area.baseline / 1000)
      foreground.setMask(maskGraphics.createGeometryMask())
      this.foregroundPieces.push(foreground)
      this.foregroundMasks.push(maskGraphics)
    }
  }

  /** 光影重构：多阶柔和丁达尔光束、环境光晕与物体投影 */
  private drawTilemapAtmosphere(): void {
    const shade = this.add.graphics().setDepth(-1.5)

    // 边缘暗角（Vignette）
    shade.fillStyle(0x130b08, 0.35)
    shade.fillRect(0, 0, 880, 28)
    shade.fillRect(0, 0, 32, 528)
    shade.fillRect(848, 0, 32, 528)
    shade.fillRect(0, 494, 880, 34)

    // 墙壁根部环境光遮蔽（AO）：分段且逐级变淡，门洞处留空。
    for (const [offset, alpha] of [[0, 0.34], [4, 0.2], [8, 0.09]] as const) {
      shade.fillStyle(0x100906, alpha)
      shade.fillRect(32, 60 + offset, 816, 5)
      shade.fillRect(32, 278 + offset, 364, 4)
      shade.fillRect(484, 278 + offset, 364, 4)
      shade.fillRect(0, 478 + offset, 392, 4)
      shade.fillRect(488, 478 + offset, 392, 4)
    }

    // Side-wall AO follows the inner edge and fades into the room, making the
    // narrow side slices read as depth rather than front-facing wall stickers.
    for (const [offset, alpha] of [[0, 0.28], [4, 0.14], [8, 0.06]] as const) {
      shade.fillStyle(0x100906, alpha)
      shade.fillRect(32 + offset, 64, 4, 414)
      shade.fillRect(844 - offset, 64, 4, 414)
    }

    // 窗户投射的自然暖阳丁达尔光束（三阶柔和光辉）
    const lightGlow = this.add.graphics().setDepth(-1.2).setBlendMode(Phaser.BlendModes.ADD)
    for (const [left, right, floorLeft, floorRight] of [[276, 396, 220, 448], [506, 626, 458, 686]] as const) {
      // 多层外扩轮廓模拟羽化，避免一道硬边多边形切在地板上。
      const featherSteps = 7
      for (let step = featherSteps; step >= 1; step -= 1) {
        const spread = step * 6
        const alpha = 0.007 + (featherSteps - step) * 0.004
        lightGlow.fillStyle(0xffe8ba, alpha)
        lightGlow.fillPoints([
          new Phaser.Geom.Point(left - spread * 0.18, 40),
          new Phaser.Geom.Point(right + spread * 0.18, 40),
          new Phaser.Geom.Point(floorRight + spread, 232),
          new Phaser.Geom.Point(floorLeft - spread, 232),
        ], true)
      }

      // 高亮核心光斑
      lightGlow.fillStyle(0xfff5d6, 0.07)
      lightGlow.fillPoints([
        new Phaser.Geom.Point(left + 24, 44), new Phaser.Geom.Point(right - 24, 44),
        new Phaser.Geom.Point(floorRight - 32, 220), new Phaser.Geom.Point(floorLeft + 32, 220),
      ], true)
      lightGlow.fillStyle(0xffe4a4, 0.035)
        .fillEllipse((floorLeft + floorRight) / 2, 229, floorRight - floorLeft + 58, 26)
      lightGlow.fillStyle(0xffefc2, 0.025)
        .fillEllipse((floorLeft + floorRight) / 2, 225, floorRight - floorLeft + 24, 15)
    }

    // 灯光与烛火暖光池（多层柔化羽化圆）
    const drawRadialGlow = (cx: number, cy: number, radius: number, color: number, maxAlpha: number) => {
      const steps = 4
      for (let i = steps; i >= 1; i -= 1) {
        const r = radius * (i / steps)
        const alpha = maxAlpha * ((steps - i + 1) / steps) * 0.4
        lightGlow.fillStyle(color, alpha)
        lightGlow.fillCircle(cx, cy, r)
      }
    }

    drawRadialGlow(224, 66, 75, 0xffd27d, 0.12)
    drawRadialGlow(652, 66, 75, 0xffd27d, 0.12)
    drawRadialGlow(392, 142, 65, 0xffdf94, 0.1)
    drawRadialGlow(640, 385, 80, 0xffc96b, 0.14)

    // 家具与大案接触阴影（带圆角与微透光）
    shade.fillStyle(0x100a07, 0.28)
    shade.fillRoundedRect(128, 150, 84, 40, 6)
    shade.fillRoundedRect(390, 136, 102, 44, 6)
    shade.fillRoundedRect(662, 118, 92, 44, 6)
    shade.fillRoundedRect(380, 364, 146, 56, 8)
    shade.fillRoundedRect(598, 366, 196, 48, 8)
  }

  /** 建筑立面重构：雕花木板壁、明清棂花花窗与黄铜壁灯 */
  private drawTilemapArchitecture(): void {
    const top = this.add.graphics().setDepth(this.baselineDepth(64, -0.002))

    // 护墙板/顶梁木纹基座
    top.fillStyle(0x2f1c13, 1).fillRect(32, 0, 816, 65)
    top.fillStyle(0x1e110b, 1).fillRect(32, 0, 816, 8)
    top.fillStyle(0x8a5b3a, 1).fillRect(34, 8, 812, 3)
    top.fillStyle(0x4a2e1d, 1).fillRect(34, 11, 812, 42)

    // 墙板古典雕花凹槽框
    for (let panelX = 42; panelX < 840; panelX += 116) {
      top.fillStyle(0x24150e, 1).fillRect(panelX, 14, 104, 34)
      top.lineStyle(2, 0x6e4933, 1).strokeRect(panelX + 2, 15, 100, 32)
      top.lineStyle(1, 0xa3744d, 0.6).strokeRect(panelX + 5, 18, 94, 26)
    }
    top.fillStyle(0x1a0e09, 1).fillRect(32, 51, 816, 14)
    top.fillStyle(0x94643d, 1).fillRect(32, 51, 816, 3)
    top.fillStyle(0x5c3722, 1).fillRect(32, 54, 816, 7)
    top.fillStyle(0x120a06, 0.85).fillRect(32, 61, 816, 7)

    // 东方木质棂花格窗（双窗套与通透天光）
    for (const windowX of [278, 508]) {
      top.fillStyle(0x190d09, 0.7).fillRect(windowX + 4, 7, 116, 49)
      top.fillStyle(0x734a2c, 1).fillRect(windowX, 4, 116, 49)
      top.lineStyle(1, 0x9b6b44, 1).strokeRect(windowX + 2, 6, 112, 45)

      // 窗纸与透光
      top.fillStyle(0xd5dec5, 1).fillRect(windowX + 6, 9, 104, 39)
      top.fillStyle(0xf3efd3, 0.95).fillRect(windowX + 8, 10, 100, 6)
      top.fillStyle(0xaab89b, 1).fillRect(windowX + 8, 16, 100, 30)

      // 木棂条纹格栅（步步锦/回纹风）
      top.lineStyle(2, 0x473727, 1)
      for (let paneX = windowX + 10; paneX <= windowX + 106; paneX += 16) {
        top.lineBetween(paneX, 10, paneX, 46)
      }
      top.lineBetween(windowX + 58, 8, windowX + 58, 48)
      top.lineBetween(windowX + 6, 28, windowX + 110, 28)

      // 窗棱斜对角加固细木线
      top.lineStyle(1, 0xede4c2, 0.5)
      for (let paneX = windowX + 10; paneX < windowX + 104; paneX += 16) {
        top.lineBetween(paneX, 16, paneX + 16, 28)
        top.lineBetween(paneX + 16, 28, paneX, 44)
      }

      // 窗台挑檐与投影
      top.fillStyle(0xb58957, 1).fillRect(windowX - 4, 50, 124, 5)
      top.fillStyle(0x27160e, 1).fillRect(windowX - 4, 55, 124, 3)
    }

    // 雅致名人匾额与画轴组
    for (const [px, py, pw, ph, coat, face] of [
      [412, 11, 26, 35, 0x3d4b47, 0xcab18e],
      [443, 7, 30, 41, 0x543f32, 0xcfb094],
      [478, 15, 20, 28, 0x2e3c43, 0xbe9d7c],
    ] as const) {
      top.fillStyle(0x140b07, 0.85).fillRect(px + 3, py + 4, pw, ph)
      top.fillStyle(0x825a35, 1).fillRect(px, py, pw, ph)
      top.fillStyle(0x241b14, 1).fillRect(px + 2, py + 2, pw - 4, ph - 4)
      top.fillStyle(coat, 1).fillPoints([
        new Phaser.Geom.Point(px + 4, py + ph - 3), new Phaser.Geom.Point(px + pw / 2, py + ph / 2),
        new Phaser.Geom.Point(px + pw - 4, py + ph - 3),
      ], true)
      top.fillStyle(face, 1).fillCircle(px + pw / 2, py + ph / 2 - 4, Math.max(3, pw / 7))
    }

    // 两侧只表现窄墙截面与承重柱，避免把北墙正立面贴图旋转到侧面。
    const sides = this.add.graphics().setDepth(this.baselineDepth(480, 0.03))
    for (const [sideX, innerX] of [[0, 25], [848, 848]] as const) {
      sides.fillStyle(0x170c08, 1).fillRect(sideX, 64, 32, 416)
      sides.fillStyle(0x332015, 1).fillRect(sideX + 4, 64, 24, 416)
      sides.fillStyle(0x74492c, 1).fillRect(innerX, 64, 7, 416)
      sides.fillStyle(0xa77448, 0.65).fillRect(innerX + (sideX === 0 ? 1 : 5), 64, 1, 416)
      for (let sy = 78; sy < 470; sy += 62) {
        sides.fillStyle(0x27170f, 1).fillRect(sideX + 5, sy, 20, 48)
        sides.lineStyle(1, 0x684128, 0.8).strokeRect(sideX + 7, sy + 2, 16, 44)
      }
    }

    // Four consistent corner posts close the north facade and the low south
    // boundary. The lower posts are short so the room stays visually open.
    for (const [postX, postY, postH] of [[24, 0, 68], [836, 0, 68], [24, 464, 32], [836, 464, 32]] as const) {
      sides.fillStyle(0x160c08, 0.55).fillRect(postX + 4, postY + 3, 22, postH)
      sides.fillStyle(0x3a2216, 1).fillRect(postX, postY, 20, postH)
      sides.fillStyle(0x875939, 1).fillRect(postX + 3, postY, 4, postH)
      sides.fillStyle(0x21120c, 1).fillRect(postX - 2, postY + postH - 6, 24, 6)
    }

    // 隔墙与侧墙的 T 型榫接柱，同时封住最外侧灰白墙砖截面。
    for (const jointX of [24, 836]) {
      sides.fillStyle(0x160c08, 0.5).fillRect(jointX + 4, 247, 22, 48)
      sides.fillStyle(0x3a2216, 1).fillRect(jointX, 244, 20, 48)
      sides.fillStyle(0x825536, 1).fillRect(jointX + 3, 244, 4, 48)
      sides.fillStyle(0x24140d, 1).fillRect(jointX - 4, 249, 28, 9)
      sides.fillStyle(0xa07146, 0.75).fillRect(jointX - 2, 249, 24, 2)
      sides.fillStyle(0x21120c, 1).fillRect(jointX - 2, 286, 24, 7)
    }
    sides.fillStyle(0x2a1911, 1).fillRect(840, 64, 8, 190)

    // 仿古黄铜壁灯
    const sconces = this.add.graphics().setDepth(this.baselineDepth(66, 0.004))
    for (const sx of [224, 652]) {
      sconces.fillStyle(0x27170f, 1).fillRect(sx - 3, 14, 6, 26)
      sconces.fillStyle(0xbfa05a, 1).fillRect(sx - 6, 19, 12, 4)
      sconces.fillStyle(0xe5b857, 1).fillPoints([
        new Phaser.Geom.Point(sx - 7, 23), new Phaser.Geom.Point(sx + 7, 23),
        new Phaser.Geom.Point(sx + 5, 37), new Phaser.Geom.Point(sx - 5, 37),
      ], true)
      sconces.fillStyle(0xfff0b8, 1).fillRect(sx - 3, 25, 6, 9)
    }

    // 厅堂中堂采用薄木格隔断：保留空间分区，但让上下两间房透气连贯。
    const divider = this.add.graphics().setDepth(this.baselineDepth(286, -0.002))
    for (const [left, width] of [[32, 364], [484, 364]] as const) {
      divider.fillStyle(0x5c3821, 1).fillRect(left, 256, width, 7)
      divider.fillStyle(0xa06d40, 0.8).fillRect(left, 256, width, 2)
      divider.fillStyle(0x27160e, 0.82).fillRect(left, 263, width, 14)
      divider.fillStyle(0x704528, 1).fillRect(left, 277, width, 6)
      divider.fillStyle(0x1d1009, 0.9).fillRect(left, 283, width, 3)
      for (let latticeX = left + 8; latticeX < left + width - 4; latticeX += 18) {
        divider.fillStyle(0x765035, 0.9).fillRect(latticeX, 263, 3, 14)
        divider.fillStyle(0xa9794c, 0.5).fillRect(latticeX + 1, 264, 1, 12)
      }
      divider.fillStyle(0x9c7244, 0.6).fillRect(left + 2, 278, width - 4, 1)
    }
    // 完整门楣、向下落地的门框柱和木石过门槛。
    divider.fillStyle(0x160c08, 0.55).fillRect(390, 246, 100, 12)
    divider.fillStyle(0x2d1a10, 1).fillRect(390, 244, 14, 48)
    divider.fillStyle(0x754a2a, 1).fillRect(394, 244, 5, 44)
    divider.fillStyle(0x2d1a10, 1).fillRect(476, 244, 14, 48)
    divider.fillStyle(0x4a2b18, 1).fillRect(476, 244, 5, 44)
    divider.fillStyle(0x5f3921, 1).fillRect(390, 244, 100, 10)
    divider.fillStyle(0x9a6a3b, 1).fillRect(395, 245, 90, 3)
    divider.fillStyle(0x2a1a12, 1).fillRect(396, 283, 88, 10)
    divider.fillStyle(0x68472f, 1).fillRect(398, 283, 84, 3)
    divider.fillStyle(0xb2885a, 0.55).fillRect(402, 286, 76, 2)

    // 南墙压缩为一格高的通透矮墙，中央入口完整留空。
    const south = this.add.graphics().setDepth(this.baselineDepth(496, -0.003))
    for (const [left, width] of [[0, 392], [488, 392]] as const) {
      south.fillStyle(0x1b0f0a, 1).fillRect(left, 480, width, 16)
      south.fillStyle(0x6c4328, 1).fillRect(left, 480, width, 5)
      south.fillStyle(0xa27145, 0.75).fillRect(left, 480, width, 2)
      south.fillStyle(0x342015, 1).fillRect(left, 485, width, 8)
      south.fillStyle(0x110906, 0.9).fillRect(left, 493, width, 3)
    }
  }

  /** 家具矢量精绘：中式古籍柜、长桌、雕花圈椅、织花地毯等 */
  private drawTileObject(object: TileObjectRect): void {
    const x = object.col * TILE_SIZE
    const y = object.row * TILE_SIZE
    const width = object.cols * TILE_SIZE
    const height = object.rows * TILE_SIZE
    const art = this.add.graphics()
      .setDepth(object.tile === T_RUG ? -1.35 : this.baselineDepth(y + height))

    // 1. 织花地毯 (T_RUG)
    if (object.tile === T_RUG) {
      art.fillStyle(0x1a0f0b, 0.35).fillRoundedRect(x + 3, y + 5, width - 2, height - 1, 4)
      art.fillStyle(0x6b3b2c, 1).fillRoundedRect(x, y, width - 3, height - 4, 4)

      // 回纹边框
      art.lineStyle(2, 0xad824f, 0.95).strokeRoundedRect(x + 3, y + 3, width - 9, height - 10, 3)
      art.lineStyle(1, 0x42241d, 0.9).strokeRoundedRect(x + 7, y + 7, width - 17, height - 18, 2)

      // 织锦底纹点缀
      for (let px = x + 12; px < x + width - 12; px += 14) {
        for (let py = y + 12; py < y + height - 12; py += 14) {
          art.fillStyle(0xbf9459, 0.65).fillRect(px, py, 2, 2)
          art.fillStyle(0x47241d, 0.75).fillRect(px + 2, py + 2, 2, 2)
        }
      }

      // 两端流苏穗子
      for (let tx = x + 4; tx < x + width - 6; tx += 6) {
        art.fillStyle(0xd9c298, 0.8).fillRect(tx, y, 3, 2)
        art.fillRect(tx, y + height - 5, 3, 2)
      }
      return
    }

    // 2. 仿古藏书阁柜 (T_BOOKSHELF)
    if (object.tile === T_BOOKSHELF) {
      art.fillStyle(0x0e0805, 0.45).fillRect(x + 6, y + 8, width, height)
      art.fillStyle(0x2d1b11, 1).fillRect(x, y + 6, width, height - 6)

      // 柜顶斜角立体面与金线
      art.fillStyle(0x5f3d23, 1).fillPoints([
        new Phaser.Geom.Point(x, y + 6), new Phaser.Geom.Point(x + 6, y),
        new Phaser.Geom.Point(x + width, y), new Phaser.Geom.Point(x + width, y + 6),
      ], true)
      art.fillStyle(0x875a34, 1).fillRect(x + 7, y + 1, width - 8, 2)
      art.fillStyle(0x190d08, 1).fillRect(x + width - 5, y + 6, 5, height - 6)
      art.fillStyle(0x1f120a, 1).fillRect(x, y + height - 5, width, 5)
      art.fillStyle(0x6f492b, 1).fillRect(x + 2, y + 7, 3, height - 12)
      // Independent solid end panels stop long shelves looking sliced off.
      art.fillStyle(0x5b3923, 1).fillRect(x, y + 6, 6, height - 7)
      art.fillStyle(0x936442, 1).fillRect(x + 1, y + 8, 2, height - 13)
      art.fillStyle(0x21120b, 1).fillRect(x + width - 7, y + 6, 7, height - 7)
      art.fillStyle(0x684329, 1).fillRect(x + width - 7, y + 8, 2, height - 13)

      // 丰富色彩的书脊与函套
      const bookColors = [0x213236, 0x482f23, 0x2b3d29, 0x52432a, 0x362d3a, 0x613c28, 0x1f2e3d, 0x522d25]
      let shelfIndex = 0
      for (let shelfY = y + 20; shelfY < y + height - 4; shelfY += 15) {
        art.fillStyle(0x160d08, 1).fillRect(x + 4, shelfY, width - 8, 3)
        let bookX = x + 6
        while (bookX < x + width - 6) {
          const bookWidth = 3 + ((bookX + shelfIndex * 7) % 4)
          const bookHeight = 8 + ((bookX + shelfIndex * 3) % 4)
          const color = bookColors[(bookX + shelfIndex) % bookColors.length]
          art.fillStyle(color, 1).fillRect(bookX, shelfY - bookHeight, Math.min(bookWidth, x + width - 6 - bookX), bookHeight)

          // 烫金书脊线
          if ((bookX + shelfIndex) % 2 === 0) {
            art.fillStyle(0xb5925a, 0.8).fillRect(bookX + 1, shelfY - bookHeight + 2, 1, 1)
          }
          // 偶尔横放的书本/卷轴
          if ((bookX + shelfIndex) % 11 === 0 && bookX + bookWidth + 6 < x + width - 5) {
            art.fillStyle(bookColors[(bookX + shelfIndex + 2) % bookColors.length], 1)
              .fillRect(bookX + bookWidth + 1, shelfY - 4, 6, 3)
            bookX += 6
          }
          bookX += bookWidth + 2
        }
        shelfIndex += 1
      }

      // 博古架古玩陈列（青瓷瓶、插花、砚山）
      if (width >= 80 && height >= 40) {
        const propY = y + Math.min(height - 12, 34)
        art.fillStyle(0x274a4f, 1).fillCircle(x + width - 23, propY - 3, 6)
        art.fillStyle(0x82a66f, 1).fillRect(x + width - 26, propY - 7, 5, 3)
        art.fillStyle(0xa67d4e, 1).fillRect(x + width - 25, propY + 3, 5, 4)
        art.fillStyle(0xc4ad80, 1).fillCircle(x + 20, propY - 2, 5)
        art.fillStyle(0x452f20, 1).fillRect(x + 17, propY + 3, 7, 4)
      }
      return
    }

    // 3. 中堂长桌/书案 (T_TABLE, T_SCROLL_DESK, T_TEA_SET)
    if (object.tile === T_TABLE || object.tile === T_SCROLL_DESK || object.tile === T_TEA_SET) {
      art.fillStyle(0x100906, 0.45).fillRoundedRect(x + 7, y + 9, width - 1, height - 2, 4)
      art.fillStyle(0x3f2314, 1).fillRoundedRect(x, y + 7, width - 3, height - 8, 4)

      // 案面打磨温润高光
      art.fillStyle(0x6b4627, 1).fillPoints([
        new Phaser.Geom.Point(x, y + 7), new Phaser.Geom.Point(x + 6, y + 1),
        new Phaser.Geom.Point(x + width - 3, y + 1), new Phaser.Geom.Point(x + width - 3, y + height - 8),
        new Phaser.Geom.Point(x, y + height - 8),
      ], true)
      art.lineStyle(2, 0x22130a, 1).strokeRoundedRect(x, y + 1, width - 3, height - 8, 4)
      art.fillStyle(0x9e7143, 1).fillRect(x + 7, y + 3, width - 12, 2)

      // 细腻木纹
      for (let grainY = y + 9; grainY < y + height - 9; grainY += 6) {
        art.fillStyle(0x52311b, 0.8).fillRect(x + 6, grainY, width - 14, 1)
      }

      // 桌腿及马蹄牙板
      art.fillStyle(0x2a160d, 1).fillRect(x + 3, y + height - 13, width - 9, 7)
      art.fillStyle(0x6a4225, 1).fillRect(x + 5, y + height - 13, width - 13, 2)
      art.fillStyle(0x22130b, 1).fillRect(x + 5, y + height - 8, 5, 8)
      art.fillRect(x + width - 12, y + height - 8, 5, 8)

      if (object.tile === T_SCROLL_DESK) {
        // 展卷宣纸与镇纸
        art.fillStyle(0xd9ccae, 1).fillRoundedRect(x + width * 0.2, y + 6, width * 0.6, Math.max(9, height - 16), 3)
        art.fillStyle(0x5c4d3b, 0.9).fillRect(x + width * 0.3, y + 10, width * 0.28, 1)
        art.fillRect(x + width * 0.38, y + 14, width * 0.3, 1)
        art.fillStyle(0x1c1916, 1).fillRoundedRect(x + width - 18, y + height - 15, 10, 7, 2)
      } else if (object.tile === T_TEA_SET) {
        // 紫砂壶与青瓷茶盅
        art.fillStyle(0xb39d7a, 1).fillCircle(x + width * 0.42, y + height * 0.47, 6)
        art.fillStyle(0x543f2d, 1).fillCircle(x + width * 0.42, y + height * 0.47, 3)
        art.fillStyle(0xc9bd9f, 1).fillCircle(x + width * 0.68, y + height * 0.53, 4)
      }
      return
    }

    // 4. 服务柜台 (T_COUNTER)
    if (object.tile === T_COUNTER) {
      art.fillStyle(0x0e0805, 0.45).fillRoundedRect(x + 7, y + 9, width, height - 2, 4)
      art.fillStyle(0x321c11, 1).fillRect(x, y + 7, width, height - 7)
      art.fillStyle(0x633f24, 1).fillPoints([
        new Phaser.Geom.Point(x, y + 7), new Phaser.Geom.Point(x + 6, y + 1),
        new Phaser.Geom.Point(x + width, y + 1), new Phaser.Geom.Point(x + width, y + 7),
      ], true)
      art.fillStyle(0x9a6d3f, 1).fillRect(x + 7, y + 2, width - 8, 2)
      art.fillStyle(0x1e1009, 1).fillRect(x + width - 5, y + 7, 5, height - 7)

      // 立面复古雕花板面与黄铜抽屉把手
      for (let panelX = x + 7; panelX < x + width - 8; panelX += 24) {
        art.lineStyle(1, 0x180d07, 1).strokeRect(panelX, y + 11, Math.min(18, x + width - 6 - panelX), height - 16)
        art.fillStyle(0xc7a05e, 1).fillRect(panelX + 8, y + 14, 2, 2)
      }
      art.fillStyle(0x1b0e08, 0.95).fillRect(x, y + height - 4, width, 4)
      return
    }

    // 5. 雕花官帽椅/木凳 (T_CHAIR)
    if (object.tile === T_CHAIR) {
      art.fillStyle(0x100906, 0.4).fillEllipse(x + width / 2 + 3, y + height - 1, Math.max(12, width - 1), 6)
      art.fillStyle(0x3f2214, 1).fillRect(x + 3, y + 2, Math.max(9, width - 7), 4)
      for (let slat = x + 5; slat < x + width - 4; slat += 4) {
        art.fillStyle(0x6b4627, 1).fillRect(slat, y + 4, 2, 5)
      }
      art.fillStyle(0x633e24, 1).fillRect(x + 3, y + 9, Math.max(9, width - 7), Math.max(4, height - 12))
      art.fillStyle(0x916538, 1).fillRect(x + 5, y + 9, Math.max(5, width - 11), 2)
      art.fillStyle(0x22130b, 1).fillRect(x + 4, y + height - 4, 3, 4)
      art.fillRect(x + width - 7, y + height - 4, 3, 4)
      return
    }

    // 6. 雅致青瓷盆景 (T_PLANT, T_POTTED_PLANT_LG)
    if (object.tile === T_PLANT || object.tile === T_POTTED_PLANT_LG) {
      const cx = x + width / 2
      art.fillStyle(0x140c08, 0.32).fillEllipse(cx + 3, y + height - 3, Math.max(14, width - 1), 8)

      // 青花瓷/紫砂花盆
      art.fillStyle(0x733c20, 1).fillRect(cx - 6, y + height - 9, 12, 8)
      art.fillStyle(0x94542d, 1).fillRect(cx - 8, y + height - 11, 16, 3)

      // 丰富深浅层次的翠绿叶片
      for (const [dx, dy, color] of [
        [-8, -17, 0x364f27], [2, -20, 0x4a6230], [8, -15, 0x2b4221],
        [-3, -13, 0x597039], [5, -11, 0x6e8748],
      ]) {
        art.fillStyle(color, 1).fillEllipse(cx + dx, y + height + dy, 10, 14)
      }
      return
    }

    this.add.image(x, y, 'tileset', `tile-${object.tile}`)
      .setOrigin(0, 0)
      .setDepth(this.baselineDepth(y + height))
    art.destroy()
  }

  /** 室内精致道具重绘：文房四宝、古籍卷轴、算盘与典雅陈设 */
  private drawRoomDetails(): void {
    const studyDetails = this.add.graphics().setDepth(this.baselineDepth(226, 0.002))

    // 藏书阁取书移动木梯
    studyDetails.lineStyle(5, 0x24140d, 1)
      .lineBetween(803, 94, 766, 226)
      .lineBetween(817, 96, 780, 226)
    studyDetails.lineStyle(2, 0x8a6039, 1)
      .lineBetween(801, 94, 764, 226)
      .lineBetween(815, 96, 778, 226)
    for (let y = 108; y <= 211; y += 15) {
      const lx = 803 - (y - 94) / 3.57
      studyDetails.lineStyle(4, 0x2f1b11, 1).lineBetween(lx, y + 2, lx + 14, y + 2)
      studyDetails.lineStyle(2, 0x7e5432, 1).lineBetween(lx, y, lx + 14, y)
      if (y % 30 !== 0) studyDetails.fillStyle(0xa88055, 0.8).fillRect(lx + 3, y, 4, 1)
    }
    studyDetails.fillStyle(0x120a06, 0.46).fillEllipse(773, 228, 43, 9)

    // 左侧品茗考据案：古旧舆图、铜柄放大镜、墨盒与摊开典籍
    studyDetails.fillStyle(0x2f1b11, 0.42).fillRect(134, 143, 59, 27)
    studyDetails.fillStyle(0xbfa573, 1).fillPoints([
      new Phaser.Geom.Point(136, 143), new Phaser.Geom.Point(189, 145),
      new Phaser.Geom.Point(192, 160), new Phaser.Geom.Point(187, 166),
      new Phaser.Geom.Point(139, 165), new Phaser.Geom.Point(134, 158),
    ], true)
    studyDetails.lineStyle(1, 0x7a6544, 0.85)
      .lineBetween(143, 151, 154, 147).lineBetween(154, 147, 164, 157)
      .lineBetween(164, 157, 184, 151).lineBetween(148, 161, 177, 160)
    studyDetails.fillStyle(0x5f7357, 0.85).fillCircle(157, 155, 4)
    studyDetails.lineStyle(2, 0xb89b58, 1).strokeCircle(177, 152, 6)
    studyDetails.lineStyle(3, 0x613d23, 1).lineBetween(181, 157, 187, 163)
    studyDetails.fillStyle(0x211c19, 1).fillRoundedRect(193, 146, 6, 8, 2)
    studyDetails.fillStyle(0xd4c6a0, 1).fillRect(176, 164, 17, 6)

    // 案上卷宗与狼毫笔
    studyDetails.fillStyle(0xcfc09a, 1).fillPoints([
      new Phaser.Geom.Point(398, 139), new Phaser.Geom.Point(423, 137),
      new Phaser.Geom.Point(447, 140), new Phaser.Geom.Point(444, 162),
      new Phaser.Geom.Point(422, 160), new Phaser.Geom.Point(400, 163),
    ], true)
    studyDetails.lineStyle(1, 0x695743, 0.8)
    for (let ly = 144; ly <= 156; ly += 4) {
      studyDetails.lineBetween(404, ly, 418, ly - 1).lineBetween(426, ly - 1, 440, ly)
    }
    studyDetails.fillStyle(0xe5dcbf, 1).fillEllipse(458, 143, 5, 17)
    studyDetails.lineStyle(2, 0x4f3a28, 1).lineBetween(456, 149, 449, 163)
    studyDetails.fillStyle(0x211c19, 1).fillRoundedRect(462, 155, 10, 7, 2)

    // 柜台细节：登记册、算盘、温润复古台灯
    const counterDetails = this.add.graphics().setDepth(this.baselineDepth(384, 0.004))
    counterDetails.fillStyle(0x35433a, 1).fillRoundedRect(650, 355, 34, 18, 2)
    counterDetails.fillStyle(0xcab996, 1).fillRect(653, 357, 28, 13)
    counterDetails.fillStyle(0x574838, 0.8).fillRect(659, 361, 16, 1).fillRect(659, 365, 13, 1)

    counterDetails.fillStyle(0xb39a6c, 1).fillEllipse(710, 363, 10, 8)
    counterDetails.fillStyle(0x4f3d2a, 1).fillEllipse(710, 362, 6, 3)

    // 暖黄台灯
    counterDetails.fillStyle(0x2f2117, 1).fillRect(742, 350, 4, 19)
    counterDetails.fillStyle(0xba8030, 1).fillPoints([
      new Phaser.Geom.Point(732, 352), new Phaser.Geom.Point(751, 352),
      new Phaser.Geom.Point(747, 343), new Phaser.Geom.Point(736, 343),
    ], true)
    counterDetails.fillStyle(0xf0c46b, 0.75).fillEllipse(741, 354, 32, 15)

    // 中堂大案：错落书堆、翡翠玉璧与印章
    const tableDetails = this.add.graphics().setDepth(this.baselineDepth(400, 0.004))
    for (const [bx, by, bw, color] of [
      [404, 370, 24, 0x3a4a42], [406, 366, 21, 0x663c2c], [409, 362, 18, 0x756237],
    ] as const) {
      tableDetails.fillStyle(0x190f09, 0.75).fillRect(bx + 2, by + 3, bw, 3)
      tableDetails.fillStyle(color, 1).fillRect(bx, by, bw, 5)
      tableDetails.fillStyle(0xc5af81, 0.95).fillRect(bx + 2, by + 1, bw - 4, 2)
    }

    // 翡翠玉盘与印泥盒
    tableDetails.fillStyle(0x567b60, 1).fillEllipse(435, 378, 14, 9)
    tableDetails.fillStyle(0x7da487, 1).fillEllipse(435, 376, 10, 5)
    tableDetails.fillStyle(0x9c3e2f, 1).fillRoundedRect(474, 373, 14, 10, 2)
    tableDetails.fillStyle(0xd95743, 1).fillCircle(481, 378, 4)
  }

  private drawTilemapGuide(): void {
    const guide = this.add.graphics().setDepth(1)
    const dividerDoor = { left: 25 * TILE_SIZE, right: 30 * TILE_SIZE, top: 16 * TILE_SIZE, bottom: 18 * TILE_SIZE }
    const frontDoor = { left: 25 * TILE_SIZE, right: 30 * TILE_SIZE, top: 30 * TILE_SIZE, bottom: 33 * TILE_SIZE }

    // 中门洞的实体结构由 architecture 绘制，这里只保留地面通路微光。
    guide.fillStyle(0xf8dda8, 0.08).fillRect(dividerDoor.left + 5, 286, dividerDoor.right - dividerDoor.left - 10, 6)

    // 底部大门：门柱、门扇暗部、门槛与两级台阶形成完整入口。
    guide.fillStyle(0x1b0f09, 0.45).fillRect(frontDoor.left - 9, 476, 98, 52)
    guide.fillStyle(0x3b2417, 1).fillRect(frontDoor.left - 8, 474, 12, 54)
    guide.fillStyle(0x805333, 1).fillRect(frontDoor.left - 5, 475, 4, 49)
    guide.fillStyle(0x3b2417, 1).fillRect(frontDoor.right - 4, 474, 12, 54)
    guide.fillStyle(0x56351f, 1).fillRect(frontDoor.right - 4, 475, 4, 49)
    guide.fillStyle(0x5c3922, 1).fillRect(frontDoor.left - 8, 474, 96, 9)
    guide.fillStyle(0x9a6b40, 1).fillRect(frontDoor.left - 3, 475, 86, 3)
    guide.fillStyle(0x2c2019, 1).fillRect(frontDoor.left - 4, 508, 88, 7)
    guide.fillStyle(0x72614e, 1).fillRect(frontDoor.left - 9, 515, 98, 6)
    guide.fillStyle(0x9a8b73, 1).fillRect(frontDoor.left - 14, 521, 108, 7)
    guide.fillStyle(0xc1ad88, 0.55).fillRect(frontDoor.left - 10, 521, 100, 2)
  }

  private drawRoomGuide(): void {
    const guide = this.add.graphics().setDepth(1)
    guide.lineStyle(2, 0x342217, 0.7)
      .lineBetween(48, 254, ROOM_DOORWAY.left, 254)
      .lineBetween(ROOM_DOORWAY.right, 254, 832, 254)
    guide.fillStyle(0xd6b477, 0.22).fillRoundedRect(ROOM_DOORWAY.left, ROOM_DOORWAY.top, ROOM_DOORWAY.right - ROOM_DOORWAY.left, ROOM_DOORWAY.bottom - ROOM_DOORWAY.top, 10)
    guide.lineStyle(1, 0xf6d397, 0.75)
      .lineBetween(ROOM_DOORWAY.left + 8, ROOM_DOORWAY.top + 6, ROOM_DOORWAY.left + 8, ROOM_DOORWAY.bottom - 6)
      .lineBetween(ROOM_DOORWAY.right - 8, ROOM_DOORWAY.top + 6, ROOM_DOORWAY.right - 8, ROOM_DOORWAY.bottom - 6)
  }

  private renderSnapshot(snapshot: RunSnapshot): void {
    const liveActorIds = new Set(snapshot.actors.map((actor) => actor.actorId))
    const targets = this.layoutTargets(snapshot)
    snapshot.actors.forEach((actor, index) => this.upsertActor(actor, index, snapshot, targets.get(actor.actorId)))
    for (const [actorId, view] of this.actorViews) {
      if (!liveActorIds.has(actorId)) {
        view.container.destroy(true)
        view.name.destroy()
        view.status.destroy()
        this.actorViews.delete(actorId)
        this.logicalPositions.delete(actorId)
      }
    }
    const liveConversationIds = new Set<string>()
    snapshot.conversations.filter((item) => item.status === 'open').forEach((conversation) => {
      liveConversationIds.add(conversation.conversationId)
      this.upsertConversation(conversation)
    })
    for (const [id, view] of this.conversationViews) {
      if (!liveConversationIds.has(id)) {
        view.ring.destroy()
        view.bg.destroy()
        view.label.destroy()
        this.conversationViews.delete(id)
      }
    }
    this.applyTimeVisual(snapshot)
  }

  private layoutTargets(snapshot: RunSnapshot): Map<string, SceneTarget> {
    const preferredRooms = snapshot.actors.map((actor) => {
      const position = snapshot.actorStates[actor.actorId]?.position ?? { x: 0, y: 0 }
      return { actor, position, room: sceneRoom(position, actor.kind) }
    })
    const preferredByActor = new Map(preferredRooms.map((item) => [item.actor.actorId, item.room]))
    const conversationRooms = new Map<string, SceneRoom>()

    for (const conversation of snapshot.conversations) {
      if (conversation.status !== 'open' || !conversation.participants.length) continue
      const room = preferredByActor.get(conversation.participants[0])
      if (!room) continue
      for (const actorId of conversation.participants) conversationRooms.set(actorId, room)
    }
    const assignedCounts: Record<SceneRoom, number> = { front: 0, study: 0 }
    const targets = new Map<string, SceneTarget>()

    for (const item of preferredRooms) {
      const groupedRoom = conversationRooms.get(item.actor.actorId)
      let room = groupedRoom ?? item.room
      if (!groupedRoom && item.actor.kind === 'npc' && assignedCounts[room] >= 3) {
        const other: SceneRoom = room === 'front' ? 'study' : 'front'
        if (assignedCounts[other] < 3) room = other
      }
      assignedCounts[room] += item.actor.kind === 'npc' ? 1 : 0

      const mapped = roomScenePosition(item.position, room)
      const occupied = [...targets.values()]
        .filter((other) => other.room === room)
        .map(({ x, y }) => ({ x, y }))
      const target = findBookstoreActorSlot(mapped, ROOM_BOUNDS[room], occupied, ACTOR_RADIUS * 2)
      targets.set(item.actor.actorId, { ...target, room })
      this.logicalPositions.set(item.actor.actorId, item.position)
    }
    return targets
  }

  private upsertActor(actor: PublicActor, index: number, snapshot: RunSnapshot, targetOverride?: SceneTarget): void {
    const actorState = snapshot.actorStates[actor.actorId]
    const target = targetOverride ?? {
      ...roomScenePosition(actorState?.position ?? { x: index, y: 0 }, sceneRoom(actorState?.position ?? { x: index, y: 0 }, actor.kind)),
      room: sceneRoom(actorState?.position ?? { x: index, y: 0 }, actor.kind),
    }
    let view = this.actorViews.get(actor.actorId)
    if (!view) {
      const config = pixelActorSpriteConfig(actor.actorId)

      // 柔和羽化的人物接地阴影
      const shadow = this.add.ellipse(0, 0, 32, 10, 0x18100b, 0.45)
      let sprite: Phaser.GameObjects.Sprite | undefined
      if (config && !this.failedTextures.has(config.textureKey) && this.textures.exists(config.textureKey)) {
        sprite = this.add.sprite(0, 0, config.textureKey, config.frame)
        applyPixelActorLayout(sprite, actor.actorId)
        const offset = pixelActorFrameOffset(actor.actorId, 'down', 'idle')
        sprite.setPosition(offset.x, offset.y)
      }

      // 现代圆角质感备用头像
      const fallbackBackground = this.add.circle(0, -28, 24, ACTOR_COLORS[index % ACTOR_COLORS.length])
        .setStrokeStyle(2.5, 0xfaf4e8, 1)
        .setVisible(!sprite)
      const fallback = this.add.text(0, -28, actor.kind === 'player' ? '我' : actor.name.slice(0, 1), {
        fontFamily: 'STSong, "Microsoft YaHei", sans-serif', fontSize: '18px', fontStyle: 'bold', color: '#fffaf0',
      }).setOrigin(0.5).setVisible(!sprite)

      const ring = this.add.ellipse(0, 0, 42, 15).setStrokeStyle(2.5, 0xf2b861, 0).setVisible(false)

      // 雅致卡片式名牌
      const name = this.add.text(target.x, target.y + 16, actor.kind === 'player' ? '你' : actor.name, {
        fontFamily: 'STSong, "PingFang SC", "Microsoft YaHei", serif',
        fontSize: '12px',
        fontStyle: 'bold',
        color: '#1b120c',
        stroke: '#fffaf0',
        strokeThickness: 1,
        backgroundColor: '#fffaf0ff',
        padding: { x: 7, y: 3 },
      }).setOrigin(0.5).setDepth(100).setResolution(2)

      // 微型胶囊状态徽章
      const status = this.add.text(target.x, target.y + 35, '', {
        fontFamily: 'STSong, "Microsoft YaHei", sans-serif',
        fontSize: '10px',
        color: '#fff5e6',
        backgroundColor: '#2b211aff',
        padding: { x: 6, y: 2 },
      }).setOrigin(0.5).setDepth(100).setResolution(2)

      const children: Phaser.GameObjects.GameObject[] = [shadow]
      if (sprite) children.push(sprite)
      children.push(fallbackBackground, fallback, ring)

      const container = this.add.container(target.x, target.y, children)
        .setDepth(ACTOR_RENDER_DEPTH)
        .setSize(72, 92)
        .setInteractive({ useHandCursor: true })

      container.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
        const e = pointer.event as MouseEvent
        if (e.button === 2 || pointer.rightButtonDown()) this.callbacks.onActorContext(actor.actorId, e.clientX, e.clientY)
      })

      view = {
        container,
        name,
        status,
        sprite,
        ring,
        actorStatus: actorState?.status ?? 'present',
        direction: 'down',
        moving: false,
        movementTarget: null,
        movementToken: 0,
      }
      this.actorViews.set(actor.actorId, view)
      this.syncActorLabels(view)
    }

    view.actorStatus = actorState?.status ?? 'present'
    const movementTarget = `${Math.round(target.x)}:${Math.round(target.y)}`
    const atTarget = Phaser.Math.Distance.Between(view.container.x, view.container.y, target.x, target.y) < 1

    if (!atTarget && view.movementTarget !== movementTarget) {
      this.moveActorAlongPath(actor.actorId, view, target)
    } else if (atTarget) {
      view.moving = false
      view.movementTarget = movementTarget
      if (!this.activeBubbleActors.has(actor.actorId)) this.setActorPixelFrame(actor.actorId, view, 'idle')
    } else if (!this.activeBubbleActors.has(actor.actorId)) {
      if (!view.moving) this.setActorPixelFrame(actor.actorId, view, 'idle')
    }

    const labels: Record<string, string> = {
      approaching: '• 漫步', inviting: '• 伫足', chatting: '• 倾谈', waiting: '• 静候', departed: '• 离席',
    }
    const visibleStatus = view.moving ? 'approaching' : view.actorStatus
    view.status.setText(labels[visibleStatus] ?? '').setVisible(Boolean(labels[visibleStatus]))
    this.syncActorLabels(view)

    if (view.actorStatus === 'departed') {
      this.tweens.killTweensOf(view.container)
      view.moving = false
      view.movementToken += 1
      if (this.reducedMotion) view.container.setAlpha(0.25)
      else this.tweens.add({ targets: view.container, alpha: 0.25, duration: 420 })
      view.name.setAlpha(0.35)
      view.status.setAlpha(0.35)
    } else {
      view.container.setAlpha(1)
      view.name.setAlpha(1)
      view.status.setAlpha(1)
    }
    if (this.bubbleQueue.size(actor.actorId) && !this.activeBubbleActors.has(actor.actorId)) this.playNextBubble(actor.actorId)
  }

  private setActorPixelFrame(actorId: string, view: ActorView, action: PixelActorAction): void {
    if (!view.sprite) return
    const config = pixelActorSpriteConfig(actorId, view.direction, action)
    if (config && view.sprite.texture.key === config.textureKey) {
      view.sprite.setFrame(config.frame)
      const offset = pixelActorFrameOffset(actorId, view.direction, action)
      view.sprite.setPosition(offset.x, offset.y)
    }
  }

  private moveActorAlongPath(actorId: string, view: ActorView, target: SceneTarget): void {
    this.tweens.killTweensOf(view.container)
    view.movementToken += 1
    const movementToken = view.movementToken
    view.movementTarget = `${Math.round(target.x)}:${Math.round(target.y)}`

    if (this.reducedMotion) {
      view.container.setPosition(target.x, target.y)
      view.container.setDepth(ACTOR_RENDER_DEPTH)
      this.syncActorLabels(view)
      view.moving = false
      this.setActorPixelFrame(actorId, view, 'idle')
      this.refreshConversationVisuals()
      return
    }

    const start = { x: view.container.x, y: view.container.y }
    const actorObstacles = [...this.actorViews.entries()]
      .filter(([otherActorId, otherView]) => otherActorId !== actorId && otherView.actorStatus !== 'departed')
      .map(([, otherView]) => ({
        left: otherView.container.x - 18,
        right: otherView.container.x + 18,
        top: otherView.container.y - 10,
        bottom: otherView.container.y + 10,
      }))
    const fullRoute = findBookstorePath(start, target, {
      clearanceX: BOOKSTORE_ACTOR_CLEARANCE,
      clearanceY: BOOKSTORE_ACTOR_CLEARANCE_Y,
      obstacles: [...BOOKSTORE_OBSTACLES, ...actorObstacles],
    })
    const routeEnd = fullRoute[fullRoute.length - 1]
    if (!routeEnd || Phaser.Math.Distance.Between(routeEnd.x, routeEnd.y, target.x, target.y) > 1) {
      view.moving = false
      view.movementTarget = null
      this.setActorPixelFrame(actorId, view, 'idle')
      return
    }
    const route = fullRoute.slice(1)
    const last = route[route.length - 1]
    if (!last || Phaser.Math.Distance.Between(last.x, last.y, target.x, target.y) > 0.5) {
      route.push({ x: target.x, y: target.y })
    } else {
      route[route.length - 1] = { x: target.x, y: target.y }
    }
    view.moving = true

    const walkSegment = (index: number): void => {
      if (movementToken !== view.movementToken) return
      const next = route[index]
      if (!next) {
        view.moving = false
        view.container.setPosition(target.x, target.y)
        view.container.setDepth(ACTOR_RENDER_DEPTH)
        this.syncActorLabels(view)
        this.setActorPixelFrame(actorId, view, 'idle')
        this.refreshConversationVisuals()
        return
      }

      const from: NavigationPoint = { x: view.container.x, y: view.container.y }
      const distance = Phaser.Math.Distance.Between(from.x, from.y, next.x, next.y)
      if (distance < 0.5) {
        walkSegment(index + 1)
        return
      }
      view.direction = movementDirection(from, next, view.direction)
      this.setActorPixelFrame(actorId, view, 'walkA')
      this.tweens.add({
        targets: view.container,
        x: next.x,
        y: next.y,
        duration: Math.max(90, Math.round((distance / ACTOR_WALK_SPEED) * 1000)),
        ease: 'Linear',
        onUpdate: (tween: Phaser.Tweens.Tween) => {
          if (movementToken !== view.movementToken) return
          const actions: PixelActorAction[] = ['walkA', 'pass', 'walkB', 'pass']
          const stride = Math.floor((tween.progress * Math.max(distance, 24)) / 18)
          this.setActorPixelFrame(actorId, view, actions[stride % actions.length])
          view.container.setDepth(ACTOR_RENDER_DEPTH)
          this.syncActorLabels(view)
        },
        onComplete: () => walkSegment(index + 1),
      })
    }

    walkSegment(0)
  }

  private syncActorLabels(view: ActorView): void {
    const x = view.container.x
    const y = view.container.y
    view.name.setPosition(Math.round(x), Math.round(y + 16))
    view.status.setPosition(Math.round(x), Math.round(y + 36))
  }

  private refreshConversationVisuals(): void {
    const snapshot = this.pendingSnapshot
    if (!snapshot) return
    for (const conversation of snapshot.conversations) {
      if (conversation.status === 'open') this.upsertConversation(conversation)
    }
  }

  /** 会话交互圈重构：温润脉动光晕 + 典雅交互提示卡 */
  private upsertConversation(conversation: PublicConversation): void {
    const participantViews = conversation.participants
      .map((id) => this.actorViews.get(id))
      .filter((view): view is ActorView => Boolean(view))
    const positions = conversation.participants
      .map((id) => this.actorViews.get(id)?.container)
      .filter((container): container is Phaser.GameObjects.Container => Boolean(container))
      .map((container) => ({ x: container.x, y: container.y }))
    if (!positions.length) return

    const waitingForArrival = participantViews.length < conversation.participants.length || participantViews.some((view) => view.moving)
    const x = positions.reduce((sum, item) => sum + item.x, 0) / positions.length
    const y = positions.reduce((sum, item) => sum + item.y, 0) / positions.length

    let view = this.conversationViews.get(conversation.conversationId)
    if (!view) {
      const ring = this.add.graphics().setDepth(5).setInteractive(
        new Phaser.Geom.Circle(x, y, 64), Phaser.Geom.Circle.Contains,
      )
      ring.on('pointerdown', () => this.callbacks.onConversationClick(conversation.conversationId))

      const bg = this.add.graphics().setDepth(8)
      const label = this.add.text(x, y - 68, '', {
        fontFamily: 'STSong, "Microsoft YaHei", sans-serif',
        fontSize: '12px',
        color: '#2d3d28',
        fontStyle: 'bold',
      }).setOrigin(0.5).setDepth(9)

      view = { ring, label, bg }
      this.conversationViews.set(conversation.conversationId, view)
    }

    // 绘制柔和交谈光环
    view.ring.clear()
    if (!waitingForArrival) {
      view.ring.fillStyle(0x6e8761, 0.15).fillEllipse(x, y, 136, 96)
      view.ring.lineStyle(2, 0x8ea881, 0.85).strokeEllipse(x, y, 136, 96)
      view.ring.lineStyle(1, 0xd4e2ce, 0.5).strokeEllipse(x, y, 142, 102)

      // 绘制顶部会话标签背景框
      view.bg.clear()
      view.bg.fillStyle(0x19120c, 0.25).fillRoundedRect(x - 52, y - 80, 104, 24, 6)
      view.bg.fillStyle(0xf8f3e6, 0.95).fillRoundedRect(x - 54, y - 82, 108, 24, 6)
      view.bg.lineStyle(1, 0xb09677, 0.9).strokeRoundedRect(x - 54, y - 82, 108, 24, 6)
    } else {
      view.bg.clear()
    }

    view.ring.setVisible(!waitingForArrival)
    view.bg.setVisible(!waitingForArrival)
    view.label.setPosition(x, y - 70)
      .setText(`💬 ${conversation.participants.length}/3 · 点击聆听`)
      .setVisible(!waitingForArrival)
  }

  /** 现代气泡重构：矢量圆角阴影框 + 对话箭头小尾巴 */
  private playNextBubble(actorId: string): void {
    if (this.activeBubbleActors.has(actorId)) return
    const cue = this.bubbleQueue.shift(actorId)
    const actor = this.actorViews.get(actorId)
    if (!cue || !actor) return

    this.activeBubbleActors.add(actorId)
    const style = TONE_STYLE[cue.tone]
    const content = cue.tone === 'thinking' ? '···' : truncateBubble(cue.text)
    const layer = [...this.activeBubbleActors].filter((id) => {
      if (id === actorId) return false
      const other = this.actorViews.get(id)
      return other ? Phaser.Math.Distance.Between(actor.container.x, actor.container.y, other.container.x, other.container.y) < 150 : false
    }).length

    const textObj = this.add.text(0, 0, content, {
      fontFamily: 'STSong, "PingFang SC", "Microsoft YaHei", sans-serif',
      fontSize: '13px',
      color: style.text,
      fontStyle: '500',
      padding: { x: 4, y: 3 },
      wordWrap: { width: 180, useAdvancedWrap: true },
      maxLines: 2,
    }).setOrigin(0.5, 0.5).setDepth(72)

    const bw = Math.max(56, textObj.width + 20)
    const bh = textObj.height + 14
    const pos = clampBubblePosition(actor.container.x, actor.container.y - 82, bw, layer)

    const bubbleBox = this.add.graphics().setDepth(70)

    // 绘制气泡阴影
    bubbleBox.fillStyle(style.shadow, 0.25)
    bubbleBox.fillRoundedRect(pos.x - bw / 2 + 2, pos.y - bh + 2, bw, bh, 8)

    // 绘制气泡本体与微光边框
    bubbleBox.fillStyle(style.fill, 1)
    bubbleBox.fillRoundedRect(pos.x - bw / 2, pos.y - bh, bw, bh, 8)
    bubbleBox.lineStyle(1.5, style.stroke, 0.95)
    bubbleBox.strokeRoundedRect(pos.x - bw / 2, pos.y - bh, bw, bh, 8)

    // 对话尾巴（指向说话人）
    bubbleBox.fillStyle(style.fill, 1)
    bubbleBox.fillTriangle(pos.x - 5, pos.y, pos.x + 5, pos.y, pos.x, pos.y + 6)
    bubbleBox.lineStyle(1.5, style.stroke, 0.95)
    bubbleBox.lineBetween(pos.x - 5, pos.y, pos.x, pos.y + 6)
    bubbleBox.lineBetween(pos.x + 5, pos.y, pos.x, pos.y + 6)

    textObj.setPosition(pos.x, pos.y - bh / 2)

    const elements = [bubbleBox, textObj]
    this.activeBubbleObjects.set(actorId, elements)

    actor.ring.setVisible(true).setStrokeStyle(3, style.stroke, 0.95)
    this.setActorPixelFrame(actorId, actor, 'pass')

    if (!this.reducedMotion) {
      elements.forEach((el) => {
        el.setAlpha(0)
        this.tweens.add({ targets: el, alpha: 1, y: el.y - 4, duration: 180, ease: 'Back.easeOut' })
      })
    }

    this.time.delayedCall(bubbleDuration(content, this.reducedMotion), () => {
      const done = () => {
        elements.forEach((el) => el.destroy())
        this.activeBubbleObjects.delete(actorId)
        actor.ring.setVisible(false)
        this.activeBubbleActors.delete(actorId)
        this.setActorPixelFrame(actorId, actor, 'idle')
        this.playNextBubble(actorId)
      }
      if (this.reducedMotion) {
        done()
      } else {
        this.tweens.add({
          targets: elements,
          alpha: 0,
          y: '-=6',
          duration: 220,
          onComplete: done,
        })
      }
    })
  }

  private applyTimeVisual(snapshot: RunSnapshot): void {
    const visual = timeVisual(snapshot.worldTime)
    this.timeOverlay?.setFillStyle(visual.color, visual.alpha)

    if (this.timeLabel && this.timeCard && visual.label) {
      this.timeLabel.setText(`⌛ ${visual.label}`).setVisible(true)
      const tw = this.timeLabel.width + 20
      this.timeCard.clear().setVisible(true)
      this.timeCard.fillStyle(0x18100b, 0.35).fillRoundedRect(848 - tw + 2, 14 + 2, tw, 26, 6)
      this.timeCard.fillStyle(0x32241dee, 1).fillRoundedRect(848 - tw, 14, tw, 26, 6)
      this.timeCard.lineStyle(1, 0xbca079, 0.8).strokeRoundedRect(848 - tw, 14, tw, 26, 6)
    } else {
      this.timeLabel?.setVisible(false)
      this.timeCard?.setVisible(false)
    }
  }

  private disposeVisuals(): void {
    this.bubbleQueue.clear()
    this.activeBubbleActors.clear()
    this.logicalPositions.clear()
    this.tweens.killAll()
    this.time.removeAllEvents()
    for (const elements of this.activeBubbleObjects.values()) {
      elements.forEach((el) => el.destroy())
    }
    this.activeBubbleObjects.clear()
    for (const foreground of this.foregroundPieces) {
      foreground.clearMask(true)
      foreground.destroy()
    }
    for (const mask of this.foregroundMasks) mask.destroy()
    this.foregroundPieces.length = 0
    this.foregroundMasks.length = 0
  }
}
