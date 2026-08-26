import { act, fireEvent, render, screen } from '@testing-library/react'

import { NoticeToast } from '../../../core/frontend/src/ui/NoticeToast'

describe('notice toast', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('dismisses an ordinary notice after five seconds', () => {
    const onDismiss = vi.fn()
    render(<NoticeToast notice={{ id: 'notice_1', text: '聊天邀请已接受。', tone: 'success' }} onDismiss={onDismiss} />)

    act(() => vi.advanceTimersByTime(4_999))
    expect(onDismiss).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(1))
    expect(onDismiss).toHaveBeenCalledWith('notice_1')
  })

  it('pauses the countdown while the notice is hovered', () => {
    const onDismiss = vi.fn()
    render(<NoticeToast notice={{ id: 'notice_2', text: '请先看完这条提醒。', tone: 'warning' }} onDismiss={onDismiss} />)
    const notice = screen.getByRole('button', { name: '请先看完这条提醒。' })

    act(() => vi.advanceTimersByTime(2_000))
    fireEvent.mouseEnter(notice)
    act(() => vi.advanceTimersByTime(10_000))
    expect(onDismiss).not.toHaveBeenCalled()
    fireEvent.mouseLeave(notice)
    act(() => vi.advanceTimersByTime(5_000))
    expect(onDismiss).toHaveBeenCalledWith('notice_2')
  })
})
