import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'

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
  const appendConversationMessage = useWorldStore((state) => state.appendConversationMessage)
  const confirmAcceptedMessage = useWorldStore((state) => state.confirmAcceptedMessage)
  const setMessageDeliveryStatus = useWorldStore((state) => state.setMessageDeliveryStatus)
  const setSnapshot = useWorldStore((state) => state.setSnapshot)
  const setJoinRequest = useWorldStore((state) => state.setJoinRequest)
  const addNotice = useWorldStore((state) => state.addNotice)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const messageListRef = useRef<HTMLDivElement>(null)
  const pendingSendRef = useRef<Promise<void> | null>(null)
  const leavingConversationIdsRef = useRef(new Set<string>())

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

  useEffect(() => {
    const list = messageListRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages.length])

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

  const leave = () => {
    const activeRunId = snapshot.runId
    const activeConversationId = conversation.conversationId
    const pendingSend = pendingSendRef.current
    leavingConversationIdsRef.current.add(activeConversationId)
    const remainingParticipants = conversation.participants.filter((id) => id !== PLAYER_ACTOR_ID)
    const optimisticConversation = {
      ...conversation,
      participants: remainingParticipants,
      ...(remainingParticipants.length < 2
        ? { status: 'closed' as const, closeReason: 'fewer_than_two_participants' }
        : {}),
    }
    setSnapshot({
      ...snapshot,
      conversations: snapshot.conversations.map((item) =>
        item.conversationId === activeConversationId ? optimisticConversation : item,
      ),
      actorStates: {
        ...snapshot.actorStates,
        [PLAYER_ACTOR_ID]: {
          ...snapshot.actorStates[PLAYER_ACTOR_ID],
          status: 'present',
        },
      },
    })
    closePanel()
    addNotice('你离开了聊天。')
    const request = () => api.leaveConversation(activeRunId, activeConversationId)
    void (pendingSend ? pendingSend.then(request) : request())
      .then((result) => setSnapshot(result.run))
      .catch(async (reason) => {
        addNotice(
          reason instanceof ApiError ? `离开聊天未同步：${reason.message}` : '离开聊天未同步，正在恢复服务器状态。',
          'error',
        )
        try {
          setSnapshot(await api.getRun(activeRunId))
        } catch {
          // WebSocket reconnect and the next world step remain as recovery paths.
        }
      })
      .finally(() => leavingConversationIdsRef.current.delete(activeConversationId))
  }

  const send = (event: FormEvent) => {
    event.preventDefault()
    const content = text.trim()
    if (!content || busy) return
    const activeConversationId = conversation.conversationId
    const optimisticMessageId = `pending_${crypto.randomUUID()}`
    appendConversationMessage(activeConversationId, {
      messageId: optimisticMessageId,
      conversationId: activeConversationId,
      authorActorId: PLAYER_ACTOR_ID,
      text: content,
      createdAt: snapshot.worldTime.label,
      deliveryStatus: 'sending',
    })
    setText('')
    setBusy(true)
    setError(null)
    const pendingSend = api.sendMessage(snapshot.runId, activeConversationId, content)
      .then((result) => {
        if (result.acceptedMessageId) {
          const canonical = result.messages?.find(
            (message) => message.messageId === result.acceptedMessageId,
          )
          confirmAcceptedMessage(
            activeConversationId,
            optimisticMessageId,
            result.acceptedMessageId,
            canonical,
          )
        }
        if (!leavingConversationIdsRef.current.has(activeConversationId)) setSnapshot(result.run)
        if (result.messages) setConversationMessages(activeConversationId, result.messages)
      })
      .catch((reason) => {
        setMessageDeliveryStatus(activeConversationId, optimisticMessageId, 'failed')
        setText((current) => current || content)
        setError(reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : '消息没有发送成功。')
      })
      .finally(() => {
        setBusy(false)
        if (pendingSendRef.current === pendingSend) pendingSendRef.current = null
      })
    pendingSendRef.current = pendingSend
  }

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== 'Enter'
      || event.shiftKey
      || event.nativeEvent.isComposing
      || event.nativeEvent.keyCode === 229
    ) return

    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
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

      <div ref={messageListRef} className="message-list" aria-live="polite">
        {messages.length ? messages.map((message) => (
          <article key={message.messageId} className={`${message.system ? 'system-message' : ''} ${message.authorActorId === PLAYER_ACTOR_ID ? 'own-message' : ''} ${message.deliveryStatus ? `message-${message.deliveryStatus}` : ''}`}>
            {!message.system ? <i className="message-avatar" style={actorPortraitCss(message.authorActorId)} aria-hidden="true" /> : null}
            {!message.system ? <strong>{message.authorActorId === PLAYER_ACTOR_ID ? '你' : actorMap.get(message.authorActorId)?.name ?? message.authorActorId}</strong> : null}
            <p>{message.system && message.systemActorId ? `${message.systemActorId === PLAYER_ACTOR_ID ? '你' : actorMap.get(message.systemActorId)?.name ?? message.systemActorId}${message.text}` : message.text}</p>
            {message.createdAt ? <time>{message.createdAt}</time> : null}
            {message.deliveryStatus ? (
              <span className="message-delivery" role={message.deliveryStatus === 'failed' ? 'alert' : undefined}>
                {message.deliveryStatus === 'sending' ? '发送中…' : '发送失败'}
              </span>
            ) : null}
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
            onKeyDown={handleComposerKeyDown}
            aria-keyshortcuts="Enter"
            title="Enter 发送，Shift+Enter 换行"
          />
          <div>
            <button type="button" className="text-button danger-text" onClick={leave}>离开聊天</button>
            <span>{text.length}/2000</span>
            <button type="submit" disabled={busy || !text.trim()}>{busy ? 'NPC 正在回复…' : '发送'}</button>
          </div>
        </form>
      ) : null}
    </div>
  )
}
