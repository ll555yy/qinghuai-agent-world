import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from 'react-force-graph-2d'

import { actorPortraitCss } from '../../game/actorAssets'
import { getPixelActorAsset, pixelActorGraphPortraitRect } from '../../game/pixelActorAssets'
import {
  buildRelationshipGraph,
  filterRelationshipGraph,
  type RelationshipGraphFilter,
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

type PixelActorImages = Record<string, HTMLImageElement | undefined>

function usePixelActorImages(actorIds: readonly string[]): PixelActorImages {
  const actorKey = actorIds.join('|')
  const [images, setImages] = useState<PixelActorImages>({})

  useEffect(() => {
    if (typeof Image === 'undefined') return
    let cancelled = false
    const nextImages: PixelActorImages = {}
    const imagePromises = (actorKey ? actorKey.split('|') : []).map((actorId) => new Promise<void>((resolve) => {
      const asset = getPixelActorAsset(actorId)
      if (!asset) {
        resolve()
        return
      }
      const image = new Image()
      image.decoding = 'async'
      image.onload = () => {
        nextImages[actorId] = image
        resolve()
      }
      image.onerror = () => resolve()
      image.src = asset.url
    }))

    void Promise.all(imagePromises).then(() => {
      if (!cancelled) setImages(nextImages)
    })
    return () => {
      cancelled = true
    }
  }, [actorKey])

  return images
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(REDUCED_MOTION_QUERY).matches
}

function actorStatusLabel(status: string | undefined): string {
  const labels: Record<string, string> = {
    present: '在场',
    waiting: '等待中',
    approaching: '正在走近',
    inviting: '等待回应',
    chatting: '聊天中',
    departed: '已离开',
  }
  return status ? labels[status] ?? status : '未知'
}

function relationshipEndpointId(endpoint: unknown): string {
  if (typeof endpoint === 'string') return endpoint
  if (endpoint && typeof endpoint === 'object' && 'id' in endpoint) return String(endpoint.id)
  return ''
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
  const { elementRef, size } = useElementSize()
  const reducedMotion = useReducedMotion()
  const [filter, setFilter] = useState<RelationshipGraphFilter>('all')
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null)
  const graphRef = useRef<ForceGraphMethods<RelationshipNode, RelationshipLink> | undefined>(undefined)
  const mountedRef = useRef(false)
  const layoutReadyRef = useRef(false)
  const zoomTimerRef = useRef<number | null>(null)
  const graphSignature = snapshot
    ? JSON.stringify({
        actors: snapshot.actors,
        conversations: snapshot.conversations,
      })
    : '{"actors":[],"conversations":[]}'
  const fullGraphData = useMemo(() => buildRelationshipGraph(
    JSON.parse(graphSignature) as Parameters<typeof buildRelationshipGraph>[0],
  ), [graphSignature])
  const graphData = useMemo(
    () => filterRelationshipGraph(fullGraphData, filter),
    [filter, fullGraphData],
  )
  const pixelImages = usePixelActorImages(graphData.nodes.map((node) => node.id))
  const selectedActor = snapshot?.actors.find((actor) => actor.actorId === selectedActorId) ?? null
  const selectedState = selectedActorId ? snapshot?.actorStates[selectedActorId] : null
  const selectedRelationships = useMemo(() => {
    if (!selectedActorId || !snapshot) return []
    return fullGraphData.links.flatMap((link) => {
      const source = relationshipEndpointId(link.source)
      const target = relationshipEndpointId(link.target)
      if (source !== selectedActorId && target !== selectedActorId) return []
      const otherId = source === selectedActorId ? target : source
      const other = snapshot.actors.find((actor) => actor.actorId === otherId)
      return [{ ...link, otherId, otherName: other?.kind === 'player' ? '你' : other?.name ?? otherId }]
    })
  }, [fullGraphData.links, selectedActorId, snapshot])
  const selectedConversations = useMemo(() => {
    if (!selectedActorId || !snapshot) return []
    return snapshot.conversations
      .filter((conversation) => conversation.participants.includes(selectedActorId))
      .sort((first, second) => second.creationSeq - first.creationSeq)
      .map((conversation) => ({
        ...conversation,
        others: conversation.participants
          .filter((actorId) => actorId !== selectedActorId)
          .map((actorId) => {
            const actor = snapshot.actors.find((candidate) => candidate.actorId === actorId)
            return actor?.kind === 'player' ? '你' : actor?.name ?? actorId
          }),
      }))
  }, [selectedActorId, snapshot])
  const selectedWorldEvents = useMemo(() => {
    if (!selectedActor || !snapshot) return []
    return [...snapshot.worldEvents]
      .reverse()
      .filter((event) => event.sourceLabel.includes(selectedActor.name) || event.summary.includes(selectedActor.name))
  }, [selectedActor, snapshot])
  const activeActorIds = useMemo(() => {
    const ids = new Set<string>()
    for (const link of graphData.links) {
      if (!link.active) continue
      ids.add(String(link.source))
      ids.add(String(link.target))
    }
    return ids
  }, [graphData])

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

      graph.zoomToFit(reducedMotion ? 0 : 180, 70)
    }, reducedMotion ? 0 : 32)
  }, [clearZoomTimer, graphData, reducedMotion, size.height, size.width])

  useEffect(() => {
    layoutReadyRef.current = false
    clearZoomTimer()
    zoomTimerRef.current = window.setTimeout(() => {
      zoomTimerRef.current = null
      if (!mountedRef.current || !graphRef.current) return
      layoutReadyRef.current = true
      scheduleZoomToFit()
    }, reducedMotion ? 0 : 80)
    return clearZoomTimer
  }, [clearZoomTimer, reducedMotion, scheduleZoomToFit])

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
    const radius = node.kind === 'player' ? 25 : 23
    const active = activeActorIds.has(node.id)
    const image = pixelImages[node.id]
    const asset = getPixelActorAsset(node.id)

    context.save()
    context.globalAlpha = 1
    context.imageSmoothingEnabled = false

    if (active || selectedActorId === node.id) {
      context.beginPath()
      context.arc(x, y, radius + (selectedActorId === node.id ? 8 : 5) / scale, 0, 2 * Math.PI)
      context.fillStyle = selectedActorId === node.id ? 'rgba(168, 111, 75, 0.22)' : 'rgba(200, 121, 59, 0.18)'
      context.fill()
      context.lineWidth = 3 / scale
      context.strokeStyle = selectedActorId === node.id ? '#a86f4b' : '#c8793b'
      context.stroke()
    }

    context.beginPath()
    context.arc(x, y, radius, 0, 2 * Math.PI)
    context.fillStyle = node.kind === 'player' ? '#ead2b9' : '#dce4d5'
    context.fill()
    context.lineWidth = 2 / scale
    context.strokeStyle = node.kind === 'player' ? '#a86f4b' : '#667a5a'
    context.stroke()

    context.save()
    context.beginPath()
    context.arc(x, y, radius - 2 / scale, 0, 2 * Math.PI)
    context.clip()
    if (image?.complete && asset) {
      const runtimeSize = {
        width: image.naturalWidth || asset.sourceSize.width,
        height: image.naturalHeight || asset.sourceSize.height,
      }
      const source = pixelActorGraphPortraitRect(node.id, runtimeSize)
      if (!source) {
        context.restore()
        context.restore()
        return
      }
      const cellRatio = source.width / source.height
      const drawHeight = radius * 1.9
      const drawWidth = drawHeight * cellRatio
      context.drawImage(
        image,
        source.x,
        source.y,
        source.width,
        source.height,
        x - drawWidth / 2,
        y - drawHeight / 2,
        drawWidth,
        drawHeight,
      )
    } else {
      context.fillStyle = node.color
      context.font = `700 ${18 / scale}px Inter, Microsoft YaHei, sans-serif`
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.fillText(node.label.slice(0, 1), x, y)
    }
    context.restore()

    if (node.kind === 'player') {
      context.beginPath()
      context.arc(x + radius * 0.72, y - radius * 0.72, 7 / scale, 0, 2 * Math.PI)
      context.fillStyle = '#a86f4b'
      context.fill()
      context.fillStyle = '#fffaf0'
      context.font = `700 ${9 / scale}px Inter, Microsoft YaHei, sans-serif`
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.fillText('你', x + radius * 0.72, y - radius * 0.72)
    }

    const fontSize = 12 / scale
    context.font = `700 ${fontSize}px Inter, Microsoft YaHei, sans-serif`
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const labelY = y + radius + 11 / scale
    const labelWidth = context.measureText(node.label).width
    const labelHalfHeight = fontSize * 0.82
    context.fillStyle = 'rgba(255, 250, 240, 0.96)'
    context.strokeStyle = active ? '#c8793b' : 'rgba(104, 72, 50, 0.2)'
    context.lineWidth = 1 / scale
    context.beginPath()
    context.roundRect(
      x - labelWidth / 2 - 6 / scale,
      labelY - labelHalfHeight,
      labelWidth + 12 / scale,
      labelHalfHeight * 2,
      5 / scale,
    )
    context.fill()
    context.stroke()
    context.fillStyle = '#29251f'
    context.fillText(node.label, x, labelY)
    context.restore()
  }, [activeActorIds, pixelImages, selectedActorId])

  const paintLinkBadge = useCallback((rawLink: LinkObject<RelationshipNode, RelationshipLink>, context: CanvasRenderingContext2D, globalScale: number) => {
    const source = rawLink.source as NodeObject<RelationshipNode> | undefined
    const target = rawLink.target as NodeObject<RelationshipNode> | undefined
    if (!Number.isFinite(source?.x) || !Number.isFinite(source?.y) || !Number.isFinite(target?.x) || !Number.isFinite(target?.y)) return
    const scale = Math.max(globalScale, 0.01)
    const x = ((source?.x as number) + (target?.x as number)) / 2
    const y = ((source?.y as number) + (target?.y as number)) / 2
    const label = rawLink.active ? '正在聊天' : `共同聊天 · ${rawLink.conversationCount}`
    const fontSize = 10 / scale

    context.save()
    context.font = `700 ${fontSize}px Inter, Microsoft YaHei, sans-serif`
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const width = context.measureText(label).width + 9 / scale
    const height = 17 / scale
    context.fillStyle = rawLink.active ? '#c8793b' : '#77896b'
    context.beginPath()
    context.roundRect(x - width / 2, y - height / 2, width, height, height / 2)
    context.fill()
    context.fillStyle = '#fffaf0'
    context.fillText(label, x, y)
    context.restore()
  }, [])

  const handleNodeClick = useCallback((rawNode: NodeObject<RelationshipNode>) => {
    setSelectedActorId(String(rawNode.id))
  }, [])

  const activeLinkCount = graphData.links.filter((link) => link.active).length
  const centerGraph = useCallback(() => {
    layoutReadyRef.current = true
    scheduleZoomToFit()
  }, [scheduleZoomToFit])

  return (
    <div className="relationship-map-screen">
      <header className="relationship-map-header">
        <div className="relationship-map-heading">
          <span id="relationship-graph-title">人物关系地图</span>
          <small>{graphData.nodes.length} 个人物 · {graphData.links.length} 条公开关系 · 点击人物查看档案</small>
        </div>
        <div className="relationship-map-actions">
          <div className="relationship-filter" role="group" aria-label="关系图谱范围">
            <button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部关系</button>
            <button type="button" aria-pressed={filter === 'player'} onClick={() => setFilter('player')}>与我有关</button>
          </div>
          <button type="button" className="relationship-center-button" onClick={centerGraph}>重新居中</button>
          <button type="button" className="icon-button relationship-map-close" onClick={closePanel} aria-label="关闭关系图谱" title="关闭关系图谱">×</button>
        </div>
      </header>

      <div className="relationship-map-body">
        <div
          ref={elementRef}
          className="relationship-canvas relationship-map-canvas"
          role="img"
          tabIndex={0}
          aria-labelledby="relationship-graph-title"
          aria-describedby="relationship-graph-description"
        >
          <div className="relationship-legend relationship-map-legend" aria-label="图例">
            <span><i className="player-node" aria-hidden="true" />你</span>
            <span><i className="npc-node" aria-hidden="true" />人物</span>
            <span><i className="history-link" aria-hidden="true" />共同聊天</span>
            <span><i className="active-link" aria-hidden="true" />正在聊天</span>
          </div>
          <div className="relationship-canvas-render" aria-hidden="true">
            <ForceGraph2D<RelationshipNode, RelationshipLink>
              key={filter}
              ref={graphRef}
              width={size.width}
              height={size.height}
              graphData={graphData}
              backgroundColor="#efe4cf"
              nodeCanvasObjectMode={() => 'replace'}
              nodeCanvasObject={paintNode}
              nodeVal="nodeValue"
              nodeLabel={(node) => `${node.label} · ${node.role} · 点击查看经历`}
              linkLabel={(link) => link.label}
              linkColor={(link) => link.active ? '#c8793b' : '#8fa082'}
              linkWidth={(link) => link.active ? 4.2 : Math.min(2.2 + link.conversationCount * 0.55, 4)}
              linkLineDash={(link) => link.active ? null : [6, 4]}
              linkCanvasObjectMode={() => 'after'}
              linkCanvasObject={paintLinkBadge}
              linkDirectionalParticles={(link) => !reducedMotion && link.active ? 2 : 0}
              linkDirectionalParticleColor="#c8793b"
              linkDirectionalParticleWidth={3.2}
              cooldownTicks={reducedMotion ? 24 : 80}
              cooldownTime={reducedMotion ? 600 : 2000}
              onEngineStop={handleEngineStop}
              d3VelocityDecay={0.35}
              minZoom={0.35}
              maxZoom={5}
              onNodeClick={handleNodeClick}
              showPointerCursor={(item) => Boolean(item && 'kind' in item)}
            />
          </div>
        </div>

        {selectedActor ? (
          <aside className="relationship-actor-inspector" aria-label={`${selectedActor.name}的人物档案`}>
            <header>
              <div>
                <small>人物档案</small>
                <strong>{selectedActor.kind === 'player' ? '你' : selectedActor.name}</strong>
              </div>
              <button type="button" className="icon-button" onClick={() => setSelectedActorId(null)} aria-label="关闭人物档案">×</button>
            </header>
            <div className="relationship-actor-summary">
              <div className="relationship-portrait portrait-avatar" style={actorPortraitCss(selectedActor.actorId)} role="img" aria-label={`${selectedActor.name}的立绘`}>
                <span>{selectedActor.name.slice(0, 1)}</span>
              </div>
              <div>
                <strong>{selectedActor.role}</strong>
                <span>当前状态：{actorStatusLabel(selectedState?.status)}</span>
              </div>
            </div>
            <section>
              <h3>人物设定</h3>
              <p>{selectedActor.publicBackground}</p>
              {selectedActor.publicImpression.length ? (
                <div className="relationship-impressions">
                  {selectedActor.publicImpression.map((impression) => <span key={impression}>{impression}</span>)}
                </div>
              ) : null}
            </section>
            <section>
              <h3>公开关系</h3>
              {selectedRelationships.length ? (
                <ul className="relationship-detail-list">
                  {selectedRelationships.map((relation) => (
                    <li key={relation.otherId}>
                      <strong>{relation.otherName}</strong>
                      <span>{relation.active ? '正在聊天' : `共同聊天 ${relation.conversationCount} 次`}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="relationship-detail-empty">还没有形成公开关系。</p>}
            </section>
            <section>
              <h3>经历的互动</h3>
              {selectedConversations.length ? (
                <ol className="relationship-experience-list">
                  {selectedConversations.map((conversation) => (
                    <li key={conversation.conversationId}>
                      <span>{conversation.status === 'open' ? '正在参与' : '参与过'}{conversation.others.length ? `与 ${conversation.others.join('、')} 的` : ''}聊天</span>
                      <small>互动记录 #{conversation.creationSeq}</small>
                    </li>
                  ))}
                </ol>
              ) : <p className="relationship-detail-empty">暂时没有聊天经历。</p>}
              {selectedWorldEvents.length ? (
                <ol className="relationship-experience-list world-events">
                  {selectedWorldEvents.map((event) => (
                    <li key={event.eventId}>
                      <span>{event.summary}</span>
                      <small>Day {event.worldDay} · {event.at}</small>
                    </li>
                  ))}
                </ol>
              ) : null}
            </section>
            <small className="privacy-note">仅展示玩家已经观察到的公开信息与互动。</small>
          </aside>
        ) : null}
      </div>

      <p id="relationship-graph-description" className="relationship-sr-only">
        全屏画布展示公开共同聊天形成的人物关系，共有 {graphData.nodes.length} 个人物、{graphData.links.length} 条关系，其中 {activeLinkCount} 条正在聊天。点击人物可以查看公开人设、关系和经历。
      </p>
      <nav className="relationship-accessibility" aria-label="人物资料键盘入口">
        <p>键盘访问人物</p>
        <ul>
          {graphData.nodes.map((node) => (
            <li key={node.id}>
              <button type="button" onClick={() => setSelectedActorId(node.id)} aria-label={`查看${node.label}的公开资料与经历，${node.role}`}>
                <span>{node.label}</span>
                <small>{node.role}</small>
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <footer className="relationship-map-footer">
        <span>{graphData.links.length === 0 ? '还没有共同聊天记录；人物交流后会出现关系连线。' : '拖动地图 · 滚轮缩放 · 点击人物查看档案'}</span>
        <small>只呈现你亲历的互动，不显示隐藏目标、信任值或秘密关系。</small>
      </footer>
    </div>
  )
}
