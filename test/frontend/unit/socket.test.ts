import { RunSocket, type SocketStatus } from '../../../core/frontend/src/api/socket'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close = vi.fn()

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this)
  }
}

function createSocket(onError: (message: string) => void) {
  const statuses: SocketStatus[] = []
  const socket = new RunSocket({
    runId: 'run_1',
    afterSeq: () => 4,
    onSnapshot: vi.fn(),
    onEvent: vi.fn(),
    onStatus: (status) => statuses.push(status),
    onError,
  })
  return { socket, statuses }
}

describe('RunSocket lifecycle errors', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('ignores an error emitted by a socket stopped during effect cleanup', () => {
    const onError = vi.fn()
    const { socket } = createSocket(onError)

    socket.connect()
    const transport = FakeWebSocket.instances[0]
    socket.stop()
    transport.onerror?.()

    expect(transport.close).toHaveBeenCalledOnce()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports an error from the active runtime connection', () => {
    const onError = vi.fn()
    const { socket } = createSocket(onError)

    socket.connect()
    FakeWebSocket.instances[0].onerror?.()

    expect(onError).toHaveBeenCalledOnce()
    expect(onError).toHaveBeenCalledWith('世界实时连接出现异常。')
  })
})
