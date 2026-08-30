import { useUiStore } from '../../state/uiStore'
import { useWorldStore } from '../../state/worldStore'

export function EventsPanel() {
  const events = useWorldStore((state) => state.snapshot?.worldEvents ?? [])
  const closePanel = useUiStore((state) => state.closePanel)
  return (
    <div className="panel-content events-panel">
      <header><span>你知道的世界事件</span><button type="button" className="icon-button" onClick={closePanel}>×</button></header>
      {events.length ? [...events].reverse().map((event) => (
        <article key={event.eventId}>
          <time>Day {event.worldDay} · {event.at}</time>
          <strong>{event.sourceLabel}</strong>
          <p>{event.summary}</p>
        </article>
      )) : <p className="empty-state">还没有新的公开事件。</p>}
      <small className="privacy-note">这里只记录玩家公开得知或亲眼看到的事件。</small>
    </div>
  )
}
