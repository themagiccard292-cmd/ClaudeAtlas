import { AlertTriangle, Bookmark, Camera, Check, ChevronRight, Globe, Info, Keyboard, LoaderCircle, MousePointerClick, ScanSearch, ShieldCheck, Terminal, Wrench, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { client } from '../lib/backend'
import { useStore } from '../store'
import type { ActionKind, Item } from '../types'
import { Markdown } from './Markdown'
import { ClaudeMark, cx } from './ui'

const KIND_ICON: Record<ActionKind, ReactNode> = {
  mouse: <MousePointerClick size={15} />,
  keyboard: <Keyboard size={15} />,
  terminal: <Terminal size={15} />,
  screen: <Camera size={15} />,
  uia: <ScanSearch size={15} />,
  web: <Globe size={15} />,
  approval: <ShieldCheck size={15} />,
  other: <Wrench size={15} />,
}

function StatusIcon({ status }: { status?: Item['status'] }) {
  if (status === 'running') return <LoaderCircle size={14} className="spin text-accent" />
  if (status === 'error') return <X size={14} className="text-danger" />
  return <Check size={14} className="text-ok" />
}

function Thumb({ item }: { item: Item }) {
  const [big, setBig] = useState(false)
  if (!item.shot) return null
  const [w, h] = item.shot_size ?? [1280, 720]
  const marker = item.coord ? { left: `${(item.coord[0] / w) * 100}%`, top: `${(item.coord[1] / h) * 100}%` } : null
  return (
    <div className={cx('relative mt-2 overflow-hidden rounded-lg border border-line bg-soft', big ? 'max-w-full' : 'max-w-[420px]')} onClick={() => setBig(!big)} style={{ cursor: big ? 'zoom-out' : 'zoom-in' }}>
      <img src={client.shotUrl(item.shot)} alt={`Screenshot after: ${item.summary ?? 'action'}`} className="block w-full" draggable={false} />
      {marker && (
        <span className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2" style={marker}>
          <span className="pulse-ring block h-5 w-5 rounded-full border-2 border-accent bg-accent/25" />
        </span>
      )}
    </div>
  )
}

function ActionRow({ item }: { item: Item }) {
  const [open, setOpen] = useState(false)
  const hasDetail = !!(item.shot || item.detail || item.output || (item.input && Object.keys(item.input).length))
  const inputText = useMemo(() => (item.input && Object.keys(item.input).length ? JSON.stringify(item.input, null, 2) : ''), [item.input])
  return (
    <div className="rounded-lg">
      <button onClick={() => hasDetail && setOpen(!open)} className={cx('flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors', hasDetail && 'hover:bg-hover')} aria-expanded={open}>
        <span className="text-muted">{KIND_ICON[item.kind ?? 'other']}</span>
        <span className="min-w-0 flex-1 truncate text-[13px]">{item.summary}</span>
        {item.coord && <span className="hidden font-mono text-[11px] text-muted sm:inline">{item.coord[0]}, {item.coord[1]}</span>}
        <StatusIcon status={item.status} />
        {hasDetail && <ChevronRight size={14} className={cx('text-muted transition-transform', open && 'rotate-90')} />}
      </button>
      {open && (
        <div className="selectable mb-1 ml-8 mr-2 space-y-2 pb-2 text-[12.5px]">
          <Thumb item={item} />
          {item.coord && (
            <div className="text-muted">
              Clicked at <span className="font-mono text-ink">({item.coord[0]}, {item.coord[1]})</span> in a {item.shot_size?.join('×')} screenshot
            </div>
          )}
          {item.kind === 'terminal' && item.detail ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-[#1d1c1b] p-3 font-mono text-[12px] leading-relaxed text-[#e8e6dc]">{item.detail}</pre>
          ) : (
            <>
              {item.detail && <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-soft p-2.5 font-mono text-[11.5px]">{item.detail}</pre>}
              {item.output && !item.detail && <div className="whitespace-pre-wrap text-muted">{item.output}</div>}
            </>
          )}
          {inputText && item.kind !== 'terminal' && <pre className="overflow-auto rounded-lg bg-soft p-2.5 font-mono text-[11px] text-muted">{inputText}</pre>}
        </div>
      )}
    </div>
  )
}

function ActionGroup({ items, live }: { items: Item[]; live: boolean }) {
  const [open, setOpen] = useState(live)
  const running = items.some((i) => i.status === 'running')
  const failed = items.filter((i) => i.status === 'error').length
  useEffect(() => {
    if (live) setOpen(true)
  }, [live])
  const last = items[items.length - 1]
  return (
    <div className="rise my-2 overflow-hidden rounded-2xl border border-line bg-card/60">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-hover/60" aria-expanded={open}>
        {running ? <LoaderCircle size={16} className="spin text-accent" /> : <ChevronRight size={16} className={cx('text-muted transition-transform', open && 'rotate-90')} />}
        <span className="text-[13px] font-medium">Agent actions</span>
        <span className="text-[12px] text-muted">
          {items.length} step{items.length === 1 ? '' : 's'}
          {failed > 0 && ` · ${failed} failed`}
        </span>
        {!open && last && <span className="ml-1 min-w-0 flex-1 truncate text-[12px] text-muted">— {last.summary}</span>}
      </button>
      {open && (
        <div className="border-t border-line px-1.5 py-1.5">
          {items.map((it) => (
            <ActionRow key={it.id} item={it} />
          ))}
        </div>
      )}
    </div>
  )
}

function UserBubble({ item }: { item: Item }) {
  const setUi = useStore((s) => s.setUi)
  return (
    <div className="group rise flex flex-col items-end">
      <div className="selectable max-w-[82%] whitespace-pre-wrap rounded-2xl bg-hover px-4 py-2.5 text-[14.5px]">{item.text}</div>
      <button onClick={() => setUi({ workflowDraft: { name: (item.text ?? '').slice(0, 40), prompt: item.text ?? '' } })} className="mt-1 flex items-center gap-1 text-[11.5px] text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
        <Bookmark size={12} /> Save as workflow
      </button>
    </div>
  )
}

function AssistantMessage({ item }: { item: Item }) {
  return (
    <div className="rise flex gap-3">
      <ClaudeMark size={20} className="mt-[3px]" />
      <div className="min-w-0 flex-1 text-[14.5px] leading-[1.65]">
        <Markdown text={item.text ?? ''} streaming={item.streaming} />
      </div>
    </div>
  )
}

function SystemNote({ item }: { item: Item }) {
  const error = item.level === 'error'
  return (
    <div className={cx('rise mx-auto flex max-w-[90%] items-start gap-2 rounded-xl px-3 py-2 text-[13px]', error ? 'bg-danger/10 text-danger' : 'bg-hover text-muted')}>
      {error ? <AlertTriangle size={15} className="mt-0.5 shrink-0" /> : <Info size={15} className="mt-0.5 shrink-0" />}
      <span className="selectable whitespace-pre-wrap">{item.text}</span>
    </div>
  )
}

type Block = { kind: 'item'; item: Item } | { kind: 'actions'; items: Item[] }

function groupItems(items: Item[]): Block[] {
  const out: Block[] = []
  for (const it of items) {
    if (it.role === 'action') {
      const last = out[out.length - 1]
      if (last?.kind === 'actions') last.items.push(it)
      else out.push({ kind: 'actions', items: [it] })
    } else out.push({ kind: 'item', item: it })
  }
  return out
}

const SUGGESTIONS = ['Open Notepad and write a packing list for a weekend trip', 'Find the 10 largest files in my Downloads folder', 'Open my browser and look up today’s weather in Seattle', 'Show me what’s using the most memory right now']

function EmptyState() {
  const h = new Date().getHours()
  const greet = h < 5 ? 'Still up' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
  return (
    <div className="mx-auto flex h-full max-w-[640px] flex-col items-center justify-center px-6 pb-24 text-center">
      <ClaudeMark size={44} />
      <h1 className="mt-5 font-serif text-[38px] font-medium leading-tight tracking-tight">{greet}.</h1>
      <p className="mt-2 font-serif text-[22px] text-muted">What should I do on your PC?</p>
      <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => client.send({ type: 'chat.send', chat_id: null, text: s })} className="rounded-xl border border-line bg-card/70 px-4 py-3 text-left text-[13.5px] transition-colors hover:bg-hover">
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

export function ChatView() {
  const items = useStore((s) => s.items)
  const agentState = useStore((s) => s.agentState)
  const runningChat = useStore((s) => s.runningChatId)
  const activeChat = useStore((s) => s.activeChatId)
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const blocks = useMemo(() => groupItems(items), [items])
  const live = agentState !== 'idle' && runningChat === activeChat

  useLayoutEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [items])

  if (items.length === 0) return <EmptyState />
  return (
    <div
      ref={scroller}
      className="h-full overflow-y-auto"
      onScroll={(e) => {
        const el = e.currentTarget
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}
    >
      <div className="mx-auto max-w-[760px] space-y-4 px-6 pb-10 pt-6">
        {blocks.map((b, i) => {
          if (b.kind === 'actions') return <ActionGroup key={b.items[0].id} items={b.items} live={live && i === blocks.length - 1} />
          const it = b.item
          if (it.role === 'user') return <UserBubble key={it.id} item={it} />
          if (it.role === 'assistant') return <AssistantMessage key={it.id} item={it} />
          return <SystemNote key={it.id} item={it} />
        })}
        {live && blocks[blocks.length - 1]?.kind === 'item' && blocks[blocks.length - 1].kind === 'item' && (blocks[blocks.length - 1] as { item: Item }).item.role === 'user' && (
          <div className="flex items-center gap-3 text-muted">
            <ClaudeMark size={20} className="spin" /> <span className="text-[13px]">Working…</span>
          </div>
        )}
      </div>
    </div>
  )
}
