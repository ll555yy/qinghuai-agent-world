import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: '../../test/frontend/e2e',
  // The PostgreSQL/FastAPI browser gate has its own config and is started
  // explicitly by CI. Keep the ordinary UI contract suite self-contained.
  testIgnore: ['**/full-stack.spec.ts'],
  fullyParallel: false,
  retries: 0,
  timeout: process.env.CI ? 60_000 : 30_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
})
