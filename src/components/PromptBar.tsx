import { ArrowUp, AudioLines, Mic, ShieldCheck, ShieldOff, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { client } from '../lib/backend'
import { voice } from '../lib/voice'
import { useStore } from '../store'
import { ModelMenu } from './ModelMenu'
import { Visualizer } from './Visualizer'
import { cx } from './ui'

export function PromptBar() {
  const agentState = useStore((s) => s.agentState)
  const activeChat = useStore((s) => s.activeChatId)
  const v = useStore((s) => s.voice)
  const settings = useStore((s) => s.settings)
  const toast = useStore((s) => s.toast)
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)
  const busy = agentState !== 'idle'
  const listening = v.mode === 'listening' || v.mode === 'speaking' || v.mode === 'transcribing'

  useEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  const send = () => {
    const t = text.trim()
    if (!t) return
    if (!client.send({ type: 'chat.send', chat_id: activeChat, text: t })) toast('Not connected to the engine yet.', 'error')
    setText('')
  }

  const setHandsFree = (on: boolean) => {
    client.send({ type: 'settings.set', patch: { hands_free: on } })
    void voice.setHandsFree(on)
  }

  const approvalsOn = settings.approval_mode !== 'never'
  const toggleApprovals = () => {
    if (approvalsOn && !window.confirm('Turn OFF approvals?\n\nClaude will delete files, send messages, make purchases and change settings WITHOUT asking you first.')) return
    client.send({ type: 'settings.set', patch: { approval_mode: approvalsOn ? 'never' : 'smart' } })
  }

  const label = v.mode === 'transcribing' ? 'Transcribing…' : v.mode === 'speaking' ? 'Claude is speaking — talk to interrupt' : v.mode === 'listening' ? (settings.hands_free ? (settings.wake_word_enabled ? `Listening — say “${settings.wake_word}”` : 'Listening…') : 'Listening — release to send') : ''

  return (
    <div className="mx-auto w-full max-w-[760px] px-6 pb-5">
      {busy && (
        <div className="mb-2 flex items-center justify-between px-1 text-[12px] text-muted">
          <span>{agentState === 'paused' ? 'Paused — waiting for your instruction' : 'Claude is working — send a message to redirect it, or press Stop.'}</span>
          <span className="font-mono">Esc Esc · Ctrl+Shift+Esc = emergency stop</span>
        </div>
      )}
      <div className={cx('rounded-[22px] border bg-card p-2.5 shadow-sm transition-shadow focus-within:shadow-md', listening ? 'border-accent' : 'border-line')}>
        <div className="flex items-end gap-2">
          <div className="relative min-w-0 flex-1">
            {listening && (
              <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex h-[44px] items-center gap-3 rounded-xl bg-card px-3">
                <Visualizer level={v.level} active={v.mode !== 'transcribing'} bars={36} height={30} className="flex-1" />
                <span className="shrink-0 text-[12.5px] text-muted">{label}</span>
              </div>
            )}
            <textarea
              ref={area}
              rows={1}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder={busy ? 'Redirect Claude…' : 'Tell Claude what to do on your PC'}
              className="selectable block max-h-[200px] min-h-[44px] w-full resize-none bg-transparent px-3 py-[10px] text-[15px] outline-none placeholder:text-muted"
              aria-label="Message"
            />
          </div>
          <button
            aria-label="Hold to talk"
            title="Hold to talk (or hold Alt+Space)"
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId)
              void voice.pttDown()
            }}
            onPointerUp={() => voice.pttUp()}
            onPointerCancel={() => voice.pttUp()}
            className={cx('no-drag flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors', v.mode === 'listening' && !settings.hands_free ? 'bg-accent text-accent-fg' : 'text-muted hover:bg-hover hover:text-ink')}
          >
            <Mic size={19} />
          </button>
          {busy && !text.trim() ? (
            <button aria-label="Stop" title="Stop" onClick={() => client.send({ type: 'agent.stop' })} className="no-drag flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-danger text-white hover:brightness-110">
              <Square size={15} fill="currentColor" />
            </button>
          ) : (
            <button aria-label="Send" title="Send (Enter)" disabled={!text.trim()} onClick={send} className="no-drag flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-accent-fg transition-opacity hover:bg-accent-hover disabled:opacity-40">
              <ArrowUp size={20} />
            </button>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-2 px-1.5 text-[12px] text-muted">
          <button onClick={() => setHandsFree(!settings.hands_free)} aria-pressed={settings.hands_free} className={cx('no-drag flex items-center gap-1.5 rounded-full px-2.5 py-1 transition-colors hover:bg-hover', settings.hands_free && 'bg-accent/15 text-accent')}>
            <AudioLines size={14} /> Hands-free {settings.hands_free ? 'on' : 'off'}
          </button>
          <button onClick={toggleApprovals} aria-pressed={approvalsOn} title={approvalsOn ? 'Claude asks before risky actions (click to turn off)' : 'Claude will NOT ask before risky actions (click to turn on)'} className={cx('no-drag flex items-center gap-1.5 rounded-full px-2.5 py-1 transition-colors hover:bg-hover', approvalsOn ? '' : 'bg-warn/15 text-warn')}>
            {approvalsOn ? <ShieldCheck size={14} /> : <ShieldOff size={14} />} Approvals {approvalsOn ? 'on' : 'off'}
          </button>
          <span className="hidden md:inline">Hold <kbd className="rounded border border-line px-1 font-mono text-[11px]">Alt</kbd>+<kbd className="rounded border border-line px-1 font-mono text-[11px]">Space</kbd> to talk</span>
          <span className="ml-auto" />
          <ModelMenu />
        </div>
      </div>
    </div>
  )
}
