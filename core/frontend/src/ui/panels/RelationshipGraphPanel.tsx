import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type NodeObject } from 'react-force-graph-2d'

import { PLAYER_ACTOR_ID } from '../../api/types'
import {
  buildRelationshipGraph,
  type RelationshipLink,
  type RelationshipNode,
} from '../../graph/relationshipGraph'
import { useUiStore } from '../../state/uiStore'
import { useWorldStore } from '../../state/worldStore'

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

interface ElementSize {
  width: number
  height: number
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(REDUCED_MOTION_QUERY).matches
}

function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY)
    const onChange = (event: MediaQueryListEvent) => setReducedMotion(event.matches)
    const supportsEventListener = typeof mediaQuery.addEventListener === 'function'

    if (supportsEventListener) {
      mediaQuery.addEventListener('change', onChange)
    } else {
      mediaQuery.addListener(onChange)
    }

    return () => {
      if (supportsEventListener) {
        mediaQuery.removeEventListener('change', onChange)
      } else {
        mediaQuery.removeListener(onChange)
      }
    }
  }, [])

  return reducedMotion
}

function useElementSize() {
  const elementRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<ElementSize>({ width: 1, height: 320 })

  useEffect(() => {
    const element = elementRef.current
    if (!element) return

    let active = true
    const update = () => {
      if (!active) return
      const nextSize = {
        width: Math.max(Math.floor(element.clientWidth), 1),
        height: Math.max(Math.floor(element.clientHeight), 1),
      }
      setSize((current) => current.width === nextSize.width && current.height === nextSize.height ? current : nextSize)
    }

    update()

    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(update)
      observer.observe(element)
      return () => {
        active = false
        observer.disconnect()
      }
    }

    window.addEventListener('resize', update)
    return () => {
      active = false
      window.removeEventListener('resize', update)
    }
  }, [])

  return { elementRef, size }
}

export default function RelationshipGraphPanel() {
  const snapshot = useWorldStore((state) => state.snapshot)
  const closePanel = useUiStore((state) => state.closePanel)
  const openActor = useUiStore((state) => state.openActor)
  const { elementRef, size } = useElementSize()
  const reducedMotion = useReducedMotion()
  const graphRef = useRef<ForceGraphMethods<RelationshipNode, RelationshipLink> | undefined>(undefined)
  const mountedRef = useRef(false)
  const layoutReadyRef = useRef(false)
  const zoomTimerRef = useRef<number | null>(null)
  const graphData = useMemo(
    () => snapshot ? buildRelationshipGraph(snapshot) : { nodes: [], links: [] },
    [snapshot],
  )

  const clearZoomTimer = useCallback(() => {
    if (zoomTimerRef.current === null) return
    window.clearTimeout(zoomTimerRef.current)
    zoomTimerRef.current = null
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      layoutReadyRef.current = false
      clearZoomTimer()
      graphRef.current?.pauseAnimation()
      graphRef.current = undefined
    }
  }, [clearZoomTimer])

  useEffect(() => {
    layoutReadyRef.current = false
    clearZoomTimer()
  }, [clearZoomTimer, graphData])

  const scheduleZoomToFit = useCallback(() => {
    if (
      !mountedRef.current ||
      !layoutReadyRef.current ||
      graphData.nodes.length === 0 ||
      size.width <= 1 ||
      size.height <= 1
    ) return

    clearZoomTimer()
    zoomTimerRef.current = window.setTimeout(() => {
      zoomTimerRef.current = null
      if (!mountedRef.current || !layoutReadyRef.current) return

      const graph = graphRef.current
      if (!graph) return
      const boundingBox = graph.getGraphBbox()
      if (!boundingBox || !boundingBox.x.every(Number.isFinite) || !boundingBox.y.every(Number.isFinite)) return

      graph.zoomToFit(reducedMotion ? 0 : 180, 38)
    }, reducedMotion ? 0 : 32)
  }, [clearZoomTimer, graphData, reducedMotion, size.height, size.width])

  useEffect(() => {
    if (layoutReadyRef.current) scheduleZoomToFit()
  }, [scheduleZoomToFit])

  const handleEngineStop = useCallback(() => {
    if (!mountedRef.current) return
    layoutReadyRef.current = true
    scheduleZoomToFit()
  }, [scheduleZoomToFit])

  const paintNode = useCallback((rawNode: NodeObject<RelationshipNode>, context: CanvasRenderingContext2D, globalScale: number) => {
    const node = rawNode as RelationshipNode & { x?: number; y?: number }
    const x = Number.isFinite(node.x) ? node.x as number : 0
    const y = Number.isFinite(node.y) ? node.y as number : 0
    const scale = Math.max(globalScale, 0.01)
    const radius = node.kind === 'player' ? 11 : 9
    context.save()
    context.globalAlpha = 1
    context.beginPath()
    context.arc(x, y, radius, 0, 2 * Math.PI)
    context.fillStyle = node.color
    context.fill()
    context.lineWidth = 2 / scale
    context.strokeStyle = '#fffaf0'
    context.stroke()

    const fontSize = 12 / scale
    context.font = `600 ${fontSize}px Inter, Microsoft YaHei, sans-serif`
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const labelY = y + radius + 8 / scale
    const labelWidth = context.measureText(node.label).width
    context.fillStyle = 'rgba(255, 250, 240, 0.9)'
    context.fillRect(
      x - labelWidth / 2 - 3 / scale,
      labelY - fontSize * 0.65,
      labelWidth + 6 / scale,
      fontSize * 1.3,
    )
    context.fillStyle = '#29251f'
    context.fillText(node.label, x, labelY)
    context.restore()
  }, [])

  const handleNodeClick = useCallback((rawNode: NodeObject<RelationshipNode>) => {
    const actorId = String(rawNode.id)
    if (actorId !== PLAYER_ACTOR_ID) openActor(actorId)
  }, [openActor])

  const activeLinkCount = graphData.links.filter((link) => link.active).length

  return (
    <div className="panel-content relationship-panel">
      <header>
        <div><span id="relationship-graph-title">人物关系图谱</span><small>随你亲历的聊天实时生长</small></div>
        <button type="button" className="icon-button" onClick={closePanel} aria-label="关闭关系图谱" title="关闭关系图谱">×</button>
      </header>
      <div className="relationship-legend" aria-label="图例">
        <span><i className="player-node" aria-hidden="true" />你</span>
        <span><i className="npc-node" aria-hidden="true" />邻里人物</span>
        <span><i className="active-link" aria-hidden="true" />正在聊天</span>
      </div>
      <div
        ref={elementRef}
        className="relationship-canvas"
        role="img"
        tabIndex={0}
        aria-labelledby="relationship-graph-title"
        aria-describedby="relationship-graph-description"
      >
        <div className="relationship-canvas-render" aria-hidden="true">
          <ForceGraph2D<RelationshipNode, RelationshipLink>
            ref={graphRef}
            width={size.width}
            height={size.height}
            graphData={graphData}
            backgroundColor="#f3ebd8"
            nodeCanvasObjectMode={() => 'replace'}
            nodeCanvasObject={paintNode}
            nodeVal="nodeValue"
            nodeLabel={(node) => `${node.label} · ${node.role}`}
            linkLabel={(link) => link.label}
            linkColor={(link) => link.active ? '#c8793b' : '#9cac92'}
            linkWidth={(link) => link.active ? 2.4 : Math.min(1 + link.conversationCount * 0.45, 2.2)}
            linkDirectionalParticles={(link) => !reducedMotion && link.active ? 2 : 0}
            linkDirectionalParticleColor="#c8793b"
            linkDirectionalParticleWidth={2.5}
            cooldownTicks={reducedMotion ? 24 : 80}
            cooldownTime={reducedMotion ? 600 : 2000}
            onEngineStop={handleEngineStop}
            d3VelocityDecay={0.35}
            minZoom={0.7}
            maxZoom={5}
            onNodeClick={handleNodeClick}
            showPointerCursor={(item) => Boolean(item && 'kind' in item && item.kind !== 'player')}
          />
        </div>
      </div>
      <p id="relationship-graph-description" className="relationship-sr-only">
        画布展示公开共同聊天形成的人物关系，共有 {graphData.nodes.length} 个人物、{graphData.links.length} 条关系，其中 {activeLinkCount} 条正在聊天。图谱不表示信任、好感或其他隐藏关系。使用下方键盘人物入口可查看 NPC 的公开资料。
      </p>
      <nav className="relationship-accessibility" aria-label="人物资料键盘入口">
        <p>键盘访问人物</p>
        <ul>
          {graphData.nodes.map((node) => (
            <li key={node.id}>
              {node.kind === 'player' ? (
                <span>{node.label}（玩家）</span>
              ) : (
                <button type="button" onClick={() => openActor(node.id)} aria-label={`查看${node.label}的公开资料，${node.role}`}>
                  <span>{node.label}</span>
                  <small>{node.role}</small>
                </button>
              )}
            </li>
          ))}
        </ul>
      </nav>
      {graphData.links.length === 0 ? (
        <p className="relationship-empty">还没有共同聊天记录。发起或加入聊天后，人物之间会出现连线。</p>
      ) : (
        <p className="relationship-hint">拖动人物整理位置，滚轮缩放；点击 NPC 可查看公开资料。</p>
      )}
      <small className="privacy-note">图谱只呈现你亲历的互动，不会泄露 NPC 的隐藏目标、信任值或秘密关系。</small>
    </div>
  )
}
