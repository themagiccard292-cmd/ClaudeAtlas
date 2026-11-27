import { AlertTriangle, Info, LoaderCircle, Monitor, PanelLeft, X } from 'lucide-react'
import { useEffect } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { ApprovalModal, respond } from './components/ApprovalModal'
import { ChatView } from './components/ChatView'
import { Hud } from './components/Hud'
import { LiveView } from './components/LiveView'
import { Overlay } from './components/Overlay'
import { PromptBar } from './components/PromptBar'
import { SettingsModal } from './components/SettingsModal'
import { SignIn } from './components/SignIn'
import { Sidebar, WorkflowDialog } from './components/Sidebar'
import { ClaudeMark, IconButton, cx } from './components/ui'
import { client } from './lib/backend'
import { liveBus } from './lib/live'
import { voice } from './lib/voice'
import { useStore } from './store'
import type { BackendInfo } from './types'

const route = () => (location.hash.startsWith('#/hud') ? 'hud' : location.hash.startsWith('#/overlay') ? 'overlay' : 'main')
const isMain = () => route() === 'main'

/** Connect to the Python backend (port/token come from Electron; `?port=&token=` allows browser dev/testing). */
function useBootstrap(): void {
  useEffect(() => {
    const st = useStore.getState
    let connectedTo = ''
    const connect = (info: BackendInfo) => {
      st().setBackend(info)
      if (info.status === 'ready' && `${info.port}:${info.token}` !== connectedTo) {
        connectedTo = `${info.port}:${info.token}`
        client.connect(info)
      }
    }
    client.onState = (c) => st().setConn(c)
    client.onFrame = (b) => liveBus.setFrame(b)
    client.onEvent = (e) => {
      if (isMain()) voice.handleBackendEvent(e)
      liveBus.handle(e)
      st().handleEvent(e)
    }
    if (isMain()) voice.init(client)
    const offUpdate = isMain() ? window.api?.onUpdateStatus((u) => st().setUpdate(u)) : undefined

    const q = new URLSearchParams(location.search)
    let off = () => {}
    if (q.get('port') && q.get('token')) {
      connect({ port: Number(q.get('port')), token: q.get('token') as string, status: 'ready' })
    } else if (window.api) {
      void window.api.backendInfo().then((i) => i && connect(i))
      off = window.api.onBackendStatus(connect)
    }
    return () => {
      off()
      offUpdate?.()
      client.disconnect()
    }
  }, [])
}

function useThemeAndHud(): void {
  const theme = useStore((s) => s.settings.theme)
  const handsFree = useStore((s) => s.settings.hands_free)
  const hudEnabled = useStore((s) => s.settings.hud_enabled)
  const agentState = useStore((s) => s.agentState)
  const hotkeys = useStore((s) => s.ready?.hotkeys)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => document.documentElement.classList.toggle('dark', theme === 'dark' || (theme === 'system' && mq.matches))
    apply()
    void window.api?.setTheme(theme)
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [theme])

  useEffect(() => {
    void voice.setHandsFree(handsFree)
  }, [handsFree])

  // Alt+Space fallback only when the backend's low-level key hook is unavailable
  useEffect(() => (hotkeys === false ? window.api?.onPttToggle(() => voice.pttToggle()) : undefined), [hotkeys])

  // Ctrl+Alt+Y / Ctrl+Alt+N answer the oldest pending approval without clicking anything
  useEffect(() => window.api?.onApprovalKey((approve) => {
    const a = useStore.getState().approvals[0]
    if (a) respond(a.id, approve)
  }), [])

  // A downloaded update installs by itself once Claude has been idle for 2 minutes (no manual reinstall/restart needed)
  const updateState = useStore((s) => s.update.state)
  useEffect(() => {
    if (!isMain() || updateState !== 'ready' || agentState !== 'idle') return
    const t = setTimeout(() => void window.api?.installUpdate(), 120_000)
    return () => clearTimeout(t)
  }, [updateState, agentState])

  // While Claude acts: Mini-HUD pill, floating live view and Claude's on-screen cursor (the main window steps aside)
  const liveMode = useStore((s) => s.settings.live_view)
  const agentCursor = useStore((s) => s.settings.agent_cursor || s.settings.screen_glow)
  useEffect(() => {
    if (!window.api || !isMain()) return
    const active = agentState === 'running' || agentState === 'paused'
    void window.api.setRunUi({
      active,
      hud: hudEnabled,
      overlay: agentCursor,
      minimizeMain: liveMode !== 'panel' && hudEnabled,
    })
  }, [agentState, hudEnabled, liveMode, agentCursor])
}

function Splash({ text, error, action }: { text: string; error?: string; action?: () => void }) {
  return (
    <div className="drag flex h-full flex-col items-center justify-center gap-4 bg-bg p-8 text-center">
      <ClaudeMark size={44} className={error ? '' : 'spin'} />
      <div className="font-serif text-2xl">{error ? 'Something went wrong' : text}</div>
      {error && (
        <>
          <p className="selectable no-drag max-w-[460px] text-[13.5px] text-muted">{error}</p>
          {action && (
            <button onClick={action} className="no-drag rounded-xl border border-line bg-card px-4 py-2 text-sm hover:bg-hover">
              Restart engine
            </button>
          )}
        </>
      )}
    </div>
  )
}

function UpdateBanner() {
  const u = useStore((s) => s.update)
  if (u.state !== 'ready' && u.state !== 'downloading') return null
  return (
    <div className="rise fixed left-1/2 top-12 z-[65] flex -translate-x-1/2 items-center gap-3 rounded-full border border-line bg-card px-4 py-2 text-[13px] shadow-lg">
      {u.state === 'downloading' ? (
        <><LoaderCircle size={15} className="spin text-accent" /> Downloading update{u.version ? ` ${u.version}` : ''}… {u.percent ?? 0}%</>
      ) : (
        <>
          <span>Update {u.version} is ready.</span>
          <button onClick={() => void window.api?.installUpdate()} className="rounded-full bg-accent px-3 py-1 text-[12.5px] font-semibold text-accent-fg hover:bg-accent-hover">Restart to update</button>
        </>
      )}
    </div>
  )
}

function Toasts() {
  const toasts = useStore((s) => s.toasts)
  const dismiss = useStore((s) => s.dismissToast)
  return (
    <div className="pointer-events-none fixed bottom-24 left-1/2 z-[70] flex -translate-x-1/2 flex-col items-center gap-2">
      {toasts.map((t) => (
        <div key={t.id} role="status" className={cx('rise pointer-events-auto flex max-w-[560px] items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-[13px] shadow-lg', t.kind === 'error' ? 'border-danger/40 bg-card text-danger' : t.kind === 'warn' ? 'border-warn/40 bg-card text-warn' : 'border-line bg-card')}>
          {t.kind === 'info' ? <Info size={16} className="mt-0.5 shrink-0" /> : <AlertTriangle size={16} className="mt-0.5 shrink-0" />}
          <span className="selectable">{t.text}</span>
          <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="ml-1 text-muted hover:text-ink">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

function Shell() {
  const { sidebarOpen, settingsOpen, workflowDraft } = useStore(useShallow((s) => ({ sidebarOpen: s.ui.sidebarOpen, settingsOpen: s.ui.settingsOpen, workflowDraft: s.ui.workflowDraft })))
  const title = useStore((s) => s.chats.find((c) => c.id === s.activeChatId)?.title ?? '')
  const setUi = useStore((s) => s.setUi)
  const agentState = useStore((s) => s.agentState)
  const ready = useStore((s) => s.ready)
  const livePanel = useStore((s) => s.ui.livePanel)
  const liveMode = useStore((s) => s.settings.live_view)
  const showPanel = liveMode !== 'off' && (livePanel || (liveMode === 'panel' && agentState !== 'idle'))

  return (
    <div className="flex h-full">
      {sidebarOpen && <Sidebar />}
      <main className="relative flex min-w-0 flex-1 flex-col bg-bg">
        <header className="drag flex h-10 shrink-0 items-center gap-1 pl-2 pr-[150px]">
          <IconButton label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'} onClick={() => setUi({ sidebarOpen: !sidebarOpen })}>
            <PanelLeft size={18} />
          </IconButton>
          <span className="min-w-0 flex-1 truncate text-center text-[13px] text-muted">{title}</span>
          {liveMode !== 'off' && (
            <IconButton label={showPanel ? 'Hide live view' : 'Show live view'} onClick={() => setUi({ livePanel: !livePanel })} className={showPanel ? 'text-accent' : ''}>
              <Monitor size={18} />
            </IconButton>
          )}
          {agentState !== 'idle' && (
            <span className="flex items-center gap-1.5 text-[12px] text-accent">
              <LoaderCircle size={13} className="spin" /> {agentState === 'paused' ? 'Paused' : 'Working'}
            </span>
          )}
        </header>
        {ready && ready.os_errors.length > 0 && <div className="mx-6 mb-2 rounded-xl bg-danger/10 px-3 py-2 text-[12.5px] text-danger">{ready.os_errors.join(' · ')}</div>}
        <div className="min-h-0 flex-1">
          <ChatView />
        </div>
        <PromptBar />
      </main>
      {showPanel && (
        <aside className="flex w-[430px] shrink-0 flex-col border-l border-line bg-soft">
          <div className="drag flex h-10 shrink-0 items-center gap-2 px-4 text-[12px] font-medium">
            <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger opacity-60" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-danger" /></span>
            LIVE <span className="font-normal text-muted">Claude’s screen</span>
          </div>
          <div className="px-3"><LiveView /></div>
          <p className="px-4 pt-3 text-[12px] leading-relaxed text-muted">Watch Claude’s cursor work on your primary monitor in real time. (Claude itself perceives the screen in snapshots; this view is live video for you.)</p>
        </aside>
      )}
      {settingsOpen && <SettingsModal />}
      {workflowDraft && <WorkflowDialog />}
      <ApprovalModal />
      <UpdateBanner />
      <Toasts />
    </div>
  )
}

export default function App() {
  useBootstrap()
  useThemeAndHud()
  const { backend, conn, auth } = useStore(useShallow((s) => ({ backend: s.backend, conn: s.conn, auth: s.auth })))

  const r = route()
  if (r === 'hud') return <Hud />
  if (r === 'overlay') return <Overlay />
  if (!backend || backend.status === 'starting' || backend.status === 'restarting') return <Splash text={backend?.status === 'restarting' ? 'Restarting the engine…' : 'Starting the OS-control engine…'} />
  if (backend.status === 'failed') return <Splash text="" error={backend.error} action={() => void window.api?.restartBackend()} />
  if (conn !== 'open' && !auth) return <Splash text="Connecting…" />
  if (!auth) return <Splash text="Checking your Claude sign-in…" />
  if (!auth.subscription_ok) return <SignIn />
  return <Shell />
}
