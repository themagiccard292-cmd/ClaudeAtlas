import type { BackendInfo } from '../types'

export type ConnState = 'connecting' | 'open' | 'closed'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type BackendEvent = { type: string } & Record<string, any>

/** Auto-reconnecting WebSocket client for the local Python backend (token-authenticated). */
export class BackendClient {
  private ws: WebSocket | null = null
  private info: Pick<BackendInfo, 'port' | 'token'> | null = null
  private retry = 0
  private timer: number | undefined
  private stopped = false
  onEvent: (e: BackendEvent) => void = () => {}
  onState: (s: ConnState) => void = () => {}

  connect(info: Pick<BackendInfo, 'port' | 'token'>): void {
    this.stopped = false
    this.info = info
    this.open()
  }

  private open(): void {
    if (!this.info || this.stopped) return
    window.clearTimeout(this.timer)
    this.ws?.close()
    this.onState('connecting')
    const ws = new WebSocket(`ws://127.0.0.1:${this.info.port}/ws?token=${encodeURIComponent(this.info.token)}`)
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.onState('open')
      this.send({ type: 'hello' })
    }
    ws.onmessage = (m) => {
      try {
        this.onEvent(JSON.parse(m.data as string) as BackendEvent)
      } catch {
        /* ignore malformed frame */
      }
    }
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.onState('closed')
      if (this.stopped) return
      this.retry += 1
      this.timer = window.setTimeout(() => this.open(), Math.min(250 * this.retry, 2000))
    }
    ws.onerror = () => ws.close()
  }

  send(msg: { type: string } & Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(msg))
    return true
  }

  disconnect(): void {
    this.stopped = true
    window.clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
  }

  /** URL of a stored screenshot thumbnail (served by the backend, token-protected). */
  shotUrl(path: string): string {
    return this.info ? `http://127.0.0.1:${this.info.port}/shots/${path}?token=${encodeURIComponent(this.info.token)}` : ''
  }
}

export const client = new BackendClient()
