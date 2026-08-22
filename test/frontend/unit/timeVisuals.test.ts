import { timeVisual } from '../../../core/frontend/src/game/timeVisuals'

function at(hour: number, minute: number) {
  return { day: 1, hour, minute, time: `${hour}:${minute}`, label: '', status: 'running' }
}

describe('authoritative world-time visuals', () => {
  it.each([
    [9, 0, 'daylight'], [16, 0, 'golden'], [17, 0, 'closing'], [17, 50, 'countdown'], [18, 0, 'ended'],
  ])('maps %s:%s to %s', (hour, minute, stage) => {
    expect(timeVisual(at(hour as number, minute as number)).stage).toBe(stage)
  })

  it('derives the countdown only from the supplied snapshot time', () => {
    expect(timeVisual(at(17, 57)).label).toBe('距闭店 3 分钟')
  })
})
