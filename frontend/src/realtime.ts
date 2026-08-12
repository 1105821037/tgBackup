export type RealtimeConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

export interface RealtimeEvent<T extends Record<string, unknown> = Record<string, unknown>> {
  version: number
  sequence: number
  type: string
  sent_at: string
  payload: T
}

class RealtimeClient {
  private socket: WebSocket | null = null
  private retryTimer: number | null = null
  private heartbeatTimer: number | null = null
  private retryAttempt = 0
  private manuallyStopped = true
  private lastMessageAt = 0
  state: RealtimeConnectionState = 'disconnected'

  connect() {
    this.manuallyStopped = false
    if (this.socket && (
      this.socket.readyState === WebSocket.OPEN
      || this.socket.readyState === WebSocket.CONNECTING
    )) return
    this.clearRetry()
    this.setState(this.retryAttempt ? 'reconnecting' : 'connecting')
    const endpoint = new URL('/api/ws', window.location.href)
    endpoint.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    if (import.meta.env.DEV) endpoint.port = '8000'
    const socket = new WebSocket(endpoint)
    this.socket = socket

    socket.addEventListener('open', () => {
      if (this.socket !== socket) return
      this.retryAttempt = 0
      this.lastMessageAt = Date.now()
      this.setState('connected')
      this.startHeartbeat(socket)
    })
    socket.addEventListener('message', (message) => {
      if (this.socket !== socket) return
      this.lastMessageAt = Date.now()
      try {
        const event = JSON.parse(String(message.data)) as RealtimeEvent
        window.dispatchEvent(new CustomEvent('tg-realtime-event', { detail: event }))
        if (event.type === 'system.ping' && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'client.ping' }))
        }
      } catch {
        // Ignore malformed frames; a later valid heartbeat keeps the channel alive.
      }
    })
    socket.addEventListener('close', (event) => {
      if (this.socket !== socket) return
      this.socket = null
      this.clearHeartbeat()
      if (this.manuallyStopped) {
        this.setState('disconnected')
        return
      }
      if (event.code === 4401) {
        this.setState('disconnected')
        window.dispatchEvent(new Event('tg-auth-expired'))
        return
      }
      this.setState('reconnecting')
      this.scheduleReconnect()
    })
    socket.addEventListener('error', () => {
      if (socket.readyState === WebSocket.OPEN) socket.close()
    })
  }

  stop() {
    this.manuallyStopped = true
    this.retryAttempt = 0
    this.clearRetry()
    this.clearHeartbeat()
    const socket = this.socket
    this.socket = null
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, 'client_stopped')
    }
    this.setState('disconnected')
  }

  private startHeartbeat(socket: WebSocket) {
    this.clearHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      if (this.socket !== socket || socket.readyState !== WebSocket.OPEN) return
      if (Date.now() - this.lastMessageAt > 50_000) {
        socket.close(4000, 'heartbeat_timeout')
        return
      }
      socket.send(JSON.stringify({ type: 'client.ping' }))
    }, 20_000)
  }

  private scheduleReconnect() {
    this.clearRetry()
    const base = Math.min(1000 * 2 ** Math.min(this.retryAttempt, 5), 30_000)
    const delay = base + Math.round(Math.random() * 500)
    this.retryAttempt += 1
    this.retryTimer = window.setTimeout(() => this.connect(), delay)
  }

  private setState(state: RealtimeConnectionState) {
    if (this.state === state) return
    this.state = state
    window.dispatchEvent(new CustomEvent('tg-realtime-state', { detail: { state } }))
  }

  private clearRetry() {
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer)
    this.retryTimer = null
  }

  private clearHeartbeat() {
    if (this.heartbeatTimer !== null) window.clearInterval(this.heartbeatTimer)
    this.heartbeatTimer = null
  }
}

export const realtimeClient = new RealtimeClient()
