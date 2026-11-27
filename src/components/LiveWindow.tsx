import { Maximize2, Minimize2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { LiveView } from './LiveView'

/** Contents of the floating "Claude's screen" window (always on top, hidden from screenshots). */
export function LiveWindow() {
  const [size, setSize] = useState<'s' | 'm' | 'l'>('m')
  useEffect(() => document.documentElement.classList.add('hud'), [])
  const change = (s: 's' | 'm' | 'l') => {
    setSize(s)
    void window.api?.setLiveSize(s)
  }
  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-white/15 bg-[#1f1e1d]/95 shadow-[0_10px_40px_rgba(0,0,0,.5)]">
      <div className="flex h-8 shrink-0 items-center gap-2 px-3 text-[11.5px] font-medium text-white/80">
        <div className="drag flex min-w-0 flex-1 cursor-move items-center gap-2 self-stretch">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-70" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
        </span>
        <span className="tracking-wide">LIVE</span>
        <span className="text-white/50">Claude’s screen</span>
        </div>
        <button aria-label="Smaller" disabled={size === 's'} onClick={() => change(size === 'l' ? 'm' : 's')} className="no-drag rounded p-1 hover:bg-white/10 disabled:opacity-30"><Minimize2 size={13} /></button>
        <button aria-label="Larger" disabled={size === 'l'} onClick={() => change(size === 's' ? 'm' : 'l')} className="no-drag rounded p-1 hover:bg-white/10 disabled:opacity-30"><Maximize2 size={13} /></button>
      </div>
      <div className="min-h-0 flex-1 px-2 pb-2">
        <LiveView />
      </div>
    </div>
  )
}
