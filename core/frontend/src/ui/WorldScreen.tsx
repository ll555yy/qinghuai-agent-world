import { lazy, Suspense, useCallback, useState } from 'react'

import { ApiError, api } from '../api/client'
import { PLAYER_ACTOR_ID, type PublicAgenda } from '../api/types'
import { useUiStore } from '../state/uiStore'
import { useWorldStore } from '../state/worldStore'
import { ActorPanel } from './panels/ActorPanel'
import { ChatPanel } from './panels/ChatPanel'
import { EventsPanel } from './panels/EventsPanel'
import { useWorldRuntime } from './useWorldRuntime'
import { NoticeToast } from './NoticeToast'

const WorldCanvas = lazy(() => import('../game/WorldCanvas').then((module) => ({ default: module.WorldCanvas })))
const RelationshipGraphPanel = lazy(() => import('./panels/RelationshipGraphPanel'))

function actorName(actorId: string, actors: { actorId: string; name: string }[]): string {
  if (actorId === PLAYER_ACTOR_ID) return '你'
  return actors.find((actor) => actor.actorId === actorId)?.name ?? actorId
}

export default function WorldScreen() {
  useWorldRuntime()
  const snapshot = useWorldStore((state) => state.snapshot)
  const invitations = useWorldStore((state) => state.invitations)
  const joinRequests = useWorldStore((state) => state.joinRequests)
  const notices = useWorldStore((state) => state.notices)
  const sceneCue = useWorldStore((state) => state.sceneCue)
  const socketStatus = useWorldStore((state) => state.socketStatus)
  const busy = useWorldStore((state) => state.busy)
  const error = useWorldStore((state) => state.error)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)
  const setInvitation = useWorldStore((state) => state.setInvitation)
  const setJoinRequest = useWorldStore((state) => state.setJoinRequest)
  const setConversationMessages = useWorldStore((state) => state.setConversationMessages)
  const setBusy = useWorldStore((state) => state.setBusy)
  const setError = useWorldStore((state) => state.setError)
  const addNotice = useWorldStore((state) => state.addNotice)
  const showSceneCue = useWorldStore((state) => state.showSceneCue)
  const dismissNotice = useWorldStore((state) => state.dismissNotice)
  const panel = useUiStore((state) => state.panel)
  const contextMenu = useUiStore((state) => state.contextMenu)
  const showContextMenu = useUiStore((state) => state.showContextMenu)
  const closeContextMenu = useUiStore((state) => state.closeContextMenu)
  const openActor = useUiStore((state) => state.openActor)
  const openChat = useUiStore((state) => state.openChat)
  const openEvents = useUiStore((state) => state.openEvents)
  const openRelationships = useUiStore((state) => state.openRelationships)
  const [commandLabel, setCommandLabel] = useState<string | null>(null)

  const [agendas] = useState<PublicAgenda[]>(() => {
    const raw = sessionStorage.getItem('qinghuai.agendas')
    if (!raw) return []
    try {
      return JSON.parse(raw) as PublicAgenda[]
    } catch {
      return []
    }
  })

  const onActorContext = useCallback(
    (actorId: string, clientX: number, clientY: number) => showContextMenu({ actorId, x: clientX, y: clientY }),
    [showContextMenu],
  )
  const onConversationClick = useCallback((conversationId: string) => openChat(conversationId), [openChat])

  if (!snapshot) return <div className="screen-message">世界快照尚未准备好。</div>

  const selectedAgenda = agendas.find((agenda) => agenda.agendaId === snapshot.playerAgendaId)
  const currentPlayerConversation = snapshot.conversations.find(
    (conversation) => conversation.status === 'open' && conversation.participants.includes(PLAYER_ACTOR_ID),
  )
  const afterCutoff = snapshot.worldTime.hour >= 17
  const nearDayEnd = snapshot.worldTime.hour === 17 && snapshot.worldTime.minute >= 50
  const menuActor = contextMenu ? snapshot.actors.find((actor) => actor.actorId === contextMenu.actorId) : null
  const menuState = contextMenu ? snapshot.actorStates[contextMenu.actorId] : null
  const canInvite = Boolean(
    menuActor?.kind === 'npc' &&
      menuState?.status !== 'departed' &&
      menuState?.status !== 'chatting' &&
      !currentPlayerConversation &&
      !afterCutoff &&
      !busy,
  )

  const runCommand = async (label: string, command: () => Promise<void>) => {
    setBusy(true)
    setCommandLabel(label)
    setError(null)
    try {
      await command()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : '操作没有完成。')
    } finally {
      setBusy(false)
      setCommandLabel(null)
    }
  }

  const inviteActor = (actorId: string) =>
    runCommand('正在等待对方回应……', async () => {
      closeContextMenu()
      const result = await api.invite(snapshot.runId, actorId)
      setSnapshot(result.run)
      if (result.invitation) setInvitation(result.invitation)
      showSceneCue(PLAYER_ACTOR_ID, '想和你聊聊', 'invite')
      if (result.conversation) {
        openChat(result.conversation.conversationId)
        const history = await api.messages(snapshot.runId, result.conversation.conversationId)
        setConversationMessages(result.conversation.conversationId, history.messages)
      } else if (result.invitation?.status === 'refused') {
        showSceneCue(actorId, '不了，我现在不想聊', 'refuse')
        addNotice(`${actorName(actorId, snapshot.actors)}拒绝了邀请。`, 'warning')
      }
    })

  const respondInvitation = (invitationId: string, accepted: boolean) =>
    runCommand(accepted ? '正在接受邀请……' : '正在拒绝邀请……', async () => {
      const result = await api.respondInvitation(snapshot.runId, invitationId, accepted)
      setSnapshot(result.run)
      if (result.invitation) setInvitation(result.invitation)
      if (!accepted) showSceneCue(PLAYER_ACTOR_ID, '不了，我现在不想聊', 'refuse')
      if (result.conversation) openChat(result.conversation.conversationId)
    })

  const respondJoin = (joinRequestId: string, accepted: boolean) =>
    runCommand('正在回应加入请求……', async () => {
      const result = await api.respondJoinRequest(snapshot.runId, joinRequestId, accepted)
      setSnapshot(result.run)
      if (result.joinRequest) setJoinRequest(result.joinRequest)
      if (result.messages && result.conversation) {
        setConversationMessages(result.conversation.conversationId, result.messages)
      }
    })

  const pendingPlayerInvitations = Object.values(invitations).filter(
    (item) => item.status === 'pending' && item.targetActorId === PLAYER_ACTOR_ID,
  )
  const pendingPlayerJoins = Object.values(joinRequests).filter(
    (item) => item.status === 'pending' && item.pendingPlayerDecision,
  )
  const showSidePanel = panel !== 'none' && panel !== 'relationships'
  const overlayChatPanel = panel === 'chat'

  return (
    <main className="world-screen" onClick={() => contextMenu && closeContextMenu()}>
      <header className={`world-topbar ${afterCutoff ? 'closing' : ''}`}>
        <div className="clock-block">
          <strong>Day {snapshot.worldTime.day} / 7</strong>
          <span>{snapshot.worldTime.time}</span>
        </div>
        <div className="task-block">
          <span>当前任务</span>
          <strong>{selectedAgenda?.title ?? '旁观五人的选择'}</strong>
        </div>
        {afterCutoff ? <div className="cutoff-note">{nearDayEnd ? '临近 18:00，当前聊天即将强制结束' : '17:00 后不能开始新聊天'}</div> : null}
        <div className="topbar-actions">
          <button type="button" className="secondary-button" onClick={(event) => { event.stopPropagation(); openRelationships() }}>
            关系图谱 <span>{snapshot.conversations.length}</span>
          </button>
          <button type="button" className="secondary-button" onClick={(event) => { event.stopPropagation(); openEvents() }}>
            事件记录 <span>{snapshot.worldEvents.length}</span>
          </button>
        </div>
        <div className={`connection-dot ${socketStatus}`}>{socketStatus === 'connected' ? '已连接' : '连接中'}</div>
      </header>

      <section className={`world-layout ${showSidePanel && !overlayChatPanel ? 'with-panel' : ''}`}>
        <div className="scene-column">
          <Suspense fallback={<div className="world-canvas scene-loading">正在布置慎之旧书店……</div>}>
            <WorldCanvas
              snapshot={snapshot}
              sceneCue={sceneCue}
              onActorContext={onActorContext}
              onConversationClick={onConversationClick}
            />
          </Suspense>
          <div className="conversation-access-list" aria-label="当前聊天">
            {snapshot.conversations.filter((item) => item.status === 'open').map((conversation) => (
              <button key={conversation.conversationId} type="button" onClick={() => openChat(conversation.conversationId)}>
                {conversation.participants.map((actorId) => actorName(actorId, snapshot.actors)).join('、')}正在聊天
              </button>
            ))}
          </div>
          <div className="scene-help">右键 NPC 了解信息或邀请聊天 · 点击聊天圈查看或申请加入</div>
        </div>
        {showSidePanel ? (
          <aside className={`side-panel ${overlayChatPanel ? 'chat-panel-overlay' : ''}`} onClick={(event) => event.stopPropagation()}>
            {panel === 'actor' ? <ActorPanel /> : null}
            {panel === 'chat' ? <ChatPanel /> : null}
            {panel === 'events' ? <EventsPanel /> : null}
          </aside>
        ) : null}
      </section>

      {panel === 'relationships' ? (
        <section className="relationship-map-overlay" onClick={(event) => event.stopPropagation()}>
          <Suspense fallback={<div className="panel-loading">正在展开人物关系地图……</div>}>
            <RelationshipGraphPanel />
          </Suspense>
        </section>
      ) : null}

      {contextMenu && menuActor ? (
        <div
          className="context-menu"
          style={{ left: Math.min(contextMenu.x, window.innerWidth - 220), top: Math.min(contextMenu.y, window.innerHeight - 150) }}
          onClick={(event) => event.stopPropagation()}
        >
          <strong>{menuActor.name}</strong>
          <button type="button" onClick={() => { closeContextMenu(); openActor(menuActor.actorId) }}>了解信息</button>
          <button type="button" disabled={!canInvite} onClick={() => inviteActor(menuActor.actorId)}>
            发出聊天邀请
          </button>
          {!canInvite ? <small>{afterCutoff ? '今天已过邀请时间' : '当前无法邀请'}</small> : null}
        </div>
      ) : null}

      <section className="request-stack" aria-live="polite">
        {pendingPlayerInvitations.map((invitation) => (
          <article className="request-card" key={invitation.invitationId}>
            <strong>{actorName(invitation.initiatorActorId, snapshot.actors)}邀请你聊天</strong>
            <div>
              <button type="button" onClick={() => respondInvitation(invitation.invitationId, true)}>接受</button>
              <button type="button" className="danger-button" onClick={() => respondInvitation(invitation.invitationId, false)}>拒绝</button>
            </div>
          </article>
        ))}
        {pendingPlayerJoins.map((request) => (
          <article className="request-card" key={request.joinRequestId}>
            <strong>{actorName(request.applicantActorId, snapshot.actors)}想加入聊天</strong>
            <div>
              <button type="button" onClick={() => respondJoin(request.joinRequestId, true)}>接受</button>
              <button type="button" className="danger-button" onClick={() => respondJoin(request.joinRequestId, false)}>拒绝</button>
            </div>
          </article>
        ))}
      </section>

      {snapshot.worldTime.hour >= 18 && !snapshot.chapterEnded ? (
        <div className="day-end-overlay" role="status">
          <div>
            <p className="eyebrow">Day {snapshot.worldTime.day} 结束</p>
            <strong>今天的聊天已经结束</strong>
            <span>世界将从下一天 08:00 继续。</span>
          </div>
        </div>
      ) : null}

      {commandLabel ? <div className="command-status">{commandLabel}</div> : null}
      {error ? <div className="error-toast" role="alert">{error}<button type="button" onClick={() => setError(null)}>×</button></div> : null}
      <section className="notice-stack" aria-live="polite">
        {notices.map((notice) => (
          <NoticeToast key={notice.id} notice={notice} onDismiss={dismissNotice} />
        ))}
      </section>
    </main>
  )
}
