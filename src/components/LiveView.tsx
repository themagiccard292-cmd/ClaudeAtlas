import { useEffect, useRef, useState } from 'react'
import { client } from '../lib/backend'
import { liveBus, type Ripple } from '../lib/live'
import { useStore } from '../store'
import { cx } from './ui'

/** Claude's cursor: terracotta arrow with a name tag. `size` is the arrow height in px. */
export function AgentCursor({ size = 22, label = 'Claude', badge = '', pressed = false }: { size?: number; label?: string; badge?: string; pressed?: boolean }) {
  return (
    <div style={{ transform: pressed ? 'scale(.88)' : 'none', transition: 'transform .08s' }}>
      <svg width={size} height={size * 1.25} viewBox="0 0 20 25" style={{ display: 'block', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.45))' }}>
        <path d="M2 1.5 L2 20 L7 15.3 L10.4 23 L13.6 21.6 L10.3 14.2 L17.5 14 Z" fill="#D97757" stroke="#fff" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
      <div className="absolute left-[14px] top-[18px] flex items-center gap-1 whitespace-nowrap rounded-full bg-[#D97757] px-2 py-[2px] text-[11px] font-semibold leading-4 text-white shadow-md">
        {label}
        {badge && <span className="font-medium opacity-90">· {badge}</span>}
      </div>
    </div>
  )
}

function RippleDot({ r, w, h, onDone }: { r: Ripple; w: number; h: number; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 900)
    return () => clearTimeout(t)
  }, [onDone])
  const color = r.kind === 'right' ? '#6aa9ff' : '#D97757'
  return (
    <span className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2" style={{ left: r.fx * w, top: r.fy * h }}>
      {(r.kind === 'double' ? [0, 140] : [0]).map((d) => (
        <span key={d} className="absolute left-0 top-0 block h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px]" style={{ borderColor: color, background: `${color}33`, animation: `ripple .8s ${d}ms ease-out both` }} />
      ))}
    </span>
  )
}

/** Smooth cursor layer: draws Claude's cursor (interpolated each animation frame) + click ripples over a box of size w x h. */
export function CursorLayer({ w, h, cursorSize = 22, className }: { w: number; h: number; cursorSize?: number; className?: string }) {
  const el = useRef<HTMLDivElement>(null)
  const [badge, setBadge] = useState('')
  const [pressed, setPressed] = useState(false)
  const [ripples, setRipples] = useState<Ripple[]>([])
  const pos = useRef({ x: 0.5, y: 0.5 })

  useEffect(() => liveBus.onRipple((r) => {
    setRipples((l) => [...l.slice(-5), r])
    setPressed(true)
    setTimeout(() => setPressed(false), 140)
  }), [])

  useEffect(() => {
    let raf = 0
    let lastBadge = ''
    const loop = () => {
      const t = liveBus.target
      const p = pos.current
      p.x += (t.fx - p.x) * 0.42
      p.y += (t.fy - p.y) * 0.42
      if (el.current) {
        el.current.style.transform = `translate(${p.x * w}px, ${p.y * h}px)`
        el.current.style.opacity = liveBus.seen ? '1' : '0'
      }
      const b = liveBus.dragging || performance.now() < liveBus.badgeUntil ? liveBus.badge : ''
      if (b !== lastBadge) {
        lastBadge = b
        setBadge(b)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [w, h])

  return (
    <div className={cx('pointer-events-none absolute inset-0 overflow-hidden', className)}>
      {ripples.map((r) => (
        <RippleDot key={r.id} r={r} w={w} h={h} onDone={() => setRipples((l) => l.filter((x) => x.id !== r.id))} />
      ))}
      <div ref={el} className="absolute left-0 top-0 will-change-transform" style={{ opacity: 0 }}>
        <AgentCursor size={cursorSize} badge={badge} pressed={pressed} />
      </div>
    </div>
  )
}

/** Live video of the primary monitor + Claude's cursor. Subscribes to the backend stream while mounted. */
export function LiveView({ className }: { className?: string }) {
  const conn = useStore((s) => s.conn)
  const ready = useStore((s) => s.ready)
  const img = useRef<HTMLImageElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 480, h: 270 })
  const [hasFrame, setHasFrame] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (conn !== 'open') return
    client.send({ type: 'live.subscribe' })
    return () => void client.send({ type: 'live.unsubscribe' })
  }, [conn])

  useEffect(() => liveBus.onFrame((url) => {
    if (img.current) img.current.src = url
    setHasFrame(true)
    setError('')
  }), [])
  useEffect(() => {
    const onErr = (e: Event) => setError((e as CustomEvent<string>).detail)
    window.addEventListener('live-error', onErr)
    return () => window.removeEventListener('live-error', onErr)
  }, [])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const sw = ready?.screen.width ?? liveBus.screen?.width ?? 16
  const sh = ready?.screen.height ?? liveBus.screen?.height ?? 9
  return (
    <div ref={box} className={cx('relative w-full overflow-hidden rounded-xl bg-black', className)} style={{ aspectRatio: `${sw} / ${sh}` }}>
      <img ref={img} alt="Live view of the screen Claude is controlling" className={cx('block h-full w-full select-none object-fill', !hasFrame && 'hidden')} draggable={false} />
      {!hasFrame && <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-[12.5px] text-white/60">{error || 'Connecting to the live view…'}</div>}
      <CursorLayer w={size.w} h={size.h} cursorSize={Math.max(16, Math.round(size.w / 34))} />
    </div>
  )
}
