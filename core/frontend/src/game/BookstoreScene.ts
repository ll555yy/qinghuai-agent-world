import Phaser from 'phaser'

import type { PublicActor, PublicConversation, RunSnapshot } from '../api/types'
import {
  BOOKSTORE_OBSTACLES,
  findBookstoreActorSlot,
  findBookstorePath,
  type NavigationPoint,
} from './bookstorePathfinding'
import { ActorBubbleQueue, bubbleDuration, clampBubblePosition, truncateBubble, type BubbleTone } from './bubblePolicy'
import {
  applyPixelActorLayout,
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

/**
 * The server deliberately keeps positions as a small, data-only grid.  The
 * renderer translates that grid into the two physical rooms below without
 * changing the authoritative snapshot.  Keeping the room type explicit also
 * makes it harder for a conversation ring or a speech bubble to accidentally
 * straddle the wall between rooms.
 */
export type SceneRoom = 'front' | 'study'

/** Public scene contract used by layout checks and non-Phaser consumers. */
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

// Visible front faces copied from the flattened room artwork. Their depth is
// compared with each actor's foot Y, which restores top-down occlusion without
// requiring the approved background to be redrawn as a tile map.
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
  // The generated two-room background places the old shop below the divider.
  front: { left: 96, right: 780, top: 282, bottom: 470 },
  study: { left: 96, right: 780, top: 88, bottom: 210 },
}

const ACTOR_RADIUS = 26
const ROOM_DOORWAY = { left: 400, right: 480, top: 220, bottom: 296 }
const ACTOR_WALK_SPEED = 112

/**
 * Room routing for the compact server grid.  The initial server positions are
 * (0, 2, 6) in the study and (4, 8) in the front room; y >= 1 is the front
 * room's open floor, which is also where the player waits.  Unknown positions
 * use the nearest side so future movement events remain visible.
 */
export function sceneRoom(position: { x: number; y: number }, kind: PublicActor['kind'] = 'npc'): SceneRoom {
  // The logical position is authoritative for both NPCs and the player.  The
  // kind argument stays in the public helper signature for callers that pass
  // actor metadata, but must not pin the player to a home room after movement.
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

/** Translate an authoritative logical position into one room's walkable floor. */
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

  // y>=1 is the entrance/waiting area. The two y=0 server anchors are open
  // floor to the left and right of the large reading table and counter.
  if (position.y >= 1 || position.x <= 2) return { x: 440, y: 454 }
  return position.x < 6 ? { x: 240, y: 340 } : { x: 300, y: 420 }
}

const ACTOR_COLORS = [0x6f7d58, 0x526b7f, 0xa95f3d, 0x9e4e3b, 0x4d5555, 0x667a5a]
const TONE_STYLE: Record<BubbleTone, { fill: number; text: string; stroke: number }> = {
  npc: { fill: 0xf4ecd9, text: '#29251f', stroke: 0x57483b }, player: { fill: 0xdce6d6, text: '#253126', stroke: 0x526247 },
  invite: { fill: 0xf2dfb8, text: '#473823', stroke: 0xa57536 }, accept: { fill: 0xdce7d6, text: '#30422e', stroke: 0x667a5a },
  refuse: { fill: 0xa64b3c, text: '#fff7ed', stroke: 0x6f2f27 }, join: { fill: 0xd9e2e4, text: '#263b40', stroke: 0x587079 },
  leave: { fill: 0xe2d6c6, text: '#51463b', stroke: 0x756455 }, thinking: { fill: 0xeee5d4, text: '#675b4f', stroke: 0x8e806e },
  closing: { fill: 0xe8c49a, text: '#4d321f', stroke: 0xa35f32 }, system: { fill: 0x4d4a44, text: '#fffaf0', stroke: 0x29251f },
}

export function scenePosition(x: number, y: number): { x: number; y: number } {
  return { x: 126 + x * 82, y: 286 + y * 84 }
}

export class BookstoreScene extends Phaser.Scene {
  private readonly actorViews = new Map<string, ActorView>()
  private readonly conversationViews = new Map<string, { ring: Phaser.GameObjects.Ellipse; label: Phaser.GameObjects.Text }>()
  private readonly logicalPositions = new Map<string, { x: number; y: number }>()
  private readonly bubbleQueue = new ActorBubbleQueue()
  private readonly activeBubbleActors = new Set<string>()
  private readonly failedTextures = new Set<string>()
  private readonly foregroundPieces: Phaser.GameObjects.Image[] = []
  private readonly foregroundMasks: Phaser.GameObjects.Graphics[] = []
  private pendingSnapshot: RunSnapshot | null = null
  private timeOverlay?: Phaser.GameObjects.Rectangle
  private timeLabel?: Phaser.GameObjects.Text
  private reducedMotion = false

  constructor(private readonly callbacks: SceneCallbacks) { super('bookstore') }

  preload(): void {
    // This is a new asset derived from the approved visual-concepts layout;
    // the original single-room background remains available as a fallback.
    this.load.image('two-room-background', '/assets/scenes/shenzhi-bookstore-two-room.png')
    this.load.image('bookstore-background', '/assets/scenes/shenzhi-bookstore-background.jpg')
    queuePixelActorSheets(this.load)
    this.load.on('loaderror', (file: Phaser.Loader.File) => this.failedTextures.add(file.key))
  }

  create(): void {
    this.reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    registerPixelActorFrames(this.textures)
    this.cameras.main.setBackgroundColor('#d8c5a5')
    this.drawBookstore()
    this.timeOverlay = this.add.rectangle(440, 267, 880, 534, 0xfff2cf, 0.04).setDepth(40).setBlendMode(Phaser.BlendModes.MULTIPLY)
    this.timeLabel = this.add.text(842, 18, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '12px', color: '#fff7ed', backgroundColor: '#3d3028cc', padding: { x: 8, y: 5 } }).setOrigin(1, 0).setDepth(45).setVisible(false)
    if (this.pendingSnapshot) this.renderSnapshot(this.pendingSnapshot)
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => this.disposeVisuals())
    this.callbacks.onReady()
  }

  updateSnapshot(snapshot: RunSnapshot): void { this.pendingSnapshot = snapshot; if (this.sys.isActive()) this.renderSnapshot(snapshot) }

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
      const dx = sceneX - view.container.x; const dy = sceneY - view.container.y
      if (Math.abs(dx) > 54 || dy < -108 || dy > 64) continue
      const distance = dx * dx + dy * dy
      if (!closest || distance < closest.distance) closest = { actorId, distance }
    }
    if (closest) {
      this.callbacks.onActorContext(closest.actorId, clientX, clientY)
      return
    }

    // Keep the original public browser target usable while old clients/tests
    // transition to the room-aware coordinates.  It only applies to the
    // authoritative logical position (0,0), so arbitrary empty clicks stay
    // empty and do not open a random NPC menu.
    let legacyClosest: { actorId: string; distance: number } | null = null
    for (const [actorId, position] of this.logicalPositions) {
      const legacy = scenePosition(position.x, position.y)
      const dx = sceneX - legacy.x; const dy = sceneY - legacy.y
      if (Math.abs(dx) > 54 || Math.abs(dy) > 64) continue
      const distance = dx * dx + dy * dy
      if (!legacyClosest || distance < legacyClosest.distance) legacyClosest = { actorId, distance }
    }
    if (legacyClosest) this.callbacks.onActorContext(legacyClosest.actorId, clientX, clientY)
  }

  private drawBookstore(): void {
    const paper = this.add.graphics().setDepth(-3)
    paper.fillStyle(0xeadcc3, 1).fillRoundedRect(28, 24, 824, 486, 22)
    const backgroundKey = !this.failedTextures.has('two-room-background') && this.textures.exists('two-room-background')
      ? 'two-room-background'
      : 'bookstore-background'
    if (!this.failedTextures.has(backgroundKey) && this.textures.exists(backgroundKey)) {
      const background = this.add.image(440, 267, backgroundKey).setDepth(-2)
      const backgroundScale = Math.max(880 / background.width, 534 / background.height)
      background.setScale(backgroundScale)
      if (backgroundKey === 'two-room-background') this.drawFurnitureOccluders(backgroundKey, backgroundScale)
      this.add.rectangle(440, 267, 880, 534).setStrokeStyle(3, 0xe6d5b7, 0.8).setDepth(-1)
      this.drawRoomGuide()
      return
    }
    paper.fillStyle(0x684832, 1).fillRoundedRect(58, 54, 214, 152, 10).fillRoundedRect(608, 54, 214, 152, 10)
    paper.fillStyle(0xb68a5f, 1).fillRoundedRect(330, 186, 220, 96, 18)
    this.add.text(440, 222, '书店长桌', { fontFamily: 'STSong, serif', fontSize: '18px', color: '#684832' }).setOrigin(0.5)
    this.drawRoomGuide()
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

  private drawRoomGuide(): void {
    const guide = this.add.graphics().setDepth(1)
    // Keep the divider legible even when the generated texture is unavailable.
    guide.lineStyle(2, 0x3b2a20, 0.62)
      .lineBetween(48, 254, ROOM_DOORWAY.left, 254)
      .lineBetween(ROOM_DOORWAY.right, 254, 832, 254)
    guide.fillStyle(0xd6b477, 0.2).fillRoundedRect(ROOM_DOORWAY.left, ROOM_DOORWAY.top, ROOM_DOORWAY.right - ROOM_DOORWAY.left, ROOM_DOORWAY.bottom - ROOM_DOORWAY.top, 10)
    guide.lineStyle(1, 0xf6d397, 0.72)
      .lineBetween(ROOM_DOORWAY.left + 8, ROOM_DOORWAY.top + 6, ROOM_DOORWAY.left + 8, ROOM_DOORWAY.bottom - 6)
      .lineBetween(ROOM_DOORWAY.right - 8, ROOM_DOORWAY.top + 6, ROOM_DOORWAY.right - 8, ROOM_DOORWAY.bottom - 6)

  }

  private renderSnapshot(snapshot: RunSnapshot): void {
    const liveActorIds = new Set(snapshot.actors.map((actor) => actor.actorId))
    const targets = this.layoutTargets(snapshot)
    snapshot.actors.forEach((actor, index) => this.upsertActor(actor, index, snapshot, targets.get(actor.actorId)))
    for (const [actorId, view] of this.actorViews) if (!liveActorIds.has(actorId)) { view.container.destroy(true); this.actorViews.delete(actorId); this.logicalPositions.delete(actorId) }
    const liveConversationIds = new Set<string>()
    snapshot.conversations.filter((item) => item.status === 'open').forEach((conversation) => { liveConversationIds.add(conversation.conversationId); this.upsertConversation(conversation) })
    for (const [id, view] of this.conversationViews) if (!liveConversationIds.has(id)) { view.ring.destroy(); view.label.destroy(); this.conversationViews.delete(id) }
    this.applyTimeVisual(snapshot)
  }

  private layoutTargets(snapshot: RunSnapshot): Map<string, SceneTarget> {
    const preferredRooms = snapshot.actors.map((actor) => {
      const position = snapshot.actorStates[actor.actorId]?.position ?? { x: 0, y: 0 }
      return { actor, position, room: sceneRoom(position, actor.kind) }
    })
    const preferredByActor = new Map(preferredRooms.map((item) => [item.actor.actorId, item.room]))
    const conversationRooms = new Map<string, SceneRoom>()
    // A conversation is a single social space.  If an authoritative snapshot
    // briefly contains stale positions for its participants, keep the whole
    // group on one side of the divider while the next movement event catches
    // up.  Capacity balancing below only applies to actors outside a group.
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
      // NPCs should never start as a crowd in a single room.  The server's
      // canonical five-person layout already fits, while this guard keeps
      // imported/replayed snapshots readable if several NPCs share a grid row.
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
      const shadow = this.add.ellipse(0, 0, 30, 9, 0x241b15, 0.38)
      let sprite: Phaser.GameObjects.Sprite | undefined
      if (config && !this.failedTextures.has(config.textureKey) && this.textures.exists(config.textureKey)) {
        sprite = this.add.sprite(0, 0, config.textureKey, config.frame)
        applyPixelActorLayout(sprite, actor.actorId)
      }
      const fallbackBackground = this.add.circle(0, -28, 24, ACTOR_COLORS[index % ACTOR_COLORS.length])
        .setStrokeStyle(2, 0xf5ead5, 0.98)
        .setVisible(!sprite)
      const fallback = this.add.text(0, -28, actor.kind === 'player' ? '我' : actor.name.slice(0, 1), {
        fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '18px', fontStyle: 'bold', color: '#fffaf0',
      }).setOrigin(0.5).setVisible(!sprite)
      const ring = this.add.ellipse(0, 0, 38, 13).setStrokeStyle(2, 0xf2b861, 0).setVisible(false)
      const name = this.add.text(0, 13, actor.kind === 'player' ? '你' : actor.name, { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '11px', color: '#29251f', backgroundColor: '#f3ebd8e8', padding: { x: 4, y: 1 } }).setOrigin(0.5)
      const status = this.add.text(0, 29, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '9px', color: '#f9f0df', backgroundColor: '#3e342ccc', padding: { x: 3, y: 1 } }).setOrigin(0.5)
      const children: Phaser.GameObjects.GameObject[] = [shadow]
      if (sprite) children.push(sprite)
      children.push(fallbackBackground, fallback, ring, name, status)
      const container = this.add.container(target.x, target.y, children).setDepth(10 + target.y / 1000).setSize(72, 92).setInteractive({ useHandCursor: true })
      container.on('pointerdown', (pointer: Phaser.Input.Pointer) => { const e = pointer.event as MouseEvent; if (e.button === 2 || pointer.rightButtonDown()) this.callbacks.onActorContext(actor.actorId, e.clientX, e.clientY) })
      view = {
        container,
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
      // The same authoritative target may arrive in several world-step
      // snapshots.  Keep the existing route alive instead of restarting a
      // straight tween from the actor's intermediate position.
      if (!view.moving) this.setActorPixelFrame(actor.actorId, view, 'idle')
    }
    const labels: Record<string, string> = { approaching: '走近中', inviting: '等待回应', chatting: '聊天中', waiting: '等待', departed: '已离开' }
    const visibleStatus = view.moving ? 'approaching' : view.actorStatus
    view.status.setText(labels[visibleStatus] ?? '').setVisible(Boolean(labels[visibleStatus]))
    if (view.actorStatus === 'departed') {
      this.tweens.killTweensOf(view.container)
      view.moving = false
      view.movementToken += 1
      if (this.reducedMotion) view.container.setAlpha(0.2); else this.tweens.add({ targets: view.container, alpha: 0.2, duration: 420 })
    } else view.container.setAlpha(1)
    if (this.bubbleQueue.size(actor.actorId) && !this.activeBubbleActors.has(actor.actorId)) this.playNextBubble(actor.actorId)
  }

  private setActorPixelFrame(actorId: string, view: ActorView, action: PixelActorAction): void {
    if (!view.sprite) return
    const config = pixelActorSpriteConfig(actorId, view.direction, action)
    if (config && view.sprite.texture.key === config.textureKey) view.sprite.setFrame(config.frame)
  }

  private moveActorAlongPath(actorId: string, view: ActorView, target: SceneTarget): void {
    this.tweens.killTweensOf(view.container)
    view.movementToken += 1
    const movementToken = view.movementToken
    view.movementTarget = `${Math.round(target.x)}:${Math.round(target.y)}`

    if (this.reducedMotion) {
      view.container.setPosition(target.x, target.y)
      view.container.setDepth(10 + target.y / 1000)
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
        view.container.setDepth(10 + target.y / 1000)
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
          view.container.setDepth(10 + view.container.y / 1000)
        },
        onComplete: () => walkSegment(index + 1),
      })
    }

    walkSegment(0)
  }

  private refreshConversationVisuals(): void {
    const snapshot = this.pendingSnapshot
    if (!snapshot) return
    for (const conversation of snapshot.conversations) {
      if (conversation.status === 'open') this.upsertConversation(conversation)
    }
  }

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
    const x = positions.reduce((sum, item) => sum + item.x, 0) / positions.length; const y = positions.reduce((sum, item) => sum + item.y, 0) / positions.length
    let view = this.conversationViews.get(conversation.conversationId)
    if (!view) {
      const ring = this.add.ellipse(x, y, 132, 102, 0x667a5a, 0.12).setStrokeStyle(2, 0xdde8d5, 0.9).setDepth(5).setInteractive({ useHandCursor: true })
      ring.on('pointerdown', () => this.callbacks.onConversationClick(conversation.conversationId))
      const label = this.add.text(x, y - 68, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '12px', color: '#3c4d36', backgroundColor: '#f3ebd8e8', padding: { x: 6, y: 3 } }).setOrigin(0.5).setDepth(8)
      view = { ring, label }; this.conversationViews.set(conversation.conversationId, view)
    }
    view.ring.setPosition(x, y).setVisible(!waitingForArrival)
    view.label.setPosition(x, y - 68).setText(`${conversation.participants.length}/3 · 点击查看`).setVisible(!waitingForArrival)
  }

  private playNextBubble(actorId: string): void {
    if (this.activeBubbleActors.has(actorId)) return
    const cue = this.bubbleQueue.shift(actorId); const actor = this.actorViews.get(actorId)
    if (!cue || !actor) return
    this.activeBubbleActors.add(actorId)
    const style = TONE_STYLE[cue.tone]; const content = cue.tone === 'thinking' ? '···' : truncateBubble(cue.text)
    const layer = [...this.activeBubbleActors].filter((id) => { if (id === actorId) return false; const other = this.actorViews.get(id); return other ? Phaser.Math.Distance.Between(actor.container.x, actor.container.y, other.container.x, other.container.y) < 150 : false }).length
    const bubble = this.add.text(0, 0, content, { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '13px', color: style.text, backgroundColor: `#${style.fill.toString(16).padStart(6, '0')}`, padding: { x: 10, y: 7 }, wordWrap: { width: 188, useAdvancedWrap: true }, maxLines: 2 }).setOrigin(0.5, 1).setDepth(70).setStroke(`#${style.stroke.toString(16).padStart(6, '0')}`, 1)
    const pos = clampBubblePosition(actor.container.x, actor.container.y - 82, Math.min(208, Math.max(70, bubble.width)), layer)
    bubble.setPosition(pos.x, pos.y).setAlpha(this.reducedMotion ? 1 : 0)
    actor.ring.setVisible(true).setStrokeStyle(3, style.stroke, 0.95)
    this.setActorPixelFrame(actorId, actor, 'pass')
    if (!this.reducedMotion) this.tweens.add({ targets: bubble, alpha: 1, y: pos.y - 5, duration: 180, ease: 'Sine.easeOut' })
    this.time.delayedCall(bubbleDuration(content, this.reducedMotion), () => {
      const done = () => { bubble.destroy(); actor.ring.setVisible(false); this.activeBubbleActors.delete(actorId); this.setActorPixelFrame(actorId, actor, 'idle'); this.playNextBubble(actorId) }
      if (this.reducedMotion) done(); else this.tweens.add({ targets: bubble, alpha: 0, y: bubble.y - 8, duration: 260, onComplete: done })
    })
  }

  private applyTimeVisual(snapshot: RunSnapshot): void { const visual = timeVisual(snapshot.worldTime); this.timeOverlay?.setFillStyle(visual.color, visual.alpha); this.timeLabel?.setText(visual.label ?? '').setVisible(Boolean(visual.label)) }
  private disposeVisuals(): void {
    this.bubbleQueue.clear()
    this.activeBubbleActors.clear()
    this.logicalPositions.clear()
    this.tweens.killAll()
    this.time.removeAllEvents()
    for (const foreground of this.foregroundPieces) { foreground.clearMask(true); foreground.destroy() }
    for (const mask of this.foregroundMasks) mask.destroy()
    this.foregroundPieces.length = 0
    this.foregroundMasks.length = 0
  }
}
