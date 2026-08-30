import { render, screen } from '@testing-library/react'

import { App } from '../../../core/frontend/src/App'

describe('App', () => {
  it('shows the chapter premise', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '慎之旧书店，只剩七天。' })).toBeVisible()
    expect(screen.getByRole('button', { name: '查看可选立场' })).toBeEnabled()
  })
})
