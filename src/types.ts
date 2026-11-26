export type Role = 'user' | 'assistant' | 'action' | 'system'
export type ActionKind = 'mouse' | 'keyboard' | 'terminal' | 'screen' | 'uia' | 'web' | 'approval' | 'other'

export interface Item {
  id: string
  role: Role
  ts: number
  text?: string
  streaming?: boolean
  level?: 'info' | 'error'
  // action items
  tool?: string
  summary?: string
  kind?: ActionKind
  status?: 'running' | 'done' | 'error'
  input?: Record<string, unknown>
  output?: string
  detail?: string
  shot?: string
  shot_size?: [number, number]
  coord?: [number, number]
}

export interface ChatMeta {
  id: string
  title: string
  updated: number
  created: number
}
export interface Chat {
  id: string
  title: string
  items: Item[]
  session_id?: string | null
}
export interface Workflow {
  id: string
  name: string
  prompt: string
  created: number
  last_run: number | null
}

export interface AuthStatus {
  cli_found: boolean
  path: string
  version: string
  logged_in: boolean
  method: string
  provider: string
  email: string
  plan: string
  subscription_ok: boolean
  message: string
  install_command: string
  install_guide_url: string
}

export interface Settings {
  theme: 'light' | 'dark' | 'system'
  claude_path: string
  model: string
  effort: '' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
  approval_mode: 'smart' | 'all' | 'never'
  screenshot_max_edge: number
  jpeg_quality: number
  auto_screenshot: boolean
  settle_ms: number
  max_actions: number
  human_mouse: boolean
  draw_cursor: boolean
  hud_enabled: boolean
  speak_responses: boolean
  stt_model: string
  stt_language: string
  tts_voice: string
  tts_rate: string
  hands_free: boolean
  wake_word_enabled: boolean
  wake_word: string
}

export type AgentState = 'idle' | 'running' | 'paused' | 'stopping'

export interface Approval {
  id: string
  kind: string
  summary: string
  details: string
  risk: 'none' | 'medium' | 'high'
  ts: number
}

export interface ReadyInfo {
  version: string
  platform: string
  screen: { width?: number; height?: number; scale?: number; shot_width?: number; shot_height?: number; error?: string }
  uia: boolean
  stt: boolean
  hotkeys: boolean
  os_errors: string[]
}

export interface BackendInfo {
  port: number
  token: string
  status: 'starting' | 'ready' | 'restarting' | 'failed' | 'stopped'
  error?: string
}

export type VoiceMode = 'idle' | 'listening' | 'transcribing' | 'speaking'

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'error' | 'warn'
}
