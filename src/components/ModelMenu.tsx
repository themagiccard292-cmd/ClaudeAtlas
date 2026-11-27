import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { client } from '../lib/backend'
import { useStore } from '../store'
import { cx } from './ui'

const MODELS: Array<{ id: string; name: string; desc: string }> = [
  { id: '', name: 'Default', desc: 'Whatever Claude Code picks for your plan' },
  { id: 'opus', name: 'Opus', desc: 'Most capable, best for complex tasks' },
  { id: 'sonnet', name: 'Sonnet', desc: 'Fast and smart for everyday tasks' },
  { id: 'haiku', name: 'Haiku', desc: 'Fastest, lightest on your plan limit' },
]
const EFFORTS: Array<{ id: string; name: string }> = [
  { id: '', name: 'Auto' },
  { id: 'low', name: 'Low' },
  { id: 'medium', name: 'Medium' },
  { id: 'high', name: 'High' },
]

/** Claude-style model picker that lives in the prompt bar. Applies to the next message you send. */
export function ModelMenu() {
  const model = useStore((s) => s.settings.model)
  const effort = useStore((s) => s.settings.effort)
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const set = (patch: Record<string, string>) => client.send({ type: 'settings.set', patch })

  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false)
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', down)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('mousedown', down)
      window.removeEventListener('keydown', key)
    }
  }, [open])

  const current = MODELS.find((m) => m.id === model) ?? { id: model, name: model, desc: '' }
  return (
    <div ref={root} className="relative">
      <button onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} className={cx('no-drag flex items-center gap-1 rounded-full px-2.5 py-1 text-[12px] transition-colors hover:bg-hover', open && 'bg-hover')}>
        <span className="font-medium text-ink">{current.name}</span>
        {effort && <span className="text-muted">· {effort}</span>}
        <ChevronDown size={13} className={cx('text-muted transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div role="menu" className="rise absolute bottom-full right-0 z-30 mb-2 w-[290px] overflow-hidden rounded-2xl border border-line bg-card p-1.5 shadow-xl">
          {MODELS.map((m) => (
            <button key={m.id} role="menuitemradio" aria-checked={m.id === model} onClick={() => { set({ model: m.id }); setOpen(false) }} className="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left hover:bg-hover">
              <span className="min-w-0 flex-1">
                <span className="block text-[13.5px] font-medium">{m.name}</span>
                <span className="block text-[12px] text-muted">{m.desc}</span>
              </span>
              {m.id === model && <Check size={16} className="mt-0.5 text-accent" />}
            </button>
          ))}
          <div className="mx-2 my-1 border-t border-line" />
          <div className="px-3 pb-2 pt-1">
            <div className="mb-1.5 text-[12px] text-muted">Thinking effort</div>
            <div className="flex gap-1 rounded-xl bg-soft p-1">
              {EFFORTS.map((e) => (
                <button key={e.id} onClick={() => set({ effort: e.id })} aria-pressed={e.id === effort} className={cx('flex-1 rounded-lg py-1 text-[12px] transition-colors', e.id === effort ? 'bg-card font-medium shadow-sm' : 'text-muted hover:text-ink')}>
                  {e.name}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11.5px] leading-snug text-muted">Applies to your next message. Higher effort is more careful but uses more of your plan.</p>
          </div>
        </div>
      )}
    </div>
  )
}
