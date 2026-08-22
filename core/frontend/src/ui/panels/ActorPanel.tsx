import { useEffect, useState } from 'react'

import { api } from '../../api/client'
import type { PublicActor } from '../../api/types'
import { useUiStore } from '../../state/uiStore'
import { useWorldStore } from '../../state/worldStore'

type ActorDetail = PublicActor & { status?: string; position?: { x: number; y: number } }

export function ActorPanel() {
  const snapshot = useWorldStore((state) => state.snapshot)
  const actorId = useUiStore((state) => state.selectedActorId)
  const closePanel = useUiStore((state) => state.closePanel)
  const [detail, setDetail] = useState<ActorDetail | null>(null)
  const runId = snapshot?.runId
  const fallback: ActorDetail | null = snapshot?.actors.find((actor) => actor.actorId === actorId) ?? null
  const visibleDetail = detail?.actorId === actorId ? detail : fallback

  useEffect(() => {
    if (!runId || !actorId) return
    let cancelled = false
    api.actor(runId, actorId)
      .then((result) => {
        if (!cancelled) setDetail(result)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [actorId, runId])

  return (
    <div className="panel-content actor-panel">
      <header><span>公开信息</span><button type="button" className="icon-button" onClick={closePanel}>×</button></header>
      {visibleDetail ? (
        <>
          <div className="large-avatar">{visibleDetail.name.slice(0, 1)}</div>
          <h2>{visibleDetail.name}</h2>
          <p className="role-label">{visibleDetail.role}</p>
          <p>{visibleDetail.publicBackground}</p>
          <h3>大家对他的印象</h3>
          <ul className="impression-list">
            {visibleDetail.publicImpression.map((impression) => <li key={impression}>{impression}</li>)}
          </ul>
          {visibleDetail.status ? <p className="public-status">当前状态：{visibleDetail.status}</p> : null}
          <small className="privacy-note">目标、关系和秘密不会在这里显示，请从聊天和行为中自行推测。</small>
        </>
      ) : <div className="screen-message">正在读取公开资料……</div>}
    </div>
  )
}
