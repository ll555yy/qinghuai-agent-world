import { useEffect, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { PublicAgenda, ScenarioMetadata } from '../api/types'
import { useUiStore } from '../state/uiStore'
import { useWorldStore } from '../state/worldStore'

export function AgendaScreen() {
  const [metadata, setMetadata] = useState<ScenarioMetadata | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const setPhase = useUiStore((state) => state.setPhase)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.health(), api.scenario()])
      .then(([health, scenario]) => {
        if (cancelled) return
        if (health.status !== 'ok') throw new Error('后端尚未准备好，请确认数据库和场景已经启动。')
        setMetadata(scenario)
        sessionStorage.setItem('qinghuai.agendas', JSON.stringify(scenario.agendas))
        sessionStorage.setItem('qinghuai.scenarioActors', JSON.stringify(scenario.actors))
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取章节信息。')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      const snapshot = await api.createRun(selected)
      setSnapshot(snapshot)
      sessionStorage.setItem('qinghuai.runId', snapshot.runId)
      setPhase('world')
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : '无法创建世界。',
      )
    } finally {
      setStarting(false)
    }
  }

  return (
    <main className="agenda-screen">
      <header className="page-heading">
        <button type="button" className="text-button" onClick={() => setPhase('intro')}>
          ← 返回
        </button>
        <p className="eyebrow">选择任务 · 不限制你的发言</p>
        <h1>你更希望谁的主张被采纳？</h1>
        <p>你也可以作为旁观者进入。最后只根据真实聊天中形成的立场结算。</p>
      </header>

      {loading ? <div className="screen-message">正在读取青槐巷的公开方案……</div> : null}
      {error ? <div className="error-banner" role="alert">{error}</div> : null}

      <section className="agenda-grid" aria-label="可选主张">
        {metadata?.agendas.map((agenda) => (
          <AgendaCard
            key={agenda.agendaId}
            agenda={agenda}
            ownerName={metadata.actors.find((actor) => actor.actorId === agenda.ownerNpcId)?.name}
            active={selected === agenda.agendaId}
            onSelect={() => setSelected(agenda.agendaId)}
          />
        ))}
        {metadata ? (
          <button
            type="button"
            className={`agenda-card observer-card ${selected === null ? 'active' : ''}`}
            onClick={() => setSelected(null)}
          >
            <span className="agenda-owner">旁观路线</span>
            <strong>暂不支持任何一方</strong>
            <span>只观察和参与聊天，不设置个人任务成败。</span>
          </button>
        ) : null}
      </section>

      {metadata ? (
        <footer className="agenda-footer">
          <span>{metadata.chapter.name} · 截止 {metadata.chapter.endsAt}</span>
          <button type="button" disabled={starting} onClick={start}>
            {starting ? '正在进入……' : '进入青槐巷'}
          </button>
        </footer>
      ) : null}
    </main>
  )
}

function AgendaCard({ agenda, ownerName, active, onSelect }: { agenda: PublicAgenda; ownerName?: string; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`agenda-card ${active ? 'active' : ''}`} onClick={onSelect}>
      <span className="agenda-owner">{ownerName ? `${ownerName}的公开主张` : '公开主张'}</span>
      <strong>{agenda.title}</strong>
      <span>{agenda.publicSummary}</span>
    </button>
  )
}
