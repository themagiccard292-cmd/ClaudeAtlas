import { useEffect, useState } from 'react'
import { CursorLayer } from './LiveView'

/** Full-screen, click-through, transparent window: Claude's cursor + ripples drawn on the REAL desktop. */
export function Overlay() {
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight })
  useEffect(() => {
    document.documentElement.classList.add('hud')
    const on = () => setSize({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return <CursorLayer w={size.w} h={size.h} cursorSize={26} />
}
