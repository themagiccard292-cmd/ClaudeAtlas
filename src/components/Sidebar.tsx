import { AudioLines, MessageSquarePlus, Play, Plus, Settings as Cog, Trash2, Volume2, Zap } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { client } from '../lib/backend'
import { voice } from '../lib/voice'
import { useStore } from '../store'
import type { ChatMeta } from '../types'
import { Badge, Button, IconButton, Modal, Toggle, cx } from './ui'

function group(chats: ChatMeta[]): Array<[string, ChatMeta[]]> {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000
  const buckets: Record<string, ChatMeta[]> = { Today: [], Yesterday: [], 'Previous 7 days': [], Older: [] }
  for (const c of chats) {
    const age = start - c.updated
    if (age < 0) buckets.Today.push(c)
    else if (age < 86400) buckets.Yesterday.push(c)
    else if (age < 7 * 86400) buckets['Previous 7 days'].push(c)
    else buckets.Older.push(c)
  }
  return Object.entries(buckets).filter(([, v]) => v.length)
}

export function WorkflowDialog() {
  const draft = useStore((s) => s.ui.workflowDraft)
  const setUi = useStore((s) => s.setUi)
  const [name, setName] = useState(draft?.name ?? '')
  const [prompt, setPrompt] = useState(draft?.prompt ?? '')
  if (!draft) return null
  const close = () => setUi({ workflowDraft: null })
  const save = () => {
    if (!prompt.trim()) return
    client.send({ type: 'workflows.save', id: draft.id, name: name.trim() || 'Untitled workflow', prompt: prompt.trim() })
    close()
  }
  return (
    <Modal title={draft.id ? 'Edit workflow' : 'Save automation workflow'} onClose={close} width="max-w-[520px]">
      <div className="space-y-3 p-5">
        <label className="block text-[13px] font-medium">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="e.g. Morning routine" className="selectable mt-1 h-10 w-full rounded-xl border border-line bg-card px-3 text-[14px] outline-none focus:border-accent" />
        </label>
        <label className="block text-[13px] font-medium">
          What should Claude do?
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} className="selectable mt-1 w-full resize-none rounded-xl border border-line bg-card px-3 py-2 text-[14px] outline-none focus:border-accent" />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={close}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={!prompt.trim()}>
            Save workflow
          </Button>
        </div>
      </div>
    </Modal>
  )
}

export function Sidebar() {
  const { chats, workflows, activeChatId, auth, settings, conn, agentState } = useStore(useShallow((s) => ({ chats: s.chats, workflows: s.workflows, activeChatId: s.activeChatId, auth: s.auth, settings: s.settings, conn: s.conn, agentState: s.agentState })))
  const setUi = useStore((s) => s.setUi)
  const grouped = useMemo(() => group(chats), [chats])
  const busy = agentState !== 'idle'
  const initial = (auth?.email || 'C')[0].toUpperCase()
  const patch = (p: Record<string, unknown>) => client.send({ type: 'settings.set', patch: p })

  return (
    <aside className="flex h-full w-[284px] shrink-0 flex-col border-r border-line bg-soft">
      <div className="drag h-10 shrink-0" />
      <div className="px-3 pb-2">
        <button
          onClick={() => {
            useStore.getState().newChat()
          }}
          className="no-drag flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-[14px] font-medium text-accent transition-colors hover:bg-hover"
        >
          <MessageSquarePlus size={18} /> New chat
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        <div className="mb-1 mt-2 flex items-center justify-between px-3">
          <span className="text-[11.5px] font-semibold uppercase tracking-wide text-muted">Workflows</span>
          <IconButton label="New workflow" className="h-6 w-6" onClick={() => setUi({ workflowDraft: { name: '', prompt: '' } })}>
            <Plus size={15} />
          </IconButton>
        </div>
        {workflows.length === 0 && <p className="px-3 py-1 text-[12.5px] text-muted">Save a prompt you reuse, then run it in one click.</p>}
        {workflows.map((w) => (
          <div key={w.id} className="group flex items-center gap-1 rounded-xl px-3 py-1.5 hover:bg-hover">
            <Zap size={14} className="shrink-0 text-accent" />
            <span className="min-w-0 flex-1 truncate text-[13.5px]" title={w.prompt}>{w.name}</span>
            <IconButton label={`Run ${w.name}`} className="h-6 w-6 opacity-0 group-hover:opacity-100" disabled={busy} onClick={() => client.send({ type: 'workflows.run', id: w.id })}>
              <Play size={13} />
            </IconButton>
            <IconButton label={`Delete ${w.name}`} className="h-6 w-6 opacity-0 group-hover:opacity-100" onClick={() => client.send({ type: 'workflows.delete', id: w.id })}>
              <Trash2 size={13} />
            </IconButton>
          </div>
        ))}

        <div className="mb-1 mt-5 px-3 text-[11.5px] font-semibold uppercase tracking-wide text-muted">Recents</div>
        {grouped.length === 0 && <p className="px-3 py-1 text-[12.5px] text-muted">Your conversations will appear here.</p>}
        {grouped.map(([label, list]) => (
          <div key={label} className="mb-2">
            <div className="px-3 py-1 text-[11.5px] text-muted">{label}</div>
            {list.map((c) => (
              <div key={c.id} className={cx('group flex items-center rounded-xl pr-1.5 hover:bg-hover', c.id === activeChatId && 'bg-hover')}>
                <button onClick={() => client.send({ type: 'chat.load', id: c.id })} className="min-w-0 flex-1 truncate px-3 py-1.5 text-left text-[13.5px]" title={c.title}>
                  {c.title}
                </button>
                <IconButton label="Delete chat" className="h-6 w-6 opacity-0 group-hover:opacity-100" onClick={() => client.send({ type: 'chat.delete', id: c.id })}>
                  <Trash2 size={13} />
                </IconButton>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="space-y-1 border-t border-line px-3 py-2.5">
        <div className="px-2 pb-0.5 text-[11.5px] font-semibold uppercase tracking-wide text-muted">Voice</div>
        <label className="flex items-center gap-2.5 px-2 py-1 text-[13px]">
          <AudioLines size={15} className="text-muted" />
          <span className="flex-1">Hands-free listening</span>
          <Toggle label="Hands-free listening" checked={settings.hands_free} onChange={(v) => { patch({ hands_free: v }); void voice.setHandsFree(v) }} />
        </label>
        <label className="flex items-center gap-2.5 px-2 py-1 text-[13px]">
          <Volume2 size={15} className="text-muted" />
          <span className="flex-1">Speak replies</span>
          <Toggle label="Speak replies" checked={settings.speak_responses} onChange={(v) => patch({ speak_responses: v })} />
        </label>
      </div>

      <div className="flex items-center gap-2.5 border-t border-line p-3">
        <button onClick={() => setUi({ settingsOpen: true, settingsTab: 'account' })} className="no-drag flex min-w-0 flex-1 items-center gap-2.5 rounded-xl p-1.5 text-left transition-colors hover:bg-hover" title="Account & usage">
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent font-semibold text-accent-fg">
            {initial}
            <span className={cx('absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-soft', conn === 'open' ? 'bg-ok' : 'bg-warn')} />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-medium">{auth?.email || 'Claude account'}</span>
            <Badge tone="accent" className="mt-0.5">Claude {auth?.plan || 'subscription'}</Badge>
          </span>
        </button>
        <IconButton label="Settings" onClick={() => setUi({ settingsOpen: true, settingsTab: 'general' })}>
          <Cog size={18} />
        </IconButton>
      </div>
    </aside>
  )
}
