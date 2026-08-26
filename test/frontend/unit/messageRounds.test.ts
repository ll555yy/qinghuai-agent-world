import {
  appendConversationMessage,
  mergeConversationMessages,
  reduceRunEvent,
  type WorldData,
} from '../../../core/frontend/src/state/eventReducer'
import type { PublicMessage } from '../../../core/frontend/src/api/types'
import { useWorldStore } from '../../../core/frontend/src/state/worldStore'

import { snapshot } from './fixtures'

function state(): WorldData {
  return { snapshot: snapshot(), messages: {}, invitations: {}, joinRequests: {} }
}

function message(overrides: Partial<PublicMessage> = {}): PublicMessage {
  return {
    messageId: 'msg_1',
    conversationId: 'conv_1',
    authorActorId: 'npc_001',
    text: '先把事实说清楚。',
    ...overrides,
  }
}

function event(
  eventSeq: number,
  messageOverrides: Partial<PublicMessage> = {},
): Parameters<typeof reduceRunEvent>[1] {
  const next = message(messageOverrides)
  return {
    runId: 'run_test',
    eventSeq,
    stateVersion: eventSeq,
    eventType: 'message_created',
    payload: {
      conversationId: next.conversationId,
      messageId: next.messageId,
      authorActorId: next.authorActorId,
      text: next.text,
      ...(next.createdAt ? { createdAt: next.createdAt } : {}),
      ...(next.segmentId ? { segmentId: next.segmentId } : {}),
      ...(next.roundId ? { roundId: next.roundId } : {}),
      ...(next.roundSequence === undefined ? {} : { roundSequence: next.roundSequence }),
      ...(next.replyToMessageIds ? { replyToMessageIds: next.replyToMessageIds } : {}),
    },
  }
}

describe('message-driven chat message state', () => {
  it('preserves round metadata and appends multiple same-round events in arrival order', () => {
    const first = event(3, {
      messageId: 'msg_1',
      text: '先把事实说清楚。',
      roundId: 'round_1',
      roundSequence: 1,
      replyToMessageIds: ['msg_player'],
    })
    const second = event(4, {
      messageId: 'msg_2',
      authorActorId: 'npc_002',
      text: '我补充另一点。',
      roundId: 'round_1',
      roundSequence: 2,
      replyToMessageIds: ['msg_player'],
    })

    const afterFirst = reduceRunEvent(state(), first)
    const afterSecond = reduceRunEvent(afterFirst, second)
    const afterDuplicate = reduceRunEvent(afterSecond, first)

    expect(afterSecond.messages.conv_1).toEqual([
      expect.objectContaining({
        messageId: 'msg_1',
        roundId: 'round_1',
        roundSequence: 1,
        replyToMessageIds: ['msg_player'],
      }),
      expect.objectContaining({
        messageId: 'msg_2',
        roundId: 'round_1',
        roundSequence: 2,
      }),
    ])
    expect(afterDuplicate.messages.conv_1).toHaveLength(2)
  })

  it('keeps a live message when a stale history response arrives and reconciles its optimistic copy', () => {
    const optimistic = message({
      messageId: 'pending_1',
      authorActorId: 'player_001',
      text: '我的新消息',
      createdAt: 'Day1 09:00',
      deliveryStatus: 'sending',
    })
    const live = message({
      messageId: 'msg_live',
      text: '服务端已经逐条发布。',
      roundId: 'round_2',
      roundSequence: 2,
    })
    const canonicalPlayer = message({
      messageId: 'msg_player',
      authorActorId: 'player_001',
      text: '我的新消息',
      createdAt: 'Day1 09:00',
    })

    const existing = [optimistic, live]
    const merged = mergeConversationMessages(existing, [canonicalPlayer])

    expect(merged.map((item) => item.messageId)).toEqual(['msg_player', 'msg_live'])
    expect(merged[0]).not.toHaveProperty('deliveryStatus')
    expect(mergeConversationMessages(merged, [message({ messageId: 'msg_live' })])).toHaveLength(2)
  })

  it('appends response deltas without reordering already-arrived events', () => {
    const first = message({ messageId: 'msg_first', text: '先到达。' })
    const second = message({ messageId: 'msg_second', text: '后到达。' })

    expect(mergeConversationMessages([first], [second, first]).map((item) => item.messageId)).toEqual([
      'msg_first',
      'msg_second',
    ])
  })

  it('does not create a second message or scene cue when a message event is replayed', () => {
    const store = useWorldStore.getState()
    store.reset()
    store.setSnapshot(snapshot())
    const first = event(3, { messageId: 'msg_1', text: '只出现一次。' })
    store.applyEvent(first)
    const firstCue = useWorldStore.getState().sceneCue

    store.applyEvent(event(4, { messageId: 'msg_1', text: '只出现一次。' }))
    const afterDuplicate = useWorldStore.getState()

    expect(afterDuplicate.messages.conv_1).toHaveLength(1)
    expect(afterDuplicate.sceneCue).toBe(firstCue)
    expect(afterDuplicate.snapshot?.eventSeq).toBe(4)
  })

  it('replaces a matching optimistic message when a live canonical event arrives', () => {
    const optimistic = message({
      messageId: 'pending_1',
      authorActorId: 'player_001',
      text: '先确认边界。',
      createdAt: 'Day1 09:00',
      deliveryStatus: 'sending',
    })
    const canonical = message({
      messageId: 'msg_1',
      authorActorId: 'player_001',
      text: '先确认边界。',
      createdAt: 'Day1 09:00',
      roundId: 'round_1',
      roundSequence: 1,
    })

    const appended = appendConversationMessage([optimistic], canonical)

    expect(appended).toEqual([canonical])
  })
})
