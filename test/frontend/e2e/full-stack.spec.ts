import { expect, test, type Page } from '@playwright/test'

type JsonRecord = Record<string, any>

const PRIVATE_FIELD_NAMES = [
  'coreSecrets',
  'privateMemory',
  'authoringNote',
  'trust',
  'affinity',
  'tension',
]

async function enterRealWorld(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '查看可选立场' }).click()
  await expect(page.getByRole('heading', { name: '你更希望谁的主张被采纳？' })).toBeVisible()
  await page.getByRole('button', { name: /青槐文社/ }).click()
  await page.getByRole('button', { name: '进入青槐巷' }).click()
  await expect(page.getByLabel('慎之旧书店二维场景')).toHaveAttribute('data-ready', 'true')
  await expect(page.getByText('Day 1 / 7')).toBeVisible()
  await expect(page.getByText('已连接')).toBeVisible()
}

async function runId(page: Page): Promise<string> {
  const value = await page.evaluate(() => sessionStorage.getItem('qinghuai.runId'))
  if (!value) throw new Error('the real app did not persist a run id')
  return value
}

async function publicRun(page: Page, id: string): Promise<JsonRecord> {
  const response = await page.request.get(`/api/runs/${id}`)
  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<JsonRecord>
}

function assertNoPrivateFields(value: unknown): void {
  const encoded = JSON.stringify(value)
  for (const field of PRIVATE_FIELD_NAMES) {
    expect(encoded).not.toContain(field)
  }
}

test('runs the real PostgreSQL/FastAPI/LangGraph/React golden path', async ({ page }) => {
  test.setTimeout(120_000)
  await enterRealWorld(page)

  const id = await runId(page)
  let snapshot = await publicRun(page, id)
  assertNoPrivateFields(snapshot)

  const canvas = page.locator('canvas')
  const box = await canvas.boundingBox()
  if (!box) throw new Error('Phaser canvas was not rendered')
  // npc_001's authoritative position is (0, 0); the scene maps that actor to
  // the same stable hit target used by the public browser acceptance suite.
  await canvas.click({
    button: 'right',
    position: { x: (126 / 880) * box.width, y: (286 / 534) * box.height },
  })
  await page.getByRole('button', { name: '发出聊天邀请' }).click()
  // The public invitation response carries the updated Run projection; the
  // conversation is intentionally discovered through the public world list.
  // This keeps the browser path on the existing REST contract without
  // inventing a client-side conversation id.
  await page.getByRole('button', { name: /正在聊天/ }).click()
  await expect(page.getByPlaceholder('自由输入你想说的话……')).toBeVisible()
  // Notices are intentionally interactive/dismissible. Exercise that public
  // behavior before using controls underneath the notice stack.
  await page.getByRole('button', { name: '聊天邀请已接受。' }).click()

  const playerText = '我希望大家先确认书店的底线。'
  await page.getByPlaceholder('自由输入你想说的话……').fill(playerText)
  await page.getByRole('button', { name: '发送' }).click()
  const npcText = '我愿意先把书店的底线和方案说清楚。'
  await expect(page.getByText(playerText, { exact: true })).toBeVisible()
  await expect(page.getByText(npcText, { exact: true })).toBeVisible({ timeout: 30_000 })

  snapshot = await publicRun(page, id)
  const conversation = snapshot.conversations.find((item: JsonRecord) =>
    item.participants.includes('player_001'),
  )
  if (!conversation) throw new Error('the real invitation did not create a player conversation')
  const messagesResponse = await page.request.get(
    `/api/runs/${id}/conversations/${conversation.conversationId}/messages`,
  )
  expect(messagesResponse.ok()).toBeTruthy()
  const messages = (await messagesResponse.json()) as JsonRecord
  expect(messages.messages).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ authorActorId: 'player_001', text: playerText }),
      expect.objectContaining({ authorActorId: 'npc_001', text: npcText }),
    ]),
  )
  assertNoPrivateFields(messages)
  for (const message of messages.messages as JsonRecord[]) {
    if (typeof message.text === 'string') {
      await expect(page.getByText(message.text, { exact: true })).toBeVisible()
    }
  }

  // Verify the browser's durable afterSeq contract without intercepting the
  // WebSocket. Create one real durable event, then connect from the browser
  // with the preceding sequence and require the replay before the snapshot.
  const beforeReplaySeq = snapshot.eventSeq as number
  const advanced = await page.request.post(`/api/runs/${id}/time/advance`, {
    data: { virtualMinutes: 1, commandId: 'fullstack-after-seq' },
  })
  expect(advanced.ok()).toBeTruthy()
  const advancedRun = (await advanced.json()) as JsonRecord
  const replayPackets = (await page.evaluate(
    ({ runId: currentRunId, afterSeq }) =>
      new Promise<JsonRecord[]>((resolve, reject) => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const socket = new WebSocket(
          `${protocol}//${window.location.host}/ws/runs/${currentRunId}?afterSeq=${afterSeq}`,
        )
        const packets: JsonRecord[] = []
        const timeout = window.setTimeout(() => {
          socket.close()
          reject(new Error('timed out waiting for afterSeq replay'))
        }, 10_000)
        socket.onmessage = (event) => {
          const packet = JSON.parse(String(event.data)) as JsonRecord
          packets.push(packet)
          const hasReplay = packets.some((item) => item.eventType === 'time_advanced')
          const hasSnapshot = packets.some((item) => item.worldTime && item.runId === currentRunId)
          if (hasReplay && hasSnapshot) {
            window.clearTimeout(timeout)
            socket.close()
            resolve(packets)
          }
        }
        socket.onerror = () => {
          window.clearTimeout(timeout)
          reject(new Error('real WebSocket replay connection failed'))
        }
      }),
    { runId: id, afterSeq: beforeReplaySeq },
  )) as JsonRecord[]
  expect(replayPackets.some((item) => item.eventType === 'time_advanced')).toBeTruthy()
  expect(
    replayPackets.some(
      (item) => item.worldTime && item.eventSeq >= (advancedRun.run.eventSeq as number),
    ),
  ).toBeTruthy()

  // Cross the real day boundary through the REST command, then reload. The
  // command closes/consolidates the persisted conversation and the UI reads
  // the same authoritative result after a fresh browser process state.
  const current = await publicRun(page, id)
  const currentMinutes = current.worldTime.hour * 60 + current.worldTime.minute
  const untilDayEnd = 18 * 60 - currentMinutes
  expect(untilDayEnd).toBeGreaterThan(0)
  const dayEnd = await page.request.post(`/api/runs/${id}/time/advance`, {
    data: { virtualMinutes: untilDayEnd, commandId: 'fullstack-day-end' },
  })
  expect(dayEnd.ok()).toBeTruthy()
  const ended = (await dayEnd.json()) as JsonRecord
  expect(ended.run.worldTime.time).toBe('18:00')
  expect(ended.run.conversations[0].status).toBe('closed')
  assertNoPrivateFields(ended)
  await expect(page.getByText('今天的聊天已经结束')).toBeVisible()
  await expect(page.getByText('世界将从下一天 08:00 继续。')).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('慎之旧书店二维场景')).toHaveAttribute('data-ready', 'true')
  const restored = await publicRun(page, id)
  // The authoritative runtime may already have resumed Day 2 while the page
  // reloads; either way, persistence must never move backward or reopen chat.
  expect(restored.eventSeq).toBeGreaterThanOrEqual(ended.run.eventSeq)
  expect(restored.worldTime.day).toBeGreaterThanOrEqual(ended.run.worldTime.day)
  expect(restored.conversations[0].status).toBe('closed')
  assertNoPrivateFields(restored)
})
