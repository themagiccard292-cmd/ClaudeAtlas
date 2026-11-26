import { useEffect, useRef, useState } from 'react'
import { cx } from './ui'

/** Animated audio bars. `level` is 0..1 (mic RMS while listening, output RMS while speaking). */
export function Visualizer({ level, active, bars = 24, height = 28, className, color = 'var(--c-accent)' }: { level: number; active: boolean; bars?: number; height?: number; className?: string; color?: string }) {
  const [t, setT] = useState(0)
  const lv = useRef(level)
  lv.current = level
  useEffect(() => {
    if (!active) return
    let raf = 0
    const loop = (ts: number) => {
      setT(ts / 1000)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [active])

  return (
    <div className={cx('flex items-center gap-[3px]', className)} style={{ height }} aria-hidden>
      {Array.from({ length: bars }, (_, i) => {
        const wave = 0.5 + 0.5 * Math.sin(t * 7 + i * 0.75) * Math.cos(t * 3.1 + i * 0.31)
        const amp = active ? 0.14 + Math.min(1, lv.current * 1.4) * (0.35 + 0.65 * wave) : 0.1
        return <span key={i} className="w-[3px] rounded-full transition-[height] duration-75" style={{ height: `${Math.max(10, amp * 100)}%`, background: color, opacity: active ? 0.55 + amp * 0.45 : 0.3 }} />
      })}
    </div>
  )
}
