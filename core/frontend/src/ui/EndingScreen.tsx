import { useState } from 'react'

import type { PublicAgenda } from '../api/types'
import { useUiStore } from '../state/uiStore'
import { useWorldStore } from '../state/worldStore'

const branchLabels: Record<string, { title: string; detail: string }> = {
  consensus_submitted: {
    title: '一份相对一致的方案按时提交',
    detail: '周慎之给出了明确授权，五人的核心承诺也终于落在了一起。',
  },
  compromise_submitted: {
    title: '一份妥协方案赶在截止前提交',
    detail: '分歧没有完全消失，但书店获得了继续争取的机会。',
  },
  no_submission: {
    title: '七天过去，方案没能提交',
    detail: '关键授权或合作承诺仍然不足，书店的危机继续逼近。',
  },
}

const adoptionLabels: Record<string, string> = {
  core_adopted: '核心采纳',
  partially_adopted: '部分采纳',
  not_adopted: '未采纳',
}

const stanceLabels: Record<string, string> = {
  unknown: '未公开表态',
  support: '支持提交',
  conditional: '有条件支持',
  oppose: '反对提交',
  withdrawn: '退出协作',
}

export function EndingScreen() {
  const snapshot = useWorldStore((state) => state.snapshot)
  const resetWorld = useWorldStore((state) => state.reset)
  const setPhase = useUiStore((state) => state.setPhase)
  const resolution = snapshot?.chapterResolution
  const [agendas] = useState<PublicAgenda[]>(() => {
    const raw = sessionStorage.getItem('qinghuai.agendas')
    if (!raw) return []
    try {
      return JSON.parse(raw) as PublicAgenda[]
    } catch {
      return []
    }
  })

  if (!resolution) return <div className="screen-message">结局数据尚未到达。</div>
  const branch = branchLabels[resolution.branch] ?? {
    title: '七日方案期结束',
    detail: '最终结果已经形成。',
  }
  const taskLabels: Record<string, string> = {
    completed: '你的任务完成了',
    partial: '你的任务部分完成',
    failed: '你的任务没有完成',
  }
  const actorMap = new Map(snapshot?.actors.map((actor) => [actor.actorId, actor]) ?? [])

  const restart = () => {
    sessionStorage.removeItem('qinghuai.runId')
    resetWorld()
    setPhase('intro')
  }

  return (
    <main className="ending-screen">
      <section className="ending-card">
        <p className="eyebrow">Day 7 · 18:00</p>
        <h1>{branch.title}</h1>
        <p className="ending-detail">{branch.detail}</p>
        {resolution.playerTaskResult ? (
          <div className={`task-result ${resolution.playerTaskResult}`}>
            {taskLabels[resolution.playerTaskResult] ?? resolution.playerTaskResult}
          </div>
        ) : (
          <div className="task-result observer">你以旁观者的身份见证了这七天。</div>
        )}

        <section className="resolution-list" aria-label="主张采纳结果">
          {Object.entries(resolution.agendaResults).map(([agendaId, result]) => {
            const agenda = agendas.find((item) => item.agendaId === agendaId)
            return (
              <article key={agendaId}>
                <div>
                  <strong>{agenda?.title ?? agendaId}</strong>
                  {agenda ? <span>提出人：{actorMap.get(agenda.ownerNpcId)?.name ?? agenda.ownerNpcId}</span> : null}
                </div>
                <span className={`adoption ${result}`}>{adoptionLabels[result] ?? result}</span>
              </article>
            )
          })}
        </section>
        {resolution.actorStances ? (
          <section className="ending-section" aria-label="五人最终公开立场">
            <h2>五人最终公开立场</h2>
            <div className="stance-grid">
              {Object.entries(resolution.actorStances).map(([actorId, stance]) => (
                <article key={actorId}>
                  <strong>{actorMap.get(actorId)?.name ?? actorId}</strong>
                  <span className={`stance ${stance}`}>{stanceLabels[stance] ?? stance}</span>
                </article>
              ))}
            </div>
          </section>
        ) : null}
        <section className="ending-section" aria-label="玩家关键聊天记录">
          <h2>你在七天中留下的发言</h2>
          {resolution.playerHighlights?.length ? (
            <div className="highlight-list">
              {resolution.playerHighlights.map((message) => (
                <blockquote key={message.messageId}>
                  <p>{message.text}</p>
                  {message.createdAt ? <cite>{message.createdAt}</cite> : null}
                </blockquote>
              ))}
            </div>
          ) : <p className="empty-state">你没有在聊天中发言，世界由 NPC 自行推进。</p>}
          <small>这些是可验证的参与记录；最终影响由上方公开立场和主张采纳结果体现。</small>
        </section>
        <button type="button" onClick={restart}>重新开始七日方案期</button>
      </section>
    </main>
  )
}
