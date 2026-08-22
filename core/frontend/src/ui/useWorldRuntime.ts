import { useEffect, useRef } from 'react'

import { ApiError, api } from '../api/client'
import { RunSocket } from '../api/socket'
import { useUiStore } from '../state/uiStore'
import { useWorldStore } from '../state/worldStore'

export function useWorldRuntime() {
  const snapshot = useWorldStore((state) => state.snapshot)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)
  const applyEvent = useWorldStore((state) => state.applyEvent)
  const setSocketStatus = useWorldStore((state) => state.setSocketStatus)
  const setError = useWorldStore((state) => state.setError)
  const setPhase = useUiStore((state) => state.setPhase)
  const eventSeqRef = useRef(snapshot?.eventSeq ?? 0)
  const steppingRef = useRef(false)

  useEffect(() => {
    eventSeqRef.current = snapshot?.eventSeq ?? eventSeqRef.current
    if (snapshot?.chapterEnded && snapshot.chapterResolution) setPhase('ending')
  }, [setPhase, snapshot])

  useEffect(() => {
    if (!snapshot?.runId || snapshot.chapterEnded) return
    const socket = new RunSocket({
      runId: snapshot.runId,
      afterSeq: () => eventSeqRef.current,
      onSnapshot: (next) => {
        eventSeqRef.current = Math.max(eventSeqRef.current, next.eventSeq)
        setSnapshot(next)
      },
      onEvent: (event) => {
        eventSeqRef.current = Math.max(eventSeqRef.current, event.eventSeq)
        applyEvent(event)
      },
      onStatus: setSocketStatus,
      onError: setError,
    })
    socket.connect()
    return () => socket.stop()
  }, [applyEvent, setError, setSnapshot, setSocketStatus, snapshot?.chapterEnded, snapshot?.runId])

  useEffect(() => {
    if (!snapshot?.runId || snapshot.chapterEnded) return
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== 'visible' || steppingRef.current) return
      steppingRef.current = true
      try {
        const result = await api.stepWorld(snapshot.runId)
        setSnapshot(result.run)
        setError(null)
      } catch (reason) {
        setError(reason instanceof ApiError ? reason.message : '世界时间暂时停止，正在等待重新同步。')
        try {
          setSnapshot(await api.getRun(snapshot.runId))
        } catch {
          // The visible error and WebSocket state provide the normal retry path.
        }
      } finally {
        steppingRef.current = false
      }
    }, 2_000)
    return () => window.clearInterval(timer)
  }, [setError, setSnapshot, snapshot?.chapterEnded, snapshot?.runId])
}
