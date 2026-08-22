import { isRunEvent, isRunSnapshot, type RunEvent, type RunSnapshot } from './types'

export type SocketStatus = 'connecting' | 'connected' | 'disconnected' | 'failed'

interface RunSocketOptions {
  runId: string
  afterSeq: () => number
  onSnapshot: (snapshot: RunSnapshot) => void
  onEvent: (event: RunEvent) => void
  onStatus: (status: SocketStatus) => void
  onError?: (message: string) => void
}

const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? '/ws'

function socketUrl(runId: string, afterSeq: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const configured = WS_BASE.startsWith('ws://') || WS_BASE.startsWith('wss://')
    ? WS_BASE
    : `${protocol}//${window.location.host}${WS_BASE}`
  return `${configured}/runs/${encodeURIComponent(runId)}?afterSeq=${afterSeq}`
}

export class RunSocket {
  private socket: WebSocket | null = null
  private retryIndex = 0
  private retryTimer: number | null = null
  private stopped = false
  private readonly retryDelays = [1_000, 2_000, 5_000]

  constructor(private readonly options: RunSocketOptions) {}

  connect(): void {
    this.stopped = false
    this.clearRetry()
    this.options.onStatus('connecting')
    const socket = new WebSocket(socketUrl(this.options.runId, this.options.afterSeq()))
    this.socket = socket

    socket.onopen = () => {
      this.retryIndex = 0
      this.options.onStatus('connected')
    }
    socket.onmessage = (message) => {
      try {
        const data: unknown = JSON.parse(String(message.data))
        if (isRunEvent(data)) {
          this.options.onEvent(data)
        } else if (isRunSnapshot(data)) {
          this.options.onSnapshot(data)
        }
      } catch {
        this.options.onError?.('收到无法识别的世界消息。')
      }
    }
    socket.onerror = () => this.options.onError?.('世界实时连接出现异常。')
    socket.onclose = () => {
      this.socket = null
      if (this.stopped) {
        this.options.onStatus('disconnected')
        return
      }
      if (this.retryIndex >= this.retryDelays.length) {
        this.options.onStatus('failed')
        return
      }
      this.options.onStatus('disconnected')
      const delay = this.retryDelays[this.retryIndex++]
      this.retryTimer = window.setTimeout(() => this.connect(), delay)
    }
  }

  reconnect(): void {
    this.retryIndex = 0
    this.socket?.close()
    this.connect()
  }

  stop(): void {
    this.stopped = true
    this.clearRetry()
    this.socket?.close()
    this.socket = null
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
  }
}
