import { create } from 'zustand'
import type { BackendEvent, ConnState } from './lib/backend'
import type { UpdateStatus, AgentState, Approval, AuthStatus, BackendInfo, Chat, ChatMeta, Item, ReadyInfo, Settings, Toast, VoiceMode, Workflow } from './types'

export const DEFAULT_SETTINGS: Settings = {
  theme: 'system',
  claude_path: '',
  model: '',
  effort: '',
  approval_mode: 'smart',
  screenshot_max_edge: 1280,
  jpeg_quality: 72,
  auto_screenshot: true,
  settle_ms: 350,
  max_actions: 150,
  human_mouse: true,
  draw_cursor: true,
  hud_enabled: true,
  live_view: 'off',
  live_fps: 10,
  live_width: 960,
  live_quality: 55,
  screen_glow: true,
  agent_cursor: true,
  speak_responses: false,
  stt_model: 'base.en',
  stt_language: 'en',
  tts_voice: 'en-US-AriaNeural',
  tts_rate: '+0%',
  hands_free: false,
  wake_word_enabled: false,
  wake_word: 'hey claude',
}

interface LoginFlow {
  active: boolean
  url: string
  error: string
}

interface State {
  conn: ConnState
  backend: BackendInfo | null
  ready: ReadyInfo | null
  settings: Settings
  auth: AuthStatus | null
  login: LoginFlow
  install: { active: boolean; lines: string[]; error: string }
  update: UpdateStatus
  chats: ChatMeta[]
  workflows: Workflow[]
  activeChatId: string | null
  items: Item[]
  agentState: AgentState
  runningChatId: string | null
  approvals: Approval[]
  voice: { mode: VoiceMode; level: number; micError: string; lastTranscript: string }
  hud: { status: string; steps: number }
  toasts: Toast[]
  ui: { sidebarOpen: boolean; settingsOpen: boolean; settingsTab: string; workflowDraft: { name: string; prompt: string; id?: string } | null; livePanel: boolean }

  setUpdate(u: UpdateStatus): void
  setConn(c: ConnState): void
  setBackend(b: BackendInfo | null): void
  setVoice(p: Partial<State['voice']>): void
  newChat(): void
  setUi(p: Partial<State['ui']>): void
  toast(text: string, kind?: Toast['kind']): void
  dismissToast(id: number): void
  handleEvent(e: BackendEvent): void
}

let toastSeq = 1

export const useStore = create<State>((set, get) => ({
  conn: 'connecting',
  backend: null,
  ready: null,
  settings: DEFAULT_SETTINGS,
  auth: null,
  login: { active: false, url: '', error: '' },
  install: { active: false, lines: [], error: '' },
  update: { state: 'idle' },
  chats: [],
  workflows: [],
  activeChatId: null,
  items: [],
  agentState: 'idle',
  runningChatId: null,
  approvals: [],
  voice: { mode: 'idle', level: 0, micError: '', lastTranscript: '' },
  hud: { status: '', steps: 0 },
  toasts: [],
  ui: { sidebarOpen: true, settingsOpen: false, settingsTab: 'general', workflowDraft: null, livePanel: false },

  setUpdate: (update) => set({ update }),
  setConn: (conn) => set({ conn }),
  setBackend: (backend) => set({ backend }),
  setVoice: (p) => set((s) => ({ voice: { ...s.voice, ...p } })),
  newChat: () => set({ activeChatId: null, items: [] }),
  setUi: (p) => set((s) => ({ ui: { ...s.ui, ...p } })),
  toast: (text, kind = 'info') => {
    const id = toastSeq++
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, text, kind }] }))
    setTimeout(() => get().dismissToast(id), kind === 'error' ? 8000 : 4500)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  handleEvent: (e) => {
    const s = get()
    switch (e.type) {
      case 'ready':
        set({ ready: e as unknown as ReadyInfo })
        break
      case 'settings':
        set({ settings: { ...DEFAULT_SETTINGS, ...(e.settings as Settings) } })
        break
      case 'auth.status': {
        const { type: _t, ...auth } = e
        set({ auth: auth as unknown as AuthStatus, login: auth.subscription_ok ? { active: false, url: '', error: '' } : s.login })
        break
      }
      case 'auth.login.started':
        set({ login: { active: !!e.started, url: '', error: e.started ? '' : s.login.error } })
        break
      case 'auth.login.url':
        set({ login: { ...s.login, url: e.url as string } })
        break
      case 'auth.login.done':
        set({ login: { active: false, url: '', error: e.ok ? '' : (e.detail as string) || 'Sign-in did not complete.' } })
        break
      case 'auth.install.started':
        set({ install: { active: !!e.started, lines: [], error: '' } })
        break
      case 'auth.install.line':
        set({ install: { ...s.install, lines: [...s.install.lines.slice(-5), e.line as string] } })
        break
      case 'auth.install.done':
        set({ install: { active: false, lines: s.install.lines, error: e.ok ? '' : (e.detail as string) || 'Installation did not finish.' } })
        break
      case 'chats':
        set({ chats: e.chats as ChatMeta[] })
        break
      case 'workflows':
        set({ workflows: e.workflows as Workflow[] })
        break
      case 'chat.opened': {
        const chat = e.chat as Chat
        set({ activeChatId: chat.id, items: chat.items })
        break
      }
      case 'state':
        set({ agentState: e.state as AgentState, runningChatId: (e.chat_id as string | null) ?? null, ...(e.state === 'idle' ? { hud: { status: '', steps: 0 } } : {}) })
        break
      case 'item': {
        const item = e.item as Item
        if (item.role === 'action') set({ hud: { status: item.summary ?? '', steps: s.hud.steps + 1 } })
        else if (item.role === 'assistant' && item.text) set({ hud: { ...s.hud, status: item.text.slice(0, 140) } })
        if (e.chat_id === s.activeChatId) set({ items: [...s.items, item] })
        break
      }
      case 'item.update':
        if (e.chat_id === s.activeChatId) {
          set({ items: s.items.map((it) => (it.id === e.id ? { ...it, ...(e.patch as Partial<Item>) } : it)) })
          const patch = e.patch as Partial<Item>
          if (patch.text && s.agentState !== 'idle') set({ hud: { ...s.hud, status: patch.text.slice(0, 140) } })
        }
        break
      case 'approval.request': {
        const { type: _t, ...a } = e
        set({ approvals: [...s.approvals.filter((x) => x.id !== a.id), a as unknown as Approval] })
        break
      }
      case 'approval.resolved':
        set({ approvals: s.approvals.filter((a) => a.id !== e.id) })
        break
      case 'kill':
        s.toast(`Stopped — ${e.reason as string}`, 'warn')
        break
      case 'run.finished':
        if (e.status === 'error') s.toast('The run ended with an error — see the chat for details.', 'error')
        break
      case 'voice.error':
        s.toast(e.message as string, 'error')
        break
      case 'voice.transcript':
        set({ voice: { ...s.voice, lastTranscript: (e.text as string) || '' } })
        if (e.ignored && e.reason) s.toast(`Ignored: “${e.text as string}” (${e.reason as string})`, 'info')
        break
      case 'voice.status':
        set({ voice: { ...s.voice, mode: e.state === 'transcribing' ? 'transcribing' : s.voice.mode === 'transcribing' ? 'idle' : s.voice.mode } })
        break
      case 'live.error':
        window.dispatchEvent(new CustomEvent('live-error', { detail: e.message as string }))
        break
      case 'error':
        s.toast(e.message as string, 'error')
        break
    }
  },
}))
