import { ExternalLink, LogOut, Mic, Palette, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import type { ReactNode } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { client } from '../lib/backend'
import { voice } from '../lib/voice'
import { useStore } from '../store'
import type { Settings } from '../types'
import { Badge, Button, Modal, Toggle, cx } from './ui'

const TABS = [
  { id: 'general', label: 'General', icon: Palette },
  { id: 'account', label: 'Claude account', icon: UserRound },
  { id: 'safety', label: 'Safety & control', icon: ShieldCheck },
  { id: 'voice', label: 'Voice', icon: Mic },
  { id: 'about', label: 'About', icon: Sparkles },
] as const

const VOICES = ['en-US-AriaNeural', 'en-US-JennyNeural', 'en-US-GuyNeural', 'en-GB-SoniaNeural', 'en-GB-RyanNeural', 'en-AU-NatashaNeural', 'de-DE-KatjaNeural', 'fr-FR-DeniseNeural', 'es-ES-ElviraNeural']

function Row({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 border-b border-line/70 py-3.5 last:border-0">
      <div className="min-w-0">
        <div className="text-[14px] font-medium">{title}</div>
        {hint && <div className="mt-0.5 text-[12.5px] leading-snug text-muted">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

function Select<T extends string | number>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: Array<[T, string]>; label: string }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange((typeof value === 'number' ? Number(e.target.value) : e.target.value) as T)} className="no-drag h-9 min-w-[170px] rounded-xl border border-line bg-card px-3 text-[13.5px] outline-none focus:border-accent">
      {options.map(([v, l]) => (
        <option key={String(v)} value={v}>
          {l}
        </option>
      ))}
    </select>
  )
}

export function SettingsModal() {
  const { ui, settings, auth, ready, setUi } = useStore(useShallow((s) => ({ ui: s.ui, settings: s.settings, auth: s.auth, ready: s.ready, setUi: s.setUi })))
  const set = (patch: Partial<Settings>) => client.send({ type: 'settings.set', patch })
  const open = (url: string) => void window.api?.openExternal(url)
  const tab = ui.settingsTab
  const close = () => setUi({ settingsOpen: false })

  return (
    <Modal title="Settings" onClose={close}>
      <div className="flex min-h-0 flex-1" style={{ height: 540 }}>
        <nav className="w-[200px] shrink-0 space-y-0.5 border-r border-line bg-soft p-3">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setUi({ settingsTab: t.id })} className={cx('no-drag flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[13.5px] transition-colors hover:bg-hover', tab === t.id && 'bg-hover font-medium')}>
              <t.icon size={16} className="text-muted" /> {t.label}
            </button>
          ))}
        </nav>
        <div className="selectable min-w-0 flex-1 overflow-y-auto px-6 py-2">
          {tab === 'general' && (
            <>
              <Row title="Appearance" hint="Warm light or charcoal dark.">
                <Select label="Theme" value={settings.theme} onChange={(v) => set({ theme: v })} options={[['system', 'Match system'], ['light', 'Light'], ['dark', 'Dark']]} />
              </Row>
              <Row title="Model" hint="Passed to Claude Code (--model). Which models you can use depends on your Claude plan.">
                <Select label="Model" value={settings.model} onChange={(v) => set({ model: v })} options={[['', 'Claude Code default'], ['opus', 'Opus'], ['sonnet', 'Sonnet'], ['haiku', 'Haiku']]} />
              </Row>
              <Row title="Thinking effort" hint="Higher = more careful but slower and uses more of your plan’s limit.">
                <Select label="Effort" value={settings.effort} onChange={(v) => set({ effort: v })} options={[['', 'Default'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['xhigh', 'Extra high'], ['max', 'Max']]} />
              </Row>
              <Row title="Live view" hint="Watch Claude control your screen in real time: a panel inside this app (great with two monitors), or off.">
                <Select label="Live view" value={settings.live_view} onChange={(v) => set({ live_view: v })} options={[['panel', 'Panel in this window'], ['off', 'Off']]} />
              </Row>
              <Row title="Orange glow around the screen" hint="A glowing orange edge shows whenever Claude is controlling your PC. Not visible to Claude.">
                <Toggle label="Screen glow" checked={settings.screen_glow} onChange={(v) => set({ screen_glow: v })} />
              </Row>
              <Row title="Show Claude’s cursor on my screen" hint="A labelled cursor and click ripples drawn over your real desktop so you can follow what it does.">
                <Toggle label="Claude cursor overlay" checked={settings.agent_cursor} onChange={(v) => set({ agent_cursor: v })} />
              </Row>
              <Row title="Live view smoothness" hint="Higher = smoother video, more CPU.">
                <Select label="Live fps" value={settings.live_fps} onChange={(v) => set({ live_fps: v })} options={[[5, '5 fps'], [10, '10 fps (default)'], [15, '15 fps'], [20, '20 fps']]} />
              </Row>
              <Row title="Live view quality">
                <Select label="Live width" value={settings.live_width} onChange={(v) => set({ live_width: v })} options={[[640, 'Low (640 px)'], [960, 'Medium (960 px)'], [1280, 'High (1280 px)'], [1920, 'Full (1920 px)']]} />
              </Row>
              <Row title="Floating Mini-HUD while Claude works" hint="Shrinks this window into an always-on-top pill with a STOP / TAKE OVER button.">
                <Toggle label="Mini-HUD" checked={settings.hud_enabled} onChange={(v) => set({ hud_enabled: v })} />
              </Row>
            </>
          )}

          {tab === 'account' && (
            <>
              <div className="my-4 rounded-2xl border border-line bg-card p-4">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[15px] font-medium">{auth?.email || 'Signed in to Claude'}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                      {auth?.subscription_ok ? <Badge tone="ok">Signed in</Badge> : <Badge tone="warn">Not signed in</Badge>}
                      <Badge tone="accent">Claude {auth?.plan || 'subscription'}</Badge>
                      <span>via Claude Code {auth?.version}</span>
                    </div>
                  </div>
                  <Button size="sm" onClick={() => open('https://claude.ai/settings/usage')}>
                    <ExternalLink size={14} /> Usage
                  </Button>
                </div>
                <p className="mt-3 text-[12.5px] leading-relaxed text-muted">Everything runs on your Claude subscription through the official Claude Code app on this PC. No API key is used or stored, and this app never sees your password, cookies or tokens.</p>
              </div>
              <Row title="Claude Code location" hint={auth?.path ? <span className="break-all font-mono text-[11.5px]">{auth.path}</span> : 'Not found'}>
                <input aria-label="Claude Code path" defaultValue={settings.claude_path} placeholder="Auto-detect" onBlur={(e) => e.target.value !== settings.claude_path && set({ claude_path: e.target.value.trim() })} className="selectable no-drag h-9 w-[220px] rounded-xl border border-line bg-card px-3 text-[13px] outline-none focus:border-accent" />
              </Row>
              <Row title="Test the connection" hint="Runs a tiny task (takes a screenshot) to verify sign-in and the OS bridge end-to-end. Uses a little of your plan.">
                <Button onClick={() => { close(); client.send({ type: 'chat.send', chat_id: null, text: 'Connection test: take a screenshot, then tell me in one short sentence which app is in the foreground.' }) }}>Run test</Button>
              </Row>
              <Row title="Sign out" hint="Signs Claude Code out on this PC (you will need to sign in again to use the agent).">
                <Button variant="secondary" onClick={() => client.send({ type: 'auth.logout' })}>
                  <LogOut size={14} /> Sign out
                </Button>
              </Row>
            </>
          )}

          {tab === 'safety' && (
            <>
              <Row title="Approval prompts" hint="Smart: asks before deleting, sending, buying, admin or system changes. Ask for everything: asks before every click/keystroke/command. Never ask: fully autonomous (not recommended).">
                <Select label="Approval mode" value={settings.approval_mode} onChange={(v) => set({ approval_mode: v })} options={[['smart', 'Smart (recommended)'], ['all', 'Ask for everything'], ['never', 'Never ask (risky)']]} />
              </Row>
              <Row title="Max actions per run" hint="A runaway agent is stopped after this many OS actions.">
                <Select label="Max actions" value={settings.max_actions} onChange={(v) => set({ max_actions: v })} options={[[30, '30'], [75, '75'], [150, '150'], [300, '300'], [1000, '1000']]} />
              </Row>
              <Row title="Screenshot size" hint="Longest edge in pixels. Smaller is faster and uses less of your plan; larger reads tiny text better.">
                <Select label="Screenshot size" value={settings.screenshot_max_edge} onChange={(v) => set({ screenshot_max_edge: v })} options={[[1024, '1024 px'], [1280, '1280 px (default)'], [1568, '1568 px']]} />
              </Row>
              <Row title="Verify every action with a screenshot" hint="Claude sees the result of each click/keystroke immediately.">
                <Toggle label="Auto screenshot" checked={settings.auto_screenshot} onChange={(v) => set({ auto_screenshot: v })} />
              </Row>
              <Row title="Human-like mouse movement">
                <Toggle label="Human mouse" checked={settings.human_mouse} onChange={(v) => set({ human_mouse: v })} />
              </Row>
              <div className="my-4 rounded-2xl border border-line bg-card p-4 text-[13px] leading-relaxed">
                <div className="mb-1 font-medium">Kill switch</div>
                Press <kbd className="rounded border border-line px-1 font-mono text-[12px]">Esc</kbd> twice quickly, or <kbd className="rounded border border-line px-1 font-mono text-[12px]">Ctrl+Shift+Esc</kbd>, at any time to instantly halt all mouse/keyboard control and end the run.
                {ready && !ready.hotkeys && <div className="mt-2 text-warn">The global hotkey hook did not start on this PC, so use the STOP button.</div>}
              </div>
            </>
          )}

          {tab === 'voice' && (
            <>
              <Row title="Hands-free listening" hint="Always listening; speaking interrupts Claude immediately.">
                <Toggle label="Hands-free" checked={settings.hands_free} onChange={(v) => { set({ hands_free: v }); void voice.setHandsFree(v) }} />
              </Row>
              <Row title="Require wake word" hint={`In hands-free mode only act on speech starting with “${settings.wake_word}”.`}>
                <Toggle label="Wake word" checked={settings.wake_word_enabled} onChange={(v) => set({ wake_word_enabled: v })} />
              </Row>
              <Row title="Wake phrase">
                <input aria-label="Wake phrase" defaultValue={settings.wake_word} onBlur={(e) => e.target.value.trim() && e.target.value !== settings.wake_word && set({ wake_word: e.target.value.trim().toLowerCase() })} className="selectable no-drag h-9 w-[200px] rounded-xl border border-line bg-card px-3 text-[13.5px] outline-none focus:border-accent" />
              </Row>
              <Row title="Speak Claude’s replies" hint="Text-to-speech with edge-tts — no key needed.">
                <Toggle label="Speak replies" checked={settings.speak_responses} onChange={(v) => set({ speak_responses: v })} />
              </Row>
              <Row title="Voice">
                <Select label="Voice" value={settings.tts_voice} onChange={(v) => set({ tts_voice: v })} options={VOICES.map((v) => [v, v.replace('Neural', '')] as [string, string])} />
              </Row>
              <Row title="Speech recognition model" hint={ready?.stt === false ? 'faster-whisper is not available in this build.' : 'Runs locally with faster-whisper. Downloaded once on first use (tiny ≈ 75 MB, base ≈ 140 MB, small ≈ 460 MB).'}>
                <Select label="STT model" value={settings.stt_model} onChange={(v) => set({ stt_model: v })} options={[['tiny.en', 'tiny (English, fastest)'], ['base.en', 'base (English)'], ['small.en', 'small (English, accurate)'], ['base', 'base (multilingual)'], ['small', 'small (multilingual)']]} />
              </Row>
              <Row title="Language" hint="ISO code like en, de, fr — or empty to auto-detect (multilingual models).">
                <input aria-label="Language" defaultValue={settings.stt_language} onBlur={(e) => e.target.value !== settings.stt_language && set({ stt_language: e.target.value.trim() })} className="selectable no-drag h-9 w-[100px] rounded-xl border border-line bg-card px-3 text-[13.5px] outline-none focus:border-accent" />
              </Row>
            </>
          )}

          {tab === 'about' && (
            <div className="space-y-3 py-4 text-[13.5px] leading-relaxed">
              <div className="font-serif text-xl font-semibold">Claude OS Agent {ready?.version}</div>
              <div className="flex items-center gap-3">
                <Button size="sm" onClick={() => void window.api?.checkForUpdates()}>Check for updates</Button>
                <span className="text-[12.5px] text-muted">Updates download automatically and install when you restart (installed version only).</span>
              </div>
              <p className="text-muted">Operates Windows through the official Claude Code app signed in with your Claude subscription. OS control (mouse, keyboard, screen, UI Automation, PowerShell) runs locally in a sidecar process and is exposed to Claude Code over a private, token-protected local MCP bridge.</p>
              <div className="rounded-xl border border-line bg-card p-3 font-mono text-[12px] text-muted">
                screen: {ready?.screen.width}×{ready?.screen.height} → screenshots {ready?.screen.shot_width}×{ready?.screen.shot_height}
                <br />UI Automation: {ready?.uia ? 'available' : 'unavailable'} · speech recognition: {ready?.stt ? 'available' : 'unavailable'}
                {ready?.os_errors?.map((e) => <div key={e} className="text-danger">{e}</div>)}
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
