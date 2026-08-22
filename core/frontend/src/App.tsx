import { lazy, Suspense, useEffect, useState } from 'react'

import { api } from './api/client'
import { useUiStore } from './state/uiStore'
import { useWorldStore } from './state/worldStore'
import { AgendaScreen } from './ui/AgendaScreen'
import { EndingScreen } from './ui/EndingScreen'
import { IntroScreen } from './ui/IntroScreen'

const WorldScreen = lazy(() => import('./ui/WorldScreen'))

export function App() {
  const phase = useUiStore((state) => state.phase)
  const setPhase = useUiStore((state) => state.setPhase)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)
  const [restoring, setRestoring] = useState(() => Boolean(sessionStorage.getItem('qinghuai.runId')))

  useEffect(() => {
    const runId = sessionStorage.getItem('qinghuai.runId')
    if (!runId) return
    let cancelled = false
    api.getRun(runId)
      .then((snapshot) => {
        if (cancelled) return
        setSnapshot(snapshot)
        setPhase(snapshot.chapterEnded ? 'ending' : 'world')
      })
      .catch(() => {
        if (!cancelled) sessionStorage.removeItem('qinghuai.runId')
      })
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })
    return () => {
      cancelled = true
    }
  }, [setPhase, setSnapshot])

  if (restoring) return <div className="screen-message">正在恢复上次的青槐巷进度……</div>

  if (phase === 'intro') return <IntroScreen />
  if (phase === 'agenda') return <AgendaScreen />
  if (phase === 'ending') return <EndingScreen />
  return (
    <Suspense fallback={<div className="screen-message">正在推开旧书店的门……</div>}>
      <WorldScreen />
    </Suspense>
  )
}
