import { ActorBubbleQueue, bubbleDuration, clampBubblePosition, truncateBubble } from '../../../core/frontend/src/game/bubblePolicy'

describe('scene bubble policy', () => {
  it('keeps a FIFO queue per actor and rejects duplicate cue ids', () => {
    const queue = new ActorBubbleQueue()
    expect(queue.enqueue({ id: 'a', actorId: 'npc_001', text: '第一句', tone: 'npc' })).toBe(true)
    expect(queue.enqueue({ id: 'b', actorId: 'npc_001', text: '第二句', tone: 'npc' })).toBe(true)
    expect(queue.enqueue({ id: 'a', actorId: 'npc_001', text: '重复', tone: 'npc' })).toBe(false)
    expect(queue.shift('npc_001')?.text).toBe('第一句')
    expect(queue.shift('npc_001')?.text).toBe('第二句')
  })

  it('truncates scene text while keeping display duration bounded', () => {
    expect(truncateBubble('  一句   有空格的话  ')).toBe('一句 有空格的话')
    expect(truncateBubble('这是一段会超过场景气泡显示上限但完整版本仍应保留在聊天面板中的长消息', 18)).toHaveLength(18)
    expect(bubbleDuration('短')).toBe(1900)
    expect(bubbleDuration('长'.repeat(100))).toBe(4600)
    expect(bubbleDuration('任意文字', true)).toBe(1600)
  })

  it('keeps bubbles inside the canvas and stacks nearby speakers upward', () => {
    expect(clampBubblePosition(2, 90, 200, 0).x).toBe(112)
    expect(clampBubblePosition(878, 90, 200, 0).x).toBe(768)
    expect(clampBubblePosition(440, 90, 120, 1).y).toBe(56)
  })
})
