/**
 * Visual QA screenshots for the world scene.
 *
 * Serves the production build with `vite preview`, mocks the backend exactly
 * like the Playwright contract suite, walks intro -> agenda -> world and
 * captures full-page screenshots into project/visual-qa/.
 *
 * Usage: node scripts/visual-qa.mjs
 */
import { spawn, spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from '@playwright/test'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(frontendRoot, '..', '..')
const viteCli = path.join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
const outDir = path.join(repoRoot, 'project', 'visual-qa')
const baseURL = 'http://127.0.0.1:4173'

const snapshot = {
  runId: 'run_visual_qa',
  stateVersion: 2,
  eventSeq: 2,
  worldTime: { day: 1, hour: 9, minute: 0, time: '09:00', label: 'Day1 09:00', status: 'running' },
  playerAgendaId: 'agenda_001_literary_society',
  actors: [
    { actorId: 'player_001', kind: 'player', name: '玩家', role: '旧书店兼职帮手', publicBackground: '半个月前搬来青槐巷。', publicImpression: ['做事稳妥'] },
    { actorId: 'npc_001', kind: 'npc', name: '林慧兰', role: '退休中学语文教师', publicBackground: '常在社区活动中心教人写字。', publicImpression: ['懂事知礼的长辈'] },
    { actorId: 'npc_002', kind: 'npc', name: '沈星遥', role: '自由插画师', publicBackground: '毕业于美术学院，搬来本地半年。', publicImpression: ['安静'] },
    { actorId: 'npc_003', kind: 'npc', name: '赵磊', role: '销售主管', publicBackground: '外地来青槐巷打拼多年。', publicImpression: ['热情活络'] },
    { actorId: 'npc_004', kind: 'npc', name: '陈月', role: '社区医院护士', publicBackground: '本地人，做事麻利。', publicImpression: ['热心直爽'] },
    { actorId: 'npc_005', kind: 'npc', name: '周慎之', role: '旧书店老板', publicBackground: '经营旧书店十余年。', publicImpression: ['沉静可靠'] },
  ],
  actorStates: {
    player_001: { status: 'present', position: { x: 1, y: 2 } },
    npc_001: { status: 'chatting', position: { x: 0, y: 0 } },
    npc_002: { status: 'chatting', position: { x: 2, y: 0 } },
    npc_003: { status: 'waiting', position: { x: 4, y: 0 } },
    npc_004: { status: 'waiting', position: { x: 6, y: 0 } },
    npc_005: { status: 'waiting', position: { x: 8, y: 0 } },
  },
  conversations: [
    { conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002'], status: 'open' },
  ],
  conversationExperiences: [],
  pendingInvitations: [],
  pendingJoinRequests: [],
  worldEvents: [
    { eventId: 'event_day1_recovery_notice', worldDay: 1, at: '09:00', visibility: 'public', sourceLabel: '青槐巷居委会', summary: '旧书店需要在七天内提交方案。' },
  ],
  currentWorldState: { proposalDeadline: 'Day7 18:00' },
  chapterEnded: false,
  chapterResolution: null,
}

const agendas = [
  { agendaId: 'agenda_001_literary_society', ownerNpcId: 'npc_001', title: '青槐文社', publicSummary: '以公益书法课和老街故事会为核心，为街坊保留文化落脚地。' },
  { agendaId: 'agenda_003_cultural_operation', ownerNpcId: 'npc_003', title: '青槐巷邻里文创运营', publicSummary: '通过品牌和文创合作建立可持续经营方式。' },
]

async function mockBackend(page) {
  await page.route(/\/api\/(?:health|scenario|runs)(?:\/|$)/, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const fulfill = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/health') return fulfill({ status: 'ok', processAlive: true, scenarioLoaded: true })
    if (url.pathname === '/api/scenario/agendas') return fulfill({ chapter: { chapterId: 'chapter_01', name: '七日方案期', startDay: 1, endDay: 7, endsAt: 'Day7 18:00' }, agendas, actors: snapshot.actors.filter((actor) => actor.kind === 'npc') })
    if (url.pathname === '/api/runs' && request.method() === 'POST') return fulfill(snapshot, 201)
    if (url.pathname === '/api/runs/run_visual_qa' && request.method() === 'GET') return fulfill(snapshot)
    if (url.pathname.endsWith('/world/step')) return fulfill({ run: snapshot })
    if (url.pathname.endsWith('/conversations/conv_1/messages')) {
      return fulfill({ conversationId: 'conv_1', messages: [
        { messageId: 'msg_1', conversationId: 'conv_1', authorActorId: 'npc_001', text: '书法课下周就排在书店后间吧。', createdAt: 'Day1 09:05' },
        { messageId: 'msg_2', conversationId: 'conv_1', authorActorId: 'npc_002', text: '好呀，我来画一张招生海报。', createdAt: 'Day1 09:06' },
      ] })
    }
    return fulfill({ error: { code: 'not_mocked', message: url.pathname, details: {} } }, 404)
  })
  await page.routeWebSocket(/\/ws\/runs\//, (ws) => {
    ws.send(JSON.stringify(snapshot))
  })
}

const server = spawn(process.execPath, [viteCli, 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
  cwd: frontendRoot,
  stdio: 'inherit',
})

async function waitForServer() {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`vite preview exited with code ${server.exitCode}`)
    try {
      const response = await fetch(baseURL)
      if (response.ok) return
    } catch {
      // still starting
    }
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  throw new Error('Timed out waiting for vite preview.')
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
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await mockBackend(page)

  // Intro screen
  await page.goto(baseURL)
  await page.screenshot({ path: path.join(outDir, 'stardew-intro-1440x900.png') })

  // Agenda screen
  await page.getByRole('button', { name: '查看可选立场' }).click()
  await page.screenshot({ path: path.join(outDir, 'stardew-agenda-1440x900.png') })

  // World screen
  await page.getByRole('button', { name: /青槐文社/ }).click()
  await page.getByRole('button', { name: '进入青槐巷' }).click()
  const canvas = page.getByLabel('慎之旧书店二维场景')
  await canvas.waitFor({ state: 'visible' })
  await page.waitForFunction(() => document.querySelector('[data-ready="true"]'))
  await page.waitForTimeout(1_600)
  // Canonical README screenshot: every visual QA run refreshes this file so
  // the repository landing page cannot silently drift behind the current UI.
  await page.screenshot({ path: path.join(outDir, 'world-1440x900.png') })

  // World screen with the chat panel open
  await page.getByRole('button', { name: /林慧兰、沈星遥正在聊天/ }).click()
  await page.waitForTimeout(500)
  await page.screenshot({ path: path.join(outDir, 'stardew-world-chat-1440x900.png') })

  await browser.close()
  console.log('Visual QA screenshots written to', outDir)
} catch (error) {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
} finally {
  stopServer()
}
