import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const viteCli = path.join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
const playwrightCli = path.join(frontendRoot, 'node_modules', '@playwright', 'test', 'cli.js')

const server = spawn(process.execPath, [viteCli, '--host', '127.0.0.1'], {
  cwd: frontendRoot,
  stdio: 'inherit',
})

async function waitForServer() {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`Vite exited with code ${server.exitCode}`)
    try {
      const response = await fetch('http://127.0.0.1:5173')
      if (response.ok) return
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  throw new Error('Timed out waiting for the E2E web server.')
}

function stopServer() {
  if (!server.pid || server.exitCode !== null) return
  server.kill()
  if (process.platform === 'win32' && server.exitCode === null) {
    spawnSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], { stdio: 'ignore' })
  }
}

try {
  await waitForServer()
  const testArguments = process.argv.slice(2)
  if (testArguments[0] === '--') testArguments.shift()
  const tests = spawn(process.execPath, [playwrightCli, 'test', ...testArguments], {
    cwd: frontendRoot,
    stdio: 'inherit',
    env: process.env,
  })
  const code = await new Promise((resolve) => tests.once('exit', (exitCode) => resolve(exitCode ?? 1)))
  process.exitCode = code
} catch (error) {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
} finally {
  stopServer()
}
