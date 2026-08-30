import { reduceRunEvent, type WorldData } from '../../../core/frontend/src/state/eventReducer'

import { snapshot } from './fixtures'

function state(): WorldData {
  return { snapshot: snapshot(), messages: {}, invitations: {}, joinRequests: {} }
}

describe('reduceRunEvent', () => {
  it('updates authoritative time and ignores duplicate events', () => {
    const original = state()
    const event = {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'time_advanced',
      payload: {
        worldTime: { day: 1, hour: 10, minute: 0, time: '10:00', label: 'Day1 10:00', status: 'running' },
      },
    }
    const updated = reduceRunEvent(original, event)
    expect(updated.snapshot?.worldTime.time).toBe('10:00')
    expect(reduceRunEvent(updated, event)).toBe(updated)
  })

  it('merges visible messages once', () => {
    const event = {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'message_created',
      payload: { conversationId: 'conv_1', messageId: 'msg_1', authorActorId: 'npc_001', text: '先坐下说。' },
    }
    const updated = reduceRunEvent(state(), event)
    expect(updated.messages.conv_1).toHaveLength(1)
    expect(updated.messages.conv_1[0].text).toBe('先坐下说。')
  })

  it('creates an explicit participant system message', () => {
    const original = state()
    original.snapshot = snapshot({
      conversations: [{ conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002'], status: 'open' }],
    })
    const updated = reduceRunEvent(original, {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'conversation_participant_joined',
      payload: {
        conversation: { conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002', 'player_001'], status: 'open' },
        actorJoined: 'player_001',
      },
    })
    expect(updated.messages.conv_1[0]).toMatchObject({ system: true, systemActorId: 'player_001', systemAction: 'joined' })
  })

  it('keeps private activity without inventing dialogue', () => {
    const updated = reduceRunEvent(state(), {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'conversation_activity',
      payload: { conversationId: 'conv_1', reason: 'speech_unavailable' },
    })
    expect(updated.messages.conv_1).toBeUndefined()
  })

  it('applies an authoritative NPC movement and status position', () => {
    const updated = reduceRunEvent(state(), {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'actor_movement_completed',
      payload: { actorId: 'npc_001', position: { x: 4, y: 2 } },
    })
    expect(updated.snapshot?.actorStates.npc_001).toMatchObject({
      status: 'waiting',
      position: { x: 4, y: 2 },
    })
  })

  it('creates an explicit leave message and closes the conversation', () => {
    const original = state()
    original.snapshot = snapshot({
      conversations: [{ conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'player_001'], status: 'open' }],
    })
    const updated = reduceRunEvent(original, {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'conversation_participant_left',
      payload: {
        conversation: { conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001'], status: 'closed', closeReason: 'fewer_than_two_participants' },
        actorLeft: 'player_001',
      },
    })
    expect(updated.snapshot?.conversations[0].status).toBe('closed')
    expect(updated.messages.conv_1[0]).toMatchObject({ system: true, systemAction: 'left' })
  })

  it('keeps the departing actor when their departure closes the conversation', () => {
    const original = state()
    original.snapshot = snapshot({
      conversations: [{ conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002'], status: 'open' }],
    })
    const updated = reduceRunEvent(original, {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'conversation_closed',
      payload: {
        conversation: { conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001'], status: 'closed', closeReason: 'fewer_than_two_participants' },
        actorLeft: 'npc_002',
      },
    })
    expect(updated.messages.conv_1[0]).toMatchObject({
      system: true,
      systemActorId: 'npc_002',
      systemAction: 'left',
      text: '已经离开对话',
    })
  })

  it('adds a public conversation experience from a live event', () => {
    const updated = reduceRunEvent(state(), {
      runId: 'run_test',
      eventSeq: 3,
      stateVersion: 3,
      eventType: 'conversation_experience_recorded',
      payload: {
        experience: {
          experienceId: 'experience_seg_1',
          conversationId: 'conv_1',
          segmentId: 'seg_1',
          participantActorIds: ['player_001', 'npc_002'],
          worldDay: 1,
          at: '10:05',
          summary: '玩家与沈星遥讨论了书店未来。',
          evidenceMessageIds: ['msg_1', 'msg_2'],
        },
      },
    })

    expect(updated.snapshot?.conversationExperiences).toEqual([
      expect.objectContaining({
        experienceId: 'experience_seg_1',
        summary: '玩家与沈星遥讨论了书店未来。',
      }),
    ])
  })
})
