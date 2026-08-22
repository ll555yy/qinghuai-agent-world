import { expect, test, type Page, type Route } from '@playwright/test'

import type { PublicAgenda, PublicMessage, RunSnapshot } from '../../api/types'

const agendas: PublicAgenda[] = [
  {
    agendaId: 'agenda_001_literary_society',
    ownerNpcId: 'npc_001',
    title: '青槐文社',
    publicSummary: '以公益书法课和老街故事会为核心，为街坊保留文化落脚地。',
  },
  {
    agendaId: 'agenda_003_cultural_operation',
    ownerNpcId: 'npc_003',
    title: '青槐巷邻里文创运营',
    publicSummary: '通过品牌和文创合作建立可持续经营方式。',
  },
]

function makeSnapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    runId: 'run_e2e',
    stateVersion: 2,
    eventSeq: 2,
    worldTime: { day: 1, hour: 9, minute: 0, time: '09:00', label: 'Day1 09:00', status: 'running' },
    playerAgendaId: 'agenda_001_literary_society',
    actors: [
      { actorId: 'player_001', kind: 'player', name: '玩家', role: '旧书店兼职帮手', publicBackground: '半个月前搬来青槐巷。', publicImpression: ['做事稳妥'] },
      { actorId: 'npc_001', kind: 'npc', name: '林慧兰', role: '退休中学语文教师', publicBackground: '常在社区活动中心教人写字。', publicImpression: ['懂事知礼的长辈'] },
      { actorId: 'npc_002', kind: 'npc', name: '沈星遥', role: '自由插画师', publicBackground: '毕业于美术学院，搬来本地半年。', publicImpression: ['安静'] },
    ],
    actorStates: {
      player_001: { status: 'present', position: { x: 1, y: 2 } },
      npc_001: { status: 'waiting', position: { x: 0, y: 0 } },
      npc_002: { status: 'waiting', position: { x: 2, y: 0 } },
    },
    conversations: [],
    pendingInvitations: [],
    pendingJoinRequests: [],
    worldEvents: [{
      eventId: 'event_day1_recovery_notice', worldDay: 1, at: '09:00', visibility: 'public', sourceLabel: '青槐巷居委会', summary: '旧书店需要在七天内提交方案。',
    }],
    currentWorldState: { proposalDeadline: 'Day7 18:00' },
    chapterEnded: false,
    chapterResolution: null,
    ...overrides,
  }
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function mockBackend(
  page: Page,
  options: { healthFailure?: boolean; initialConversation?: boolean; ended?: boolean; dayEndEvent?: boolean } = {},
) {
  const initialConversation = options.initialConversation
    ? [{ conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002'], status: 'open' as const }]
    : []
  const endingOverrides: Partial<RunSnapshot> = options.ended
    ? {
        chapterEnded: true,
        worldTime: { day: 7, hour: 18, minute: 0, time: '18:00', label: 'Day7 18:00', status: 'chapter_ended' },
        chapterResolution: {
          chapterId: 'chapter_01_proposal_deadline',
          branch: 'compromise_submitted',
          agendaResults: {
            agenda_001_literary_society: 'core_adopted',
            agenda_003_cultural_operation: 'partially_adopted',
          },
          playerTaskResult: 'completed',
          actorStances: { npc_001: 'support', npc_002: 'conditional' },
          playerHighlights: [{ messageId: 'msg_key', conversationId: 'conv_old', text: '先确认书店的底线。', createdAt: 'Day6 14:00' }],
        },
      }
    : {}
  let current = makeSnapshot({ conversations: initialConversation, ...endingOverrides })
  let conversationMessages: PublicMessage[] = options.initialConversation
    ? [{ messageId: 'msg_old', conversationId: 'conv_1', authorActorId: 'npc_001', text: '我们先把各自的底线说清楚。', createdAt: 'Day1 09:10' }]
    : []

  await page.route(/\/api\/(?:health|scenario|runs)(?:\/|$)/, async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/health') {
      if (options.healthFailure) return fulfillJson(route, { error: { code: 'offline', message: '后端尚未启动', details: {} } }, 503)
      return fulfillJson(route, { status: 'ok', processAlive: true, scenarioLoaded: true })
    }
    if (path === '/api/scenario/agendas') {
      return fulfillJson(route, { chapter: { chapterId: 'chapter_01_proposal_deadline', name: '七日方案期', startDay: 1, endDay: 7, endsAt: 'Day7 18:00' }, agendas, actors: current.actors.filter((actor) => actor.kind === 'npc') })
    }
    if (path === '/api/runs' && request.method() === 'POST') return fulfillJson(route, current, 201)
    if (path === '/api/runs/run_e2e' && request.method() === 'GET') return fulfillJson(route, current)
    if (path === '/api/runs/run_e2e/world/step') return fulfillJson(route, { run: current })
    if (path === '/api/runs/run_e2e/actors/npc_001') {
      return fulfillJson(route, { ...current.actors[1], status: current.actorStates.npc_001.status, position: current.actorStates.npc_001.position })
    }
    if (path === '/api/runs/run_e2e/invitations' && request.method() === 'POST') {
      const conversation = { conversationId: 'conv_chat', creationSeq: 2, participants: ['player_001', 'npc_001'], status: 'open' as const }
      current = { ...current, eventSeq: current.eventSeq + 1, conversations: [conversation], actorStates: { ...current.actorStates, player_001: { ...current.actorStates.player_001, status: 'chatting' }, npc_001: { ...current.actorStates.npc_001, status: 'chatting' } } }
      conversationMessages = [{ messageId: 'msg_open', conversationId: 'conv_chat', authorActorId: 'npc_001', text: '既然来了，就坐下谈谈吧。', createdAt: 'Day1 09:05' }]
      return fulfillJson(route, { run: current, conversation, invitation: { invitationId: 'invite_1', initiatorActorId: 'player_001', targetActorId: 'npc_001', status: 'accepted', conversationId: 'conv_chat' } })
    }
    if (path.endsWith('/conversations/conv_1/join')) {
      const conversation = { conversationId: 'conv_1', creationSeq: 1, participants: ['npc_001', 'npc_002', 'player_001'], status: 'open' as const }
      current = { ...current, eventSeq: current.eventSeq + 1, conversations: [conversation] }
      return fulfillJson(route, { run: current, conversation, messages: conversationMessages, joinRequest: { joinRequestId: 'join_1', conversationId: 'conv_1', applicantActorId: 'player_001', status: 'accepted', approverActorIds: ['npc_001', 'npc_002'], pendingPlayerDecision: false } })
    }
    const messageMatch = path.match(/\/conversations\/(conv_1|conv_chat)\/messages$/)
    if (messageMatch && request.method() === 'GET') return fulfillJson(route, { conversationId: messageMatch[1], messages: conversationMessages })
    if (messageMatch && request.method() === 'POST') {
      const data = request.postDataJSON() as { text: string }
      conversationMessages = [
        ...conversationMessages,
        { messageId: 'msg_player', conversationId: messageMatch[1], authorActorId: 'player_001', text: data.text, createdAt: 'Day1 09:06' },
        { messageId: 'msg_reply', conversationId: messageMatch[1], authorActorId: 'npc_001', text: '这件事可以再商量。', createdAt: 'Day1 09:07' },
      ]
      const conversation = current.conversations.find((item) => item.conversationId === messageMatch[1])
      return fulfillJson(route, { run: current, conversation, messages: conversationMessages })
    }
    if (path.includes('/participants/player_001') && request.method() === 'DELETE') {
      const existing = current.conversations[0]
      const conversation = { ...existing, participants: existing.participants.filter((id) => id !== 'player_001'), status: 'closed' as const, closeReason: 'fewer_than_two_participants' }
      current = { ...current, conversations: [conversation] }
      return fulfillJson(route, { run: current, conversation })
    }
    return fulfillJson(route, { error: { code: 'not_mocked', message: path, details: {} } }, 404)
  })

  await page.routeWebSocket(/\/ws\/runs\//, (ws) => {
    ws.send(JSON.stringify(current))
    if (options.dayEndEvent) {
      setTimeout(() => {
        current = {
          ...current,
          eventSeq: current.eventSeq + 1,
          stateVersion: current.stateVersion + 1,
          worldTime: { day: 1, hour: 18, minute: 0, time: '18:00', label: 'Day1 18:00', status: 'running' },
        }
        ws.send(JSON.stringify({ runId: current.runId, eventSeq: current.eventSeq, stateVersion: current.stateVersion, eventType: 'world_day_ended', payload: { worldTime: current.worldTime, reason: 'day_end' } }))
      }, 250)
    }
  })
}

async function enterWorld(page: Page, options: Parameters<typeof mockBackend>[1] = {}) {
  await mockBackend(page, options)
  await page.goto('/')
  await page.getByRole('button', { name: '查看可选立场' }).click()
  await expect(page.getByRole('heading', { name: '你更希望谁的主张被采纳？' })).toBeVisible()
  await page.getByRole('button', { name: /青槐文社/ }).click()
  await page.getByRole('button', { name: '进入青槐巷' }).click()
}

test('selects a task and enters the authoritative world', async ({ page }) => {
  await enterWorld(page)
  await expect(page.getByLabel('慎之旧书店二维场景')).toBeVisible()
  await expect(page.getByText('Day 1 / 7')).toBeVisible()
  await expect(page.getByText('青槐文社', { exact: true })).toBeVisible()
})

test('restores the authoritative Run after a page reload', async ({ page }) => {
  await enterWorld(page)
  await page.reload()
  await expect(page.getByLabel('慎之旧书店二维场景')).toBeVisible()
  await expect(page.getByText('Day 1 / 7')).toBeVisible()
})

test('opens a public actor card and completes a player chat', async ({ page }) => {
  await enterWorld(page)
  await expect(page.getByLabel('慎之旧书店二维场景')).toHaveAttribute('data-ready', 'true')
  const canvas = page.locator('canvas')
  const box = await canvas.boundingBox()
  if (!box) throw new Error('Phaser canvas was not rendered')
  await canvas.click({ button: 'right', position: { x: (126 / 880) * box.width, y: (286 / 534) * box.height } })
  await page.getByRole('button', { name: '了解信息' }).click()
  await expect(page.getByRole('heading', { name: '林慧兰' })).toBeVisible()
  await expect(page.getByText('目标、关系和秘密不会在这里显示')).toBeVisible()
  await page.getByRole('button', { name: '×' }).click()
  await page.waitForTimeout(700)

  const secondBox = await canvas.boundingBox()
  if (!secondBox) throw new Error('Phaser canvas disappeared')
  await canvas.click({ button: 'right', position: { x: (126 / 880) * secondBox.width, y: (286 / 534) * secondBox.height } })
  await page.getByRole('button', { name: '发出聊天邀请' }).click()
  await expect(page.getByText('既然来了，就坐下谈谈吧。')).toBeVisible()
  await page.getByPlaceholder('自由输入你想说的话……').fill('我希望大家先确认书店的底线。')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('我希望大家先确认书店的底线。')).toBeVisible()
  await expect(page.getByText('这件事可以再商量。')).toBeVisible()
  await page.getByRole('button', { name: '离开聊天' }).click()
  await expect(page.getByText('参与者不足两人，聊天结束。')).toBeVisible()
})

test('requests to join an existing chat and receives its earlier history', async ({ page }) => {
  await enterWorld(page, { initialConversation: true })
  await page.getByRole('button', { name: /林慧兰、沈星遥正在聊天/ }).click()
  await expect(page.getByText('你还没有加入这场聊天')).toBeVisible()
  await page.getByRole('button', { name: '申请加入聊天' }).click()
  await expect(page.getByText('我们先把各自的底线说清楚。')).toBeVisible()
  await expect(page.getByPlaceholder('自由输入你想说的话……')).toBeVisible()
})

test('shows a day-end transition from a WebSocket event', async ({ page }) => {
  await enterWorld(page, { dayEndEvent: true })
  await expect(page.getByText('今天的聊天已经结束')).toBeVisible()
  await expect(page.getByText('世界将从下一天 08:00 继续。')).toBeVisible()
})

test('renders the Day7 branch, agenda adoption, and player task result', async ({ page }) => {
  await enterWorld(page, { ended: true })
  await expect(page.getByRole('heading', { name: '一份妥协方案赶在截止前提交' })).toBeVisible()
  await expect(page.getByText('你的任务完成了')).toBeVisible()
  await expect(page.getByText('核心采纳')).toBeVisible()
  await expect(page.getByText('部分采纳')).toBeVisible()
  await expect(page.getByRole('heading', { name: '五人最终公开立场' })).toBeVisible()
  await expect(page.getByText('先确认书店的底线。')).toBeVisible()
})

test('keeps the task screen usable when the backend is unavailable', async ({ page }) => {
  await mockBackend(page, { healthFailure: true })
  await page.goto('/')
  await page.getByRole('button', { name: '查看可选立场' }).click()
  await expect(page.getByRole('alert')).toContainText('后端尚未启动')
  await expect(page.getByRole('button', { name: '← 返回' })).toBeEnabled()
})
