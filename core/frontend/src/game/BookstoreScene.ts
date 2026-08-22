import Phaser from 'phaser'

import type { PublicActor, PublicConversation, RunSnapshot } from '../api/types'
import { actorAsset, NPC_PORTRAIT_SHEETS, selectPortraitState } from './actorAssets'
import { ActorBubbleQueue, bubbleDuration, clampBubblePosition, truncateBubble, type BubbleTone } from './bubblePolicy'
import { timeVisual } from './timeVisuals'

interface ActorView {
  container: Phaser.GameObjects.Container
  status: Phaser.GameObjects.Text
  portrait?: Phaser.GameObjects.Sprite | Phaser.GameObjects.Image
  maskSource?: Phaser.GameObjects.Graphics
  ring: Phaser.GameObjects.Arc
  actorStatus: string
}

interface SceneCallbacks {
  onActorContext: (actorId: string, clientX: number, clientY: number) => void
  onConversationClick: (conversationId: string) => void
  onReady: () => void
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
  private readonly bubbleQueue = new ActorBubbleQueue()
  private readonly activeBubbleActors = new Set<string>()
  private readonly failedTextures = new Set<string>()
  private pendingSnapshot: RunSnapshot | null = null
  private timeOverlay?: Phaser.GameObjects.Rectangle
  private timeLabel?: Phaser.GameObjects.Text
  private reducedMotion = false

  constructor(private readonly callbacks: SceneCallbacks) { super('bookstore') }

  preload(): void {
    this.load.image('bookstore-background', '/assets/scenes/shenzhi-bookstore-background.jpg')
    for (const asset of NPC_PORTRAIT_SHEETS) this.load.spritesheet(asset.key, asset.url, { frameWidth: 512, frameHeight: 1024 })
    this.load.image('portrait-player', '/assets/actors/player-neutral.png')
    this.load.on('loaderror', (file: Phaser.Loader.File) => this.failedTextures.add(file.key))
  }

  create(): void {
    this.reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.cameras.main.setBackgroundColor('#d8c5a5')
    this.drawBookstore()
    this.timeOverlay = this.add.rectangle(440, 267, 880, 534, 0xfff2cf, 0.04).setDepth(40).setBlendMode(Phaser.BlendModes.MULTIPLY)
    this.timeLabel = this.add.text(842, 18, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '12px', color: '#fff7ed', backgroundColor: '#3d3028cc', padding: { x: 8, y: 5 } }).setOrigin(1, 0).setDepth(45).setVisible(false)
    if (this.pendingSnapshot) this.renderSnapshot(this.pendingSnapshot)
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => this.disposeVisuals())
    this.callbacks.onReady()
  }

  updateSnapshot(snapshot: RunSnapshot): void { this.pendingSnapshot = snapshot; if (this.sys.isActive()) this.renderSnapshot(snapshot) }

  update(): void {
    for (const view of this.actorViews.values()) view.maskSource?.setPosition(view.container.x, view.container.y)
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
      const dx = sceneX - view.container.x; const dy = sceneY - view.container.y
      if (Math.abs(dx) > 54 || Math.abs(dy) > 64) continue
      const distance = dx * dx + dy * dy
      if (!closest || distance < closest.distance) closest = { actorId, distance }
    }
    if (closest) this.callbacks.onActorContext(closest.actorId, clientX, clientY)
  }

  private drawBookstore(): void {
    const paper = this.add.graphics().setDepth(-3)
    paper.fillStyle(0xeadcc3, 1).fillRoundedRect(28, 24, 824, 486, 22)
    if (!this.failedTextures.has('bookstore-background') && this.textures.exists('bookstore-background')) {
      const background = this.add.image(440, 267, 'bookstore-background').setDepth(-2)
      background.setScale(Math.max(880 / background.width, 534 / background.height))
      this.add.rectangle(440, 267, 880, 534).setStrokeStyle(3, 0xe6d5b7, 0.8).setDepth(-1)
      return
    }
    paper.fillStyle(0x684832, 1).fillRoundedRect(58, 54, 214, 152, 10).fillRoundedRect(608, 54, 214, 152, 10)
    paper.fillStyle(0xb68a5f, 1).fillRoundedRect(330, 186, 220, 96, 18)
    this.add.text(440, 222, '书店长桌', { fontFamily: 'STSong, serif', fontSize: '18px', color: '#684832' }).setOrigin(0.5)
  }

  private renderSnapshot(snapshot: RunSnapshot): void {
    const liveActorIds = new Set(snapshot.actors.map((actor) => actor.actorId))
    snapshot.actors.forEach((actor, index) => this.upsertActor(actor, index, snapshot))
    for (const [actorId, view] of this.actorViews) if (!liveActorIds.has(actorId)) { view.maskSource?.destroy(); view.container.destroy(true); this.actorViews.delete(actorId) }
    const liveConversationIds = new Set<string>()
    snapshot.conversations.filter((item) => item.status === 'open').forEach((conversation) => { liveConversationIds.add(conversation.conversationId); this.upsertConversation(conversation, snapshot) })
    for (const [id, view] of this.conversationViews) if (!liveConversationIds.has(id)) { view.ring.destroy(); view.label.destroy(); this.conversationViews.delete(id) }
    this.applyTimeVisual(snapshot)
  }

  private upsertActor(actor: PublicActor, index: number, snapshot: RunSnapshot): void {
    const actorState = snapshot.actorStates[actor.actorId]
    const target = scenePosition(actorState?.position.x ?? index, actorState?.position.y ?? 0)
    let view = this.actorViews.get(actor.actorId)
    if (!view) {
      const asset = actorAsset(actor.actorId)
      const background = this.add.circle(0, 0, 31, ACTOR_COLORS[index % ACTOR_COLORS.length]).setStrokeStyle(3, 0xf5ead5, 0.98)
      let portrait: Phaser.GameObjects.Sprite | Phaser.GameObjects.Image | undefined
      let maskSource: Phaser.GameObjects.Graphics | undefined
      if (asset.url && !this.failedTextures.has(asset.key) && this.textures.exists(asset.key)) {
        portrait = actor.kind === 'player' ? this.add.image(0, 0, asset.key) : this.add.sprite(0, 0, asset.key, 0)
        portrait.setDisplaySize(72, 72)
        portrait.setCrop(0, actor.kind === 'player' ? 70 : 40, actor.kind === 'player' ? 1024 : 512, actor.kind === 'player' ? 800 : 650)
        maskSource = this.add.graphics({ x: target.x, y: target.y }).setVisible(false).fillStyle(0xffffff).fillCircle(0, 0, 29)
        portrait.setMask(maskSource.createGeometryMask())
      }
      const fallback = this.add.text(0, -1, asset.fallback || actor.name.slice(0, 1), { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '18px', fontStyle: 'bold', color: '#fffaf0' }).setOrigin(0.5).setVisible(!portrait)
      const ring = this.add.circle(0, 0, 34).setStrokeStyle(3, 0xf2b861, 0).setVisible(false)
      const name = this.add.text(0, 39, actor.kind === 'player' ? '你' : actor.name, { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '13px', color: '#29251f', backgroundColor: '#f3ebd8e8', padding: { x: 5, y: 2 } }).setOrigin(0.5)
      const status = this.add.text(0, 59, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '11px', color: '#f9f0df', backgroundColor: '#3e342ccc', padding: { x: 4, y: 1 } }).setOrigin(0.5)
      const children: Phaser.GameObjects.GameObject[] = [background]
      if (portrait) children.push(portrait)
      children.push(fallback, ring, name, status)
      const container = this.add.container(target.x, target.y, children).setDepth(10).setSize(92, 110).setInteractive({ useHandCursor: true })
      container.on('pointerdown', (pointer: Phaser.Input.Pointer) => { const e = pointer.event as MouseEvent; if (e.button === 2 || pointer.rightButtonDown()) this.callbacks.onActorContext(actor.actorId, e.clientX, e.clientY) })
      view = { container, status, portrait, maskSource, ring, actorStatus: actorState?.status ?? 'present' }; this.actorViews.set(actor.actorId, view)
    }
    view.actorStatus = actorState?.status ?? 'present'
    if (view.container.x !== target.x || view.container.y !== target.y) {
      this.tweens.killTweensOf(view.container)
      if (this.reducedMotion) view.container.setPosition(target.x, target.y)
      else this.tweens.add({ targets: view.container, ...target, duration: 560, ease: 'Sine.easeInOut' })
    }
    const labels: Record<string, string> = { approaching: '走近中', inviting: '等待回应', chatting: '聊天中', waiting: '等待', departed: '已离开' }
    view.status.setText(labels[view.actorStatus] ?? '').setVisible(Boolean(labels[view.actorStatus]))
    if (view.portrait instanceof Phaser.GameObjects.Sprite && !this.activeBubbleActors.has(actor.actorId)) view.portrait.setFrame(actorAsset(actor.actorId, selectPortraitState(view.actorStatus)).frame)
    if (view.actorStatus === 'departed') {
      this.tweens.killTweensOf(view.container)
      if (this.reducedMotion) view.container.setAlpha(0.2); else this.tweens.add({ targets: view.container, alpha: 0.2, duration: 420 })
    } else view.container.setAlpha(1)
    if (this.bubbleQueue.size(actor.actorId) && !this.activeBubbleActors.has(actor.actorId)) this.playNextBubble(actor.actorId)
  }

  private upsertConversation(conversation: PublicConversation, snapshot: RunSnapshot): void {
    const positions = conversation.participants.map((id) => snapshot.actorStates[id]?.position).filter((p): p is { x: number; y: number } => Boolean(p)).map((p) => scenePosition(p.x, p.y))
    if (!positions.length) return
    const x = positions.reduce((sum, item) => sum + item.x, 0) / positions.length; const y = positions.reduce((sum, item) => sum + item.y, 0) / positions.length
    let view = this.conversationViews.get(conversation.conversationId)
    if (!view) {
      const ring = this.add.ellipse(x, y, 132, 102, 0x667a5a, 0.12).setStrokeStyle(2, 0xdde8d5, 0.9).setDepth(5).setInteractive({ useHandCursor: true })
      ring.on('pointerdown', () => this.callbacks.onConversationClick(conversation.conversationId))
      const label = this.add.text(x, y - 68, '', { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '12px', color: '#3c4d36', backgroundColor: '#f3ebd8e8', padding: { x: 6, y: 3 } }).setOrigin(0.5).setDepth(8)
      view = { ring, label }; this.conversationViews.set(conversation.conversationId, view)
    }
    view.ring.setPosition(x, y); view.label.setPosition(x, y - 68).setText(`${conversation.participants.length}/3 · 点击查看`)
  }

  private playNextBubble(actorId: string): void {
    if (this.activeBubbleActors.has(actorId)) return
    const cue = this.bubbleQueue.shift(actorId); const actor = this.actorViews.get(actorId)
    if (!cue || !actor) return
    this.activeBubbleActors.add(actorId)
    const style = TONE_STYLE[cue.tone]; const content = cue.tone === 'thinking' ? '···' : truncateBubble(cue.text)
    const layer = [...this.activeBubbleActors].filter((id) => { if (id === actorId) return false; const other = this.actorViews.get(id); return other ? Phaser.Math.Distance.Between(actor.container.x, actor.container.y, other.container.x, other.container.y) < 150 : false }).length
    const bubble = this.add.text(0, 0, content, { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '13px', color: style.text, backgroundColor: `#${style.fill.toString(16).padStart(6, '0')}`, padding: { x: 10, y: 7 }, wordWrap: { width: 188, useAdvancedWrap: true }, maxLines: 2 }).setOrigin(0.5, 1).setDepth(70).setStroke(`#${style.stroke.toString(16).padStart(6, '0')}`, 1)
    const pos = clampBubblePosition(actor.container.x, actor.container.y - 46, Math.min(208, Math.max(70, bubble.width)), layer)
    bubble.setPosition(pos.x, pos.y).setAlpha(this.reducedMotion ? 1 : 0)
    actor.ring.setVisible(true).setStrokeStyle(3, style.stroke, 0.95)
    if (actor.portrait instanceof Phaser.GameObjects.Sprite) actor.portrait.setFrame(actorAsset(actorId, cue.tone === 'refuse' ? 'tense' : 'speaking').frame)
    if (!this.reducedMotion) this.tweens.add({ targets: bubble, alpha: 1, y: pos.y - 5, duration: 180, ease: 'Sine.easeOut' })
    this.time.delayedCall(bubbleDuration(content, this.reducedMotion), () => {
      const done = () => { bubble.destroy(); actor.ring.setVisible(false); this.activeBubbleActors.delete(actorId); if (actor.portrait instanceof Phaser.GameObjects.Sprite) actor.portrait.setFrame(actorAsset(actorId, selectPortraitState(actor.actorStatus)).frame); this.playNextBubble(actorId) }
      if (this.reducedMotion) done(); else this.tweens.add({ targets: bubble, alpha: 0, y: bubble.y - 8, duration: 260, onComplete: done })
    })
  }

  private applyTimeVisual(snapshot: RunSnapshot): void { const visual = timeVisual(snapshot.worldTime); this.timeOverlay?.setFillStyle(visual.color, visual.alpha); this.timeLabel?.setText(visual.label ?? '').setVisible(Boolean(visual.label)) }
  private disposeVisuals(): void { this.bubbleQueue.clear(); this.activeBubbleActors.clear(); for (const view of this.actorViews.values()) view.maskSource?.destroy(); this.tweens.killAll(); this.time.removeAllEvents() }
}
