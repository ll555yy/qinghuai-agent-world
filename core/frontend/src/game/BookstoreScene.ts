import Phaser from 'phaser'

import type { PublicActor, PublicConversation, RunSnapshot } from '../api/types'

interface ActorView {
  container: Phaser.GameObjects.Container
  status: Phaser.GameObjects.Text
}

interface SceneCallbacks {
  onActorContext: (actorId: string, clientX: number, clientY: number) => void
  onConversationClick: (conversationId: string) => void
  onReady: () => void
}

const ACTOR_COLORS = [0x7b654f, 0x7a6d90, 0xa86f4b, 0x5f7e78, 0x58636d, 0x667a5a]

function scenePosition(x: number, y: number): { x: number; y: number } {
  return { x: 126 + x * 82, y: 286 + y * 84 }
}

export class BookstoreScene extends Phaser.Scene {
  private readonly actorViews = new Map<string, ActorView>()
  private readonly conversationViews = new Map<
    string,
    { ring: Phaser.GameObjects.Ellipse; label: Phaser.GameObjects.Text }
  >()
  private pendingSnapshot: RunSnapshot | null = null

  constructor(private readonly callbacks: SceneCallbacks) {
    super('bookstore')
  }

  create(): void {
    this.cameras.main.setBackgroundColor('#d8c5a5')
    this.drawBookstore()
    if (this.pendingSnapshot) this.renderSnapshot(this.pendingSnapshot)
    this.callbacks.onReady()
  }

  updateSnapshot(snapshot: RunSnapshot): void {
    this.pendingSnapshot = snapshot
    if (this.sys.isActive()) this.renderSnapshot(snapshot)
  }

  showBubble(actorId: string, text: string, tone: 'paper' | 'refuse' = 'paper'): void {
    const actor = this.actorViews.get(actorId)
    if (!actor) return
    const bubble = this.add
      .text(actor.container.x, actor.container.y - 58, text, {
        fontFamily: 'Microsoft YaHei, sans-serif',
        fontSize: '13px',
        color: tone === 'refuse' ? '#fff7ed' : '#29251f',
        backgroundColor: tone === 'refuse' ? '#a64b3c' : '#f3ebd8',
        padding: { x: 9, y: 6 },
      })
      .setOrigin(0.5)
      .setDepth(20)
    this.tweens.add({
      targets: bubble,
      alpha: 0,
      y: bubble.y - 12,
      delay: 1_500,
      duration: 350,
      onComplete: () => bubble.destroy(),
    })
  }

  handleContextMenu(clientX: number, clientY: number): void {
    const bounds = this.game.canvas.getBoundingClientRect()
    const sceneX = ((clientX - bounds.left) / bounds.width) * this.scale.width
    const sceneY = ((clientY - bounds.top) / bounds.height) * this.scale.height
    let closest: { actorId: string; distance: number } | null = null
    for (const [actorId, view] of this.actorViews) {
      const dx = sceneX - view.container.x
      const dy = sceneY - view.container.y
      if (Math.abs(dx) > 34 || Math.abs(dy) > 46) continue
      const distance = dx * dx + dy * dy
      if (!closest || distance < closest.distance) closest = { actorId, distance }
    }
    if (closest) this.callbacks.onActorContext(closest.actorId, clientX, clientY)
  }

  private drawBookstore(): void {
    const graphics = this.add.graphics()
    graphics.fillStyle(0xeadcc3, 1)
    graphics.fillRoundedRect(28, 24, 824, 486, 22)
    graphics.fillStyle(0x684832, 1)
    graphics.fillRoundedRect(58, 54, 214, 152, 10)
    graphics.fillRoundedRect(608, 54, 214, 152, 10)
    graphics.fillStyle(0xc79a68, 1)
    for (let row = 0; row < 4; row += 1) {
      graphics.fillRect(72, 70 + row * 31, 186, 8)
      graphics.fillRect(622, 70 + row * 31, 186, 8)
    }
    graphics.fillStyle(0xb68a5f, 1)
    graphics.fillRoundedRect(330, 186, 220, 96, 18)
    graphics.lineStyle(2, 0x8f6949, 0.7)
    graphics.strokeRoundedRect(330, 186, 220, 96, 18)
    this.add
      .text(440, 222, '书店长桌', {
        fontFamily: 'STSong, serif',
        fontSize: '18px',
        color: '#684832',
      })
      .setOrigin(0.5)
    this.add
      .text(66, 468, '慎之旧书店', {
        fontFamily: 'STSong, serif',
        fontSize: '24px',
        color: '#684832',
      })
      .setAlpha(0.74)
  }

  private renderSnapshot(snapshot: RunSnapshot): void {
    snapshot.actors.forEach((actor, index) => this.upsertActor(actor, index, snapshot))
    const liveConversationIds = new Set<string>()
    snapshot.conversations
      .filter((conversation) => conversation.status === 'open')
      .forEach((conversation) => {
        liveConversationIds.add(conversation.conversationId)
        this.upsertConversation(conversation, snapshot)
      })
    for (const [conversationId, view] of this.conversationViews) {
      if (!liveConversationIds.has(conversationId)) {
        view.ring.destroy()
        view.label.destroy()
        this.conversationViews.delete(conversationId)
      }
    }
  }

  private upsertActor(actor: PublicActor, index: number, snapshot: RunSnapshot): void {
    const actorState = snapshot.actorStates[actor.actorId]
    const target = scenePosition(actorState?.position.x ?? index, actorState?.position.y ?? 0)
    let view = this.actorViews.get(actor.actorId)
    if (!view) {
      const circle = this.add.circle(0, 0, 25, ACTOR_COLORS[index % ACTOR_COLORS.length])
      circle.setStrokeStyle(3, 0xf3ebd8, 0.95)
      const initial = this.add
        .text(0, -1, actor.kind === 'player' ? '我' : actor.name.slice(0, 1), {
          fontFamily: 'Microsoft YaHei, sans-serif',
          fontSize: '18px',
          fontStyle: 'bold',
          color: '#fffaf0',
        })
        .setOrigin(0.5)
      const name = this.add
        .text(0, 34, actor.kind === 'player' ? '你' : actor.name, {
          fontFamily: 'Microsoft YaHei, sans-serif',
          fontSize: '13px',
          color: '#29251f',
          backgroundColor: '#f3ebd8cc',
          padding: { x: 5, y: 2 },
        })
        .setOrigin(0.5)
      const status = this.add
        .text(0, 54, '', {
          fontFamily: 'Microsoft YaHei, sans-serif',
          fontSize: '11px',
          color: '#66584a',
        })
        .setOrigin(0.5)
      const container = this.add.container(target.x, target.y, [circle, initial, name, status])
      container.setSize(58, 80).setInteractive({ useHandCursor: true })
      container.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
        const sourceEvent = pointer.event as MouseEvent
        if (sourceEvent.button !== 2 && !pointer.rightButtonDown()) return
        this.callbacks.onActorContext(actor.actorId, sourceEvent.clientX, sourceEvent.clientY)
      })
      view = { container, status }
      this.actorViews.set(actor.actorId, view)
    }
    if (view.container.x !== target.x || view.container.y !== target.y) {
      this.tweens.killTweensOf(view.container)
      this.tweens.add({ targets: view.container, ...target, duration: 560, ease: 'Sine.easeInOut' })
    }
    const statusLabels: Record<string, string> = {
      approaching: '走近中',
      inviting: '等待回应',
      chatting: '聊天中',
      waiting: '等待',
      departed: '已离开',
    }
    view.status.setText(statusLabels[actorState?.status] ?? '')
    view.container.setAlpha(actorState?.status === 'departed' ? 0.24 : 1)
  }

  private upsertConversation(conversation: PublicConversation, snapshot: RunSnapshot): void {
    const positions = conversation.participants
      .map((actorId) => snapshot.actorStates[actorId]?.position)
      .filter((position): position is { x: number; y: number } => Boolean(position))
      .map((position) => scenePosition(position.x, position.y))
    if (!positions.length) return
    const x = positions.reduce((sum, item) => sum + item.x, 0) / positions.length
    const y = positions.reduce((sum, item) => sum + item.y, 0) / positions.length
    let view = this.conversationViews.get(conversation.conversationId)
    if (!view) {
      const ring = this.add.ellipse(x, y, 126, 98, 0x667a5a, 0.08)
      ring.setStrokeStyle(2, 0x667a5a, 0.68).setDepth(1).setInteractive({ useHandCursor: true })
      ring.on('pointerdown', () => this.callbacks.onConversationClick(conversation.conversationId))
      const label = this.add
        .text(x, y - 62, `${conversation.participants.length}/3 · 点击查看`, {
          fontFamily: 'Microsoft YaHei, sans-serif',
          fontSize: '12px',
          color: '#526247',
          backgroundColor: '#f3ebd8dd',
          padding: { x: 6, y: 3 },
        })
        .setOrigin(0.5)
        .setDepth(2)
      view = { ring, label }
      this.conversationViews.set(conversation.conversationId, view)
    }
    view.ring.setPosition(x, y)
    view.label.setPosition(x, y - 62).setText(`${conversation.participants.length}/3 · 点击查看`)
  }
}
