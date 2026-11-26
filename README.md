# Claude OS Agent  `v1.1`

A Windows desktop app (Electron + React) that looks like the Claude desktop app and lets Claude operate your PC —
mouse, keyboard, screen, Windows UI Automation, PowerShell — **using your Claude Pro subscription, with no API key.**

```
┌──────────────────────── Electron (React UI, Mini-HUD) ────────────────────────┐
│  chat · action accordions · voice visualizer · approvals · settings · sign-in │
└───────────────▲──────────────── WebSocket (127.0.0.1, per-launch token) ──────┘
                │
┌───────────────┴────────────── backend.exe  (Python, PyInstaller) ─────────────┐
│  FastAPI ── run orchestrator ──spawns──►  claude -p  (your Claude Code login)  │
│     │                                          │  stream-json (text, steps)    │
│     └── /mcp  (local MCP server) ◄─────────────┘  tool calls: click, type, …   │
│            │                                                                    │
│   OS tools: mss capture · pyautogui/pynput · pywinauto UIA · PowerShell         │
│   Safety : kill switch · approvals · risk rules · action budget                │
│   Voice  : faster-whisper (local STT) · edge-tts · push-to-talk · wake word    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## What's new in v1.1 — live view

Watch Claude work in real time instead of reading screenshots:

* **Live video of the screen** streamed from the backend (default 10 fps, 960 px, adjustable; unchanged frames are
  skipped and slow viewers drop stale frames instead of lagging).
* **Claude's cursor**: a labelled terracotta cursor that glides exactly where the real pointer goes, with click
  ripples (left / right / double) and badges (“Typing 12 chars…”, “Scrolling ↓”, “ctrl+s”, “Dragging”).
* **Three ways to watch** (Settings → General → Live view): a **floating always-on-top window** (default, resizable,
  hidden from capture so it never mirrors itself), a **panel inside the app** (ideal with two monitors — only the
  primary monitor is controlled), or off. Plus an optional **on-screen cursor overlay** drawn on your real desktop.
* The live window and the HUD are click-through at the exact moment Claude clicks beneath them.

Honest note: Claude itself still *perceives* through snapshots (Claude Code accepts images, not video); the live view
is video for **you**. Action thumbnails in the chat are still there but collapsed.

## Why it signs in through Claude Code (and not an embedded claude.ai session)

The brief asked for an embedded `claude.ai` login with cookie capture and a DOM/network interceptor. **That is not
built, deliberately.** Anthropic's Claude Code terms say third-party developers may not *"offer Claude.ai login into
their own applications, or … route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users"*,
and may not *"collect, store, or intermediate Claude.ai credentials or session tokens"*
([legal & compliance](https://code.claude.com/docs/en/legal-and-compliance)). Scripting the consumer web app also
risks getting your account suspended and would be brittle.

The same page carves out the supported route, which is what this app uses: **the end user signs in to the unmodified
Claude Code binary with their own subscription.** So:

* The user runs **Sign in with Claude Pro** → the app runs `claude auth login --claudeai` (Anthropic's own flow, in
  your browser). The window switches to the agent UI automatically once `claude auth status` reports a subscription login.
* Each task is one `claude -p --output-format stream-json` process. Claude Code is the agent loop; this app only
  supplies the hands and eyes as a local **MCP server** (`mcp__osagent__*` tools) and renders the result.
* The app never reads, stores or forwards a password, cookie or token, and never touches `~/.claude` credentials.
* `ANTHROPIC_API_KEY` & friends are **stripped from the child's environment** (in `-p` mode a set key would otherwise
  silently bill the API instead of your plan), and runs are refused unless `auth status` shows a subscription login.
* Intended for **personal use on your own PC with your own login**. Plan limits assume ordinary individual use.

## Folder structure

```
ClaudeAtlas/
├─ build.bat                    one-click build → release\Claude-OS-Agent-Setup.exe + …-Portable.exe
├─ package.json                 scripts + electron-builder config (NSIS + portable, extraResources)
├─ electron/                    main process (TypeScript)
│  ├─ main.ts                   windows, Mini-HUD, theme, IPC, Alt+Space swallow
│  ├─ backend.ts                free port → silent spawn of backend.exe → health wait → restart → tree-kill
│  └─ preload.ts                minimal contextBridge API
├─ src/                         renderer (React + TS + Tailwind v4)
│  ├─ App.tsx  store.ts  types.ts
│  ├─ components/               ChatView (action accordions) · PromptBar · Sidebar · SettingsModal · SignIn
│  │                            ApprovalModal · Hud · Visualizer · Markdown · ui
│  └─ lib/                      backend.ts (WebSocket client) · voice.ts (PTT, VAD, barge-in, TTS playback)
├─ backend/
│  ├─ app/
│  │  ├─ main.py                entry: DPI awareness FIRST, free port, parent-PID watchdog
│  │  ├─ dpi.py  screen.py      SetProcessDpiAwareness(2), primary monitor, scaling/coordinate mapping
│  │  ├─ input_control.py       pyautogui/pynput driver, kill-switch-aware primitives, key normalisation
│  │  ├─ uia.py                 pywinauto UI Automation snapshot / click / set text / focus
│  │  ├─ shell.py               PowerShell tool (encoded command, timeout, output cap)
│  │  ├─ osagent_tools.py       the tool set Claude sees + approvals, auto-verify screenshots
│  │  ├─ mcp_server.py          minimal MCP (streamable HTTP) endpoint, bearer-token + Origin checks
│  │  ├─ claude_cli.py          locate/probe `claude`, auth status/login, billing-safe env, stream-json parser
│  │  ├─ runner.py              run lifecycle: stop / pause / voice-interrupt (kill + --resume)
│  │  ├─ safety.py              KillSwitch, global hotkey logic, risk rules, ApprovalManager
│  │  ├─ live.py                live screen stream (binary WebSocket frames) · cursor events
│  │  ├─ voice.py  hudshield.py local STT/TTS helpers; keeps the HUD from eating Claude's clicks
│  │  └─ server.py  history.py  config.py  events.py  prompts.py  procutil.py
│  ├─ backend.spec              PyInstaller spec    ·  build_backend.bat
│  ├─ requirements*.txt
│  └─ tests/                    127 tests incl. end-to-end against a fake `claude` CLI
├─ scripts/make_icons.py        generates build/icon.ico + icon.png
└─ build/                       icons
```

## Build the .exe

On a Windows 10/11 x64 machine with **Node.js 22+** and **Python 3.10–3.13** (3.12 recommended; 3.14+ is not supported yet) on PATH:

```bat
build.bat
```

That runs, in order (you can run them yourself):

```bat
:: 1) Python sidecar  → backend\dist\backend\backend.exe
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --distpath dist --workpath build backend.spec
cd ..

:: 2) UI + Electron + installers → release\
npm ci
npm run dist          :: = vite build + tsc (electron) + electron-builder --win nsis portable --x64
```

`electron-builder` bundles `backend\dist\backend\` into the app via `extraResources` (→ `resources\backend\backend.exe`).
The result needs **no Python or Node on the target PC**. The backend is one-dir (not one-file) on purpose: it starts
instantly and avoids the antivirus false-positives of self-extracting one-file exes.
Unsigned builds trigger SmartScreen; sign them for distribution.

**End users need nothing pre-installed** (no Python, Node or API key). If Claude Code is missing, the first screen has
an **Install Claude Code automatically** button that runs Anthropic's official installer
(`irm https://claude.ai/install.ps1 | iex`, no Node needed), then moves straight on to sign-in.

**No build tools on your PC either:** run the **Build Windows installer** GitHub Action (Actions tab → Run workflow,
or push a `v*` tag) and download `Claude-OS-Agent-Setup.exe` / `…-Portable.exe` from the run's artifacts.

### Development

```bat
cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt && cd ..
npm install
npm run dev            :: Vite + tsc watch + Electron; backend runs from backend\.venv
npm run typecheck
npm run test:backend
```

## Using it

| | |
|---|---|
| **Sign in** | First launch: *Sign in with Claude Pro* → finish in your browser → the agent UI opens by itself. |
| **Chat** | Describe a task. Steps appear in a collapsible **Agent actions** accordion: screenshot thumbnails with the click marked, coordinates, PowerShell commands + output. Sending a message mid-run redirects Claude. |
| **Mini-HUD** | While Claude acts, the window minimises into an always-on-top pill (kept out of Claude's screenshots, click-through where Claude clicks) with status, voice waveform and **STOP / TAKE OVER**. |
| **Kill switch** | **Esc Esc** (quickly) or **Ctrl+Shift+Esc**, system-wide. Halts all mouse/keyboard threads, releases held keys/buttons, kills the Claude process. Keys the agent itself injects are ignored, so it cannot trip its own switch. |
| **Voice** | Hold **Alt+Space** (or the mic button) to talk; or enable *Hands-free* (optional wake word, default “hey claude”). Speaking while Claude works **pauses it instantly**, transcribes locally, then redirects it; speaking over Claude’s voice stops the speech. Say “approve”/“deny” to answer approval prompts. STT: faster-whisper (model downloaded once, then offline). TTS: edge-tts. No keys. |
| **Workflows** | “Save as workflow” on any message; run it later from the sidebar. |

## Safety model

* **Approvals** (modal, HUD buttons, or voice) before: deleting, sending email/messages, purchases, admin/system changes.
  Enforced three ways: Claude is instructed to call `request_approval`; PowerShell commands are risk-classified
  (delete/registry/`-Verb RunAs`/shutdown/download-and-execute…); GUI clicks check the control name under the
  pointer through UI Automation (Send/Delete/Buy/Place order…). Denial returns an error to Claude and nothing runs.
  Modes: *Smart* (default) · *Ask for everything* · *Never ask* (explicit opt-in). No answer in 4 minutes = deny.
* Claude Code runs with built-in tools limited to `WebSearch`/`WebFetch`, `--permission-mode dontAsk`, and an
  allow-list of exactly the `mcp__osagent__*` tools — it has no shell or file tools of its own; everything goes through
  the approval-aware bridge.
* **Per-run action budget** (default 150), `Win+L` and `Ctrl+Alt+Del` combos blocked.
* The local server binds to `127.0.0.1`; the UI socket needs a random per-launch token **and** an allowed Origin
  (so no web page can drive your PC); `/mcp` needs its own bearer token and rejects any request with an `Origin` header.
* Screenshots show your screen to Claude — don’t run it while sensitive windows are open.
* Windows can’t be controlled across UAC/elevation boundaries: the app runs un-elevated, so elevated windows
  and the secure desktop are out of reach (by design).

## Testing — what is and isn't verified

Verified in a Linux container:
* 127 backend tests (`npm run test:backend`): key handling, kill switch, hotkey logic, risk rules, approvals,
  screen scaling and coordinate mapping, input controller (incl. “drag is killed mid-way → button released”),
  UIA snapshot logic, PowerShell runner, stream-json parser, and **end-to-end runs through the real WebSocket API and
  the real MCP endpoint** against a fake `claude` CLI (approve/deny, stop, hotkey kill, voice interruption → `--resume`,
  budget, not-signed-in, API-key stripping).
* The **real Claude Code client connects to the MCP endpoint** (`claude mcp list` → ✓ Connected).
* A PyInstaller build of the spec starts and serves; the **real Electron** app boots headlessly: preload bridge,
  `file://` + strict CSP, sidecar spawn, HUD window, and sidecar teardown on both normal quit and Electron crash.
* UI rendered and driven in Chromium (light/dark, sign-in, approval, HUD, settings).

**Not verified (needs a real Windows box + Claude login):** pyautogui/pynput/pywinauto/mss on Windows, the low-level
hotkey hook, DPI behaviour on scaled displays, the HUD click-through shield, `claude auth login`, an actual model run
(`claude -p` stream-json shapes are implemented from Anthropic's docs and parsed defensively), electron-builder output.
Use **Settings → Claude account → Run test** after installing; logs are in `%APPDATA%\Claude OS Agent\logs`.

## Troubleshooting

* **“Install Claude Code” screen** — run the installer command shown, then *check again* (or set the path in Settings).
* **“API billing, not a subscription”** — `claude auth status` shows an API/console login or an `apiKeyHelper`; use
  *Sign in with Claude Pro* (`claude auth login --claudeai`).
* **Clicks land in the wrong place** — shouldn’t happen (per-monitor DPI awareness is set first); check Settings → About
  for the detected screen size, and note only the **primary** monitor is controlled.
* **Usage limit reached** — your plan’s limit; lower screenshot size / effort, or wait (claude.ai/settings/usage).
* **electron-builder “Cannot create symbolic link”** — enable Windows Developer Mode or build as Administrator.
