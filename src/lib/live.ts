import type { BackendEvent } from './backend'

export interface Ripple {
  id: number
  fx: number
  fy: number
  kind: 'left' | 'right' | 'double'
}

/** Mutable, non-React state for the live view: frames and cursor events arrive far too fast for a store. */
class LiveBus {
  frameUrl = ''
  screen: { width: number; height: number } | null = null
  target = { fx: 0.5, fy: 0.5 }
  seen = false // has Claude's cursor been seen yet?
  badge = '' // "Typing…", "Scrolling ↓", "ctrl+s"
  badgeUntil = 0
  dragging = false
  private nextId = 1
  private frameSubs = new Set<(url: string) => void>()
  private rippleSubs = new Set<(r: Ripple) => void>()

  onFrame(fn: (url: string) => void): () => void {
    this.frameSubs.add(fn)
    if (this.frameUrl) fn(this.frameUrl)
    return () => this.frameSubs.delete(fn)
  }
  onRipple(fn: (r: Ripple) => void): () => void {
    this.rippleSubs.add(fn)
    return () => this.rippleSubs.delete(fn)
  }

  setFrame(blob: Blob): void {
    const old = this.frameUrl
    this.frameUrl = URL.createObjectURL(blob)
    this.frameSubs.forEach((f) => f(this.frameUrl))
    if (old) setTimeout(() => URL.revokeObjectURL(old), 1500)
  }

  handle(e: BackendEvent): void {
    if (e.type === 'live.info') this.screen = { width: e.width as number, height: e.height as number }
    else if (e.type === 'agent.cursor') this.cursor(e)
  }

  private badgeFor(text: string, ms = 1400): void {
    this.badge = text
    this.badgeUntil = performance.now() + ms
  }

  private cursor(e: BackendEvent): void {
    const kind = e.kind as string
    if (typeof e.fx === 'number' && typeof e.fy === 'number') {
      this.target = { fx: e.fx, fy: e.fy }
      this.seen = true
    }
    if (kind === 'click') {
      const r: Ripple = { id: this.nextId++, fx: this.target.fx, fy: this.target.fy, kind: e.button === 'right' ? 'right' : (e.clicks as number) > 1 ? 'double' : 'left' }
      this.rippleSubs.forEach((f) => f(r))
    } else if (kind === 'type') this.badgeFor(`Typing ${e.n as number} char${e.n === 1 ? '' : 's'}…`, 1800)
    else if (kind === 'key') this.badgeFor(String(e.keys))
    else if (kind === 'scroll') this.badgeFor(`Scrolling ${e.direction === 'up' ? '↑' : e.direction === 'down' ? '↓' : e.direction === 'left' ? '←' : '→'}`)
    else if (kind === 'drag_start') {
      this.dragging = true
      this.badgeFor('Dragging', 4000)
    } else if (kind === 'drag_end') {
      this.dragging = false
      this.badge = ''
    }
  }
}

export const liveBus = new LiveBus()
