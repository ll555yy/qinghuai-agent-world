import { useWorldStore } from '../../../core/frontend/src/state/worldStore'

import { snapshot } from './fixtures'

describe('world store', () => {
  beforeEach(() => useWorldStore.getState().reset())

  it('restores player-facing pending requests from a snapshot', () => {
    useWorldStore.getState().setSnapshot(snapshot({
      pendingInvitations: [{
        invitationId: 'invite_1', initiatorActorId: 'npc_001', targetActorId: 'player_001', status: 'pending',
      }],
      pendingJoinRequests: [{
        joinRequestId: 'join_1', conversationId: 'conv_1', applicantActorId: 'npc_002', status: 'pending', approverActorIds: ['player_001'], pendingPlayerDecision: true,
      }],
    }))
    expect(useWorldStore.getState().invitations.invite_1.status).toBe('pending')
    expect(useWorldStore.getState().joinRequests.join_1.pendingPlayerDecision).toBe(true)
  })

  it('removes stale pending requests when a newer snapshot no longer contains them', () => {
    useWorldStore.getState().setInvitation({
      invitationId: 'invite_1', initiatorActorId: 'npc_001', targetActorId: 'player_001', status: 'pending',
    })
    useWorldStore.getState().setSnapshot(snapshot())
    expect(useWorldStore.getState().invitations.invite_1).toBeUndefined()
  })
})
