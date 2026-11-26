import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { CursorLayer } from './LiveView'

/** Full-screen, click-through, transparent window: orange glow around the screen + Claude's cursor on the REAL desktop. */
export function Overlay() {
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight })
  const glow = useStore((s) => s.settings.screen_glow)
  const cursor = useStore((s) => s.settings.agent_cursor)
  const paused = useStore((s) => s.agentState === 'paused')
  useEffect(() => {
    document.documentElement.classList.add('hud')
    const on = () => setSize({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return (
    <>
      {glow && <div className="screen-glow" style={{ opacity: paused ? 0.5 : 1 }} aria-hidden />}
      {cursor && <CursorLayer w={size.w} h={size.h} cursorSize={26} />}
    </>
  )
}
