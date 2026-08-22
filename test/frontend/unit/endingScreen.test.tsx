import { render, screen } from '@testing-library/react'

import { useWorldStore } from '../../../core/frontend/src/state/worldStore'
import { EndingScreen } from '../../../core/frontend/src/ui/EndingScreen'

import { agendas, snapshot } from './fixtures'

describe('Day 7 ending', () => {
  beforeEach(() => {
    useWorldStore.getState().reset()
    sessionStorage.setItem('qinghuai.agendas', JSON.stringify(agendas))
  })

  it('renders branch, public stance, adoption, player result, and chat record', () => {
    useWorldStore.getState().setSnapshot(snapshot({
      chapterEnded: true,
      worldTime: { day: 7, hour: 18, minute: 0, time: '18:00', label: 'Day7 18:00', status: 'chapter_ended' },
      chapterResolution: {
        chapterId: 'chapter_01_proposal_deadline',
        branch: 'consensus_submitted',
        agendaResults: { agenda_001_literary_society: 'core_adopted' },
        playerTaskResult: 'completed',
        actorStances: { npc_001: 'support' },
        playerHighlights: [{ messageId: 'msg_1', conversationId: 'conv_1', text: '先守住书店的底线。', createdAt: 'Day6 14:00' }],
      },
    }))
    render(<EndingScreen />)
    expect(screen.getByRole('heading', { name: '一份相对一致的方案按时提交' })).toBeVisible()
    expect(screen.getByText('核心采纳')).toBeVisible()
    expect(screen.getByText('支持提交')).toBeVisible()
    expect(screen.getByText('你的任务完成了')).toBeVisible()
    expect(screen.getByText('先守住书店的底线。')).toBeVisible()
  })
})
