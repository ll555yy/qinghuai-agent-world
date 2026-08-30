import { actorAsset, actorPortraitCss, selectPortraitState } from '../../../core/frontend/src/game/actorAssets'

describe('actor portrait assets', () => {
  it('maps all public actors to stable production assets', () => {
    expect(actorAsset('npc_001', 'neutral')).toMatchObject({ url: '/assets/actors/lin-huilan-states.jpg', frame: 0 })
    expect(actorAsset('npc_002', 'speaking')).toMatchObject({ url: '/assets/actors/shen-xingyao-states.jpg', frame: 1 })
    expect(actorAsset('npc_005', 'tense')).toMatchObject({ url: '/assets/actors/zhou-shenzhi-states.jpg', frame: 2 })
    expect(actorAsset('player_001').url).toBe('/assets/actors/player-neutral.png')
  })

  it('uses a readable fallback for unknown actors', () => {
    expect(actorAsset('npc_unknown')).toEqual({ key: 'portrait-fallback', url: '', frame: 0, fallback: '?' })
    expect(actorPortraitCss('npc_unknown')).toEqual({})
  })

  it('selects speaking and tense frames without exposing private state', () => {
    expect(selectPortraitState('waiting')).toBe('neutral')
    expect(selectPortraitState('waiting', true)).toBe('speaking')
    expect(selectPortraitState('departed')).toBe('tense')
  })
})
