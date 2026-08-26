import { useCallback, useEffect, useRef } from 'react'

import type { Notice } from '../state/worldStore'

const NOTICE_DURATION_MS: Record<Notice['tone'], number> = {
  info: 5_000,
  success: 5_000,
  warning: 7_000,
  error: 9_000,
}

interface NoticeToastProps {
  notice: Notice
  onDismiss: (id: string) => void
}

export function NoticeToast({ notice, onDismiss }: NoticeToastProps) {
  const remainingMs = useRef(NOTICE_DURATION_MS[notice.tone])
  const startedAt = useRef(0)
  const timer = useRef<number | null>(null)

  const clearTimer = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }, [])

  const startTimer = useCallback(() => {
    clearTimer()
    startedAt.current = Date.now()
    timer.current = window.setTimeout(() => onDismiss(notice.id), remainingMs.current)
  }, [clearTimer, notice.id, onDismiss])

  const pauseTimer = useCallback(() => {
    if (timer.current === null) return
    remainingMs.current = Math.max(0, remainingMs.current - (Date.now() - startedAt.current))
    clearTimer()
  }, [clearTimer])

  const resumeTimer = useCallback(() => {
    if (remainingMs.current <= 0) {
      onDismiss(notice.id)
      return
    }
    startTimer()
  }, [notice.id, onDismiss, startTimer])

  useEffect(() => {
    remainingMs.current = NOTICE_DURATION_MS[notice.tone]
    startTimer()
    return clearTimer
  }, [clearTimer, notice.tone, startTimer])

  return (
    <button
      type="button"
      className={`notice ${notice.tone}`}
      onClick={() => onDismiss(notice.id)}
      onMouseEnter={pauseTimer}
      onMouseLeave={resumeTimer}
      onFocus={pauseTimer}
      onBlur={resumeTimer}
    >
      {notice.text}
    </button>
  )
}
