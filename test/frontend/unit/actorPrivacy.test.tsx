import { render, screen } from '@testing-library/react'

import { useUiStore } from '../../../core/frontend/src/state/uiStore'
import { useWorldStore } from '../../../core/frontend/src/state/worldStore'
import { ActorPanel } from '../../../core/frontend/src/ui/panels/ActorPanel'

import { snapshot } from './fixtures'

describe('actor public panel', () => {
  beforeEach(() => {
    useWorldStore.getState().reset()
    useUiStore.setState({ selectedActorId: 'npc_001', panel: 'actor' })
  })

  it('renders only the public projection even if an unknown private field is present', () => {
    const publicSnapshot = snapshot()
    const actorWithUnexpectedPrivateData = {
      ...publicSnapshot.actors[1],
      coreSecrets: ['不应该出现在界面里的秘密'],
      goals: ['隐藏目标'],
      trust: 2,
    }
    publicSnapshot.actors[1] = actorWithUnexpectedPrivateData
    useWorldStore.getState().setSnapshot(publicSnapshot)
    render(<ActorPanel />)
    expect(screen.getByRole('heading', { name: '林慧兰' })).toBeVisible()
    expect(screen.queryByText('不应该出现在界面里的秘密')).not.toBeInTheDocument()
    expect(screen.queryByText('隐藏目标')).not.toBeInTheDocument()
  })
})
