import { FormEvent, useEffect, useMemo, useState } from 'react'

import { ApiError, api } from '../../api/client'
import { PLAYER_ACTOR_ID } from '../../api/types'
import { actorPortraitCss } from '../../game/actorAssets'
import { useUiStore } from '../../state/uiStore'
import { useWorldStore } from '../../state/worldStore'

const closeReasonLabels: Record<string, string> = {
  fewer_than_two_participants: '参与者不足两人，聊天结束。',
  idle: '连续两轮无人继续发言，聊天自然结束。',
  day_end: '已经到 18:00，今天的聊天结束。',
  chapter_deadline: '七日方案截止，聊天结束。',
  model_leave: '有人主动离开，聊天结束。',
}

export function ChatPanel() {
  const snapshot = useWorldStore((state) => state.snapshot)
  const conversationId = useUiStore((state) => state.selectedConversationId)
  const closePanel = useUiStore((state) => state.closePanel)
  const messagesByConversation = useWorldStore((state) => state.messages)
  const setConversationMessages = useWorldStore((state) => state.setConversationMessages)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)
  const setJoinRequest = useWorldStore((state) => state.setJoinRequest)
  const addNotice = useWorldStore((state) => state.addNotice)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const conversation = snapshot?.conversations.find((item) => item.conversationId === conversationId)
  const runId = snapshot?.runId
  const messages = conversationId ? messagesByConversation[conversationId] ?? [] : []
  const isParticipant = conversation?.participants.includes(PLAYER_ACTOR_ID) ?? false
  const actorMap = useMemo(
    () => new Map(snapshot?.actors.map((actor) => [actor.actorId, actor]) ?? []),
    [snapshot?.actors],
  )

  useEffect(() => {
    if (!runId || !conversationId || !isParticipant) return
    api.messages(runId, conversationId)
      .then((result) => setConversationMessages(conversationId, result.messages))
      .catch(() => undefined)
  }, [conversationId, isParticipant, runId, setConversationMessages])

  if (!snapshot || !conversation) {
    return <div className="panel-content"><header><span>聊天</span><button type="button" className="icon-button" onClick={closePanel}>×</button></header><p className="empty-state">这场聊天已经不可用。</p></div>
  }

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : '操作没有完成。')
    } finally {
      setBusy(false)
    }
  }

  const join = () => runAction(async () => {
    const result = await api.joinConversation(snapshot.runId, conversation.conversationId)
    setSnapshot(result.run)
    if (result.joinRequest) {
      setJoinRequest(result.joinRequest)
      if (result.joinRequest.status === 'refused') addNotice('聊天成员拒绝了你的加入请求。', 'warning')
      if (result.joinRequest.status === 'accepted') addNotice('你已经加入聊天。', 'success')
    }
    if (result.messages) setConversationMessages(conversation.conversationId, result.messages)
  })

  const leave = () => runAction(async () => {
    const result = await api.leaveConversation(snapshot.runId, conversation.conversationId)
    setSnapshot(result.run)
    addNotice('你离开了聊天。')
  })

  const send = (event: FormEvent) => {
    event.preventDefault()
    const content = text.trim()
    if (!content) return
    runAction(async () => {
      const result = await api.sendMessage(snapshot.runId, conversation.conversationId, content)
      setSnapshot(result.run)
      if (result.messages) setConversationMessages(conversation.conversationId, result.messages)
      setText('')
    })
  }

  return (
    <div className="panel-content chat-panel">
      <header>
        <div><span>聊天</span><small>{conversation.participants.length} / 3 人</small></div>
        <button type="button" className="icon-button" onClick={closePanel}>×</button>
      </header>
      <div className="participant-row">
        {conversation.participants.map((actorId) => (
          <span key={actorId} className="participant-chip">
            <i className="mini-avatar" style={actorPortraitCss(actorId)} aria-hidden="true" />
            {actorId === PLAYER_ACTOR_ID ? '你' : actorMap.get(actorId)?.name ?? actorId}
          </span>
        ))}
      </div>

      {!isParticipant && conversation.status === 'open' ? (
        <section className="join-prompt">
          <p>你还没有加入这场聊天。申请后，当前成员会分别决定是否接受。</p>
          <button type="button" disabled={busy || conversation.participants.length >= 3 || snapshot.worldTime.hour >= 17} onClick={join}>
            {busy ? '正在等待回应……' : '申请加入聊天'}
          </button>
          {snapshot.worldTime.hour >= 17 ? <small>17:00 后不能发起新的加入请求。</small> : null}
        </section>
      ) : null}

      <div className="message-list" aria-live="polite">
        {messages.length ? messages.map((message) => (
          <article key={message.messageId} className={`${message.system ? 'system-message' : ''} ${message.authorActorId === PLAYER_ACTOR_ID ? 'own-message' : ''}`}>
            {!message.system ? <i className="message-avatar" style={actorPortraitCss(message.authorActorId)} aria-hidden="true" /> : null}
            {!message.system ? <strong>{message.authorActorId === PLAYER_ACTOR_ID ? '你' : actorMap.get(message.authorActorId)?.name ?? message.authorActorId}</strong> : null}
            <p>{message.system && message.systemActorId ? `${message.systemActorId === PLAYER_ACTOR_ID ? '你' : actorMap.get(message.systemActorId)?.name ?? message.systemActorId}${message.text}` : message.text}</p>
            {message.createdAt ? <time>{message.createdAt}</time> : null}
          </article>
        )) : <p className="empty-state">{isParticipant ? '聊天刚刚开始。' : '加入后可以查看此前的聊天记录。'}</p>}
      </div>

      {conversation.status === 'closed' ? (
        <div className="conversation-ended">{closeReasonLabels[conversation.closeReason ?? ''] ?? '这场聊天已经结束。'}</div>
      ) : null}
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {isParticipant && conversation.status === 'open' ? (
        <form className="chat-composer" onSubmit={send}>
          <textarea
            value={text}
            maxLength={2000}
            rows={3}
            placeholder="自由输入你想说的话……"
            onChange={(event) => setText(event.target.value)}
          />
          <div>
            <button type="button" className="text-button danger-text" disabled={busy} onClick={leave}>离开聊天</button>
            <span>{text.length}/2000</span>
            <button type="submit" disabled={busy || !text.trim()}>{busy ? '等待 NPC……' : '发送'}</button>
          </div>
        </form>
      ) : null}
    </div>
  )
}
