import { useEffect, useRef } from 'react'
import Phaser from 'phaser'

import type { RunSnapshot } from '../api/types'
import type { SceneCue } from '../state/worldStore'
import { BookstoreScene } from './BookstoreScene'

interface WorldCanvasProps {
  snapshot: RunSnapshot
  onActorContext: (actorId: string, clientX: number, clientY: number) => void
  onConversationClick: (conversationId: string) => void
  sceneCue?: SceneCue | null
}

export function WorldCanvas({ snapshot, onActorContext, onConversationClick, sceneCue }: WorldCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<BookstoreScene | null>(null)
  const initialSnapshotRef = useRef(snapshot)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const scene = new BookstoreScene({
      onActorContext,
      onConversationClick,
      onReady: () => {
        host.dataset.ready = 'true'
      },
    })
    sceneRef.current = scene
    const game = new Phaser.Game({
      type: Phaser.AUTO,
      parent: host,
      width: 880,
      height: 534,
      backgroundColor: '#d8c5a5',
      scene,
      render: { antialias: true, pixelArt: false },
      scale: {
        mode: Phaser.Scale.FIT,
        autoCenter: Phaser.Scale.CENTER_BOTH,
      },
    })
    const handleContextMenu = (event: MouseEvent) => {
      event.preventDefault()
      scene.handleContextMenu(event.clientX, event.clientY)
    }
    game.canvas.addEventListener('contextmenu', handleContextMenu)
    scene.updateSnapshot(initialSnapshotRef.current)
    return () => {
      delete host.dataset.ready
      game.canvas.removeEventListener('contextmenu', handleContextMenu)
      sceneRef.current = null
      game.destroy(true)
    }
  }, [onActorContext, onConversationClick])

  useEffect(() => {
    sceneRef.current?.updateSnapshot(snapshot)
  }, [snapshot])

  useEffect(() => {
    if (sceneCue) sceneRef.current?.showBubble(sceneCue.actorId, sceneCue.text, sceneCue.tone, sceneCue.id)
  }, [sceneCue])

  return <div ref={hostRef} className="world-canvas" aria-label="慎之旧书店二维场景" data-actor-count={snapshot.actors.length} data-world-time={snapshot.worldTime.time} />
}
