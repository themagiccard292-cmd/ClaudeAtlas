import { app, BrowserWindow, globalShortcut, ipcMain, nativeTheme, screen, session, shell } from 'electron'
import * as path from 'path'
import { BackendManager } from './backend'

const DEV_URL = !app.isPackaged && process.env.ELECTRON_DEV === '1' ? 'http://localhost:5173' : null
const HUD_TITLE = 'Claude OS Agent HUD' // the Python side finds these by exact title (click-through shield)
const LIVE_TITLE = 'Claude OS Agent Live'

let mainWin: BrowserWindow | null = null
let hudWin: BrowserWindow | null = null
let liveWin: BrowserWindow | null = null
let overlayWin: BrowserWindow | null = null
let minimizedByUs = false
let liveSize: 's' | 'm' | 'l' = 'm'
const LIVE_DIMS = { s: [360, 238], m: [500, 322], l: [760, 478] } as const
let backend: BackendManager | null = null
let quitting = false

const palette = {
  light: { bg: '#F5F4EF', fg: '#3D3D3A' },
  dark: { bg: '#262624', fg: '#C2C0B6' },
}
const effective = () => (nativeTheme.shouldUseDarkColors ? palette.dark : palette.light)

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWin) {
      if (mainWin.isMinimized()) mainWin.restore()
      mainWin.show()
      mainWin.focus()
    }
  })
}

function loadRenderer(win: BrowserWindow, hash = ''): void {
  if (DEV_URL) void win.loadURL(DEV_URL + (hash ? `#${hash}` : ''))
  else void win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), hash ? { hash } : undefined)
}

function hardenWebContents(win: BrowserWindow): void {
  // Never navigate away from the app, never open new app windows; external links go to the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    const allowed = DEV_URL ? url.startsWith(DEV_URL) : url.startsWith('file://')
    if (!allowed) e.preventDefault()
  })
}

function createMainWindow(): void {
  const c = effective()
  mainWin = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 760,
    minHeight: 520,
    show: false,
    backgroundColor: c.bg,
    title: 'Claude OS Agent',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    titleBarStyle: 'hidden', // Claude-style: content runs to the top, native caption buttons stay
    titleBarOverlay: { color: c.bg, symbolColor: c.fg, height: 40 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false, // keep voice + WebSocket alive while minimised into the HUD
    },
  })
  hardenWebContents(mainWin)
  mainWin.once('ready-to-show', () => mainWin?.show())
  mainWin.on('closed', () => {
    mainWin = null
    if (!quitting) app.quit()
  })
  loadRenderer(mainWin)
}

function createHud(): BrowserWindow {
  const wa = screen.getPrimaryDisplay().workArea
  const w = 640
  const h = 84
  const win = new BrowserWindow({
    width: w,
    height: h,
    x: Math.round(wa.x + (wa.width - w) / 2),
    y: wa.y + wa.height - h - 24,
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: false, // clicking STOP must not steal focus from the app Claude is working in
    hasShadow: false,
    show: false,
    title: HUD_TITLE,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  })
  win.setAlwaysOnTop(true, 'screen-saver')
  win.setContentProtection(true) // keep the HUD out of the screenshots Claude takes
  win.on('page-title-updated', (e) => e.preventDefault()) // title must stay exactly HUD_TITLE
  hardenWebContents(win)
  loadRenderer(win, '/hud')
  win.on('closed', () => {
    hudWin = null
  })
  return win
}

function auxWebPrefs() {
  return {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    backgroundThrottling: false,
  }
}

/** Floating "Claude's screen" window: live video + Claude's cursor. Hidden from capture (no mirror-in-mirror). */
function createLive(): BrowserWindow {
  const wa = screen.getPrimaryDisplay().workArea
  const [w, h] = LIVE_DIMS[liveSize]
  const win = new BrowserWindow({
    width: w, height: h, x: wa.x + wa.width - w - 20, y: wa.y + 20,
    frame: false, transparent: true, resizable: false, skipTaskbar: true, alwaysOnTop: true,
    focusable: false, hasShadow: false, show: false, title: LIVE_TITLE, webPreferences: auxWebPrefs(),
  })
  win.setAlwaysOnTop(true, 'screen-saver')
  win.setContentProtection(true)
  win.on('page-title-updated', (e) => e.preventDefault())
  hardenWebContents(win)
  loadRenderer(win, '/live')
  win.on('closed', () => { liveWin = null })
  return win
}

/** Full-screen, click-through overlay on the primary display that draws Claude's cursor + click ripples. */
function createOverlay(): BrowserWindow {
  const b = screen.getPrimaryDisplay().bounds
  const win = new BrowserWindow({
    x: b.x, y: b.y, width: b.width, height: b.height, frame: false, transparent: true, resizable: false, movable: false,
    skipTaskbar: true, alwaysOnTop: true, focusable: false, hasShadow: false, show: false, title: 'Claude OS Agent Overlay', webPreferences: auxWebPrefs(),
  })
  win.setAlwaysOnTop(true, 'screen-saver')
  win.setIgnoreMouseEvents(true) // never intercepts a click
  win.setContentProtection(true)
  win.on('page-title-updated', (e) => e.preventDefault())
  hardenWebContents(win)
  loadRenderer(win, '/overlay')
  win.on('closed', () => { overlayWin = null })
  return win
}

function showWhenLoaded(win: BrowserWindow): void {
  const show = () => win.showInactive()
  if (win.webContents.isLoading()) win.webContents.once('did-finish-load', show)
  else show()
}

interface RunUi { active: boolean; hud: boolean; live: boolean; overlay: boolean; minimizeMain: boolean }

/** One call from the renderer whenever the agent starts/stops or the related settings change. */
function setRunUi(o: RunUi): void {
  const want = (on: boolean, get: () => BrowserWindow | null, make: () => BrowserWindow, set: (w: BrowserWindow) => void) => {
    if (on && o.active) {
      let w = get()
      if (!w) { w = make(); set(w) }
      showWhenLoaded(w)
    } else get()?.hide()
  }
  want(o.hud, () => hudWin, createHud, (w) => { hudWin = w })
  want(o.live, () => liveWin, createLive, (w) => { liveWin = w })
  want(o.overlay, () => overlayWin, createOverlay, (w) => { overlayWin = w })
  if (o.active && o.minimizeMain) {
    if (mainWin && !mainWin.isMinimized()) { mainWin.minimize(); minimizedByUs = true }
  } else if (minimizedByUs) {
    minimizedByUs = false
    if (mainWin) {
      if (mainWin.isMinimized()) mainWin.restore()
      mainWin.show()
      mainWin.focus()
    }
  }
}

function setLiveSize(size: 's' | 'm' | 'l'): void {
  liveSize = size
  if (!liveWin) return
  const wa = screen.getPrimaryDisplay().workArea
  const [w, h] = LIVE_DIMS[size]
  liveWin.setBounds({ x: wa.x + wa.width - w - 20, y: wa.y + 20, width: w, height: h })
}

function applyTitleBarTheme(): void {
  const c = effective()
  if (mainWin && process.platform === 'win32') {
    try {
      mainWin.setTitleBarOverlay({ color: c.bg, symbolColor: c.fg, height: 40 })
      mainWin.setBackgroundColor(c.bg)
    } catch {
      /* not supported on this Windows build */
    }
  }
}

function registerIpc(): void {
  ipcMain.handle('backend:info', () => backend?.info ?? null)
  ipcMain.handle('backend:restart', async () => {
    await backend?.restart()
    return backend?.info ?? null
  })
  ipcMain.handle('shell:openExternal', (_e, url: string) => {
    if (typeof url === 'string' && /^https:\/\//i.test(url)) return shell.openExternal(url)
    return undefined
  })
  ipcMain.handle('theme:set', (_e, theme: 'light' | 'dark' | 'system') => {
    nativeTheme.themeSource = theme
    applyTitleBarTheme()
  })
  ipcMain.handle('run:ui', (_e, o: RunUi) => setRunUi(o))
  ipcMain.handle('live:size', (_e, size: 's' | 'm' | 'l') => setLiveSize(size))
  ipcMain.handle('app:info', () => ({ version: app.getVersion(), dataDir: app.getPath('userData'), packaged: app.isPackaged }))
  ipcMain.on('voice:level', (_e, payload) => hudWin?.webContents.send('voice:level', payload))
}

app.setAppUserModelId('com.claudeosagent.app')

app.whenReady().then(async () => {
  if (!app.hasSingleInstanceLock()) return
  registerIpc()

  // Microphone for voice control - only for our own pages; nothing else is ever granted.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'media'))
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media')

  backend = new BackendManager(app.getPath('userData'))
  backend.on('status', (info) => {
    for (const w of [mainWin, hudWin, liveWin, overlayWin]) w?.webContents.send('backend:status', info)
  })
  void backend.start() // renderer shows "starting engine…" until the sidecar reports ready

  createMainWindow()
  nativeTheme.on('updated', applyTitleBarTheme)

  // Alt+Space would open the window system menu; swallow it. Press/release detection lives in the
  // backend's low-level hook; this handler is only the fallback when that hook is unavailable.
  globalShortcut.register('Alt+Space', () => mainWin?.webContents.send('ptt:toggle'))
})

app.on('before-quit', () => {
  quitting = true
  globalShortcut.unregisterAll()
  backend?.stop() // kills backend.exe and its whole process tree (PowerShell, claude CLI)
})
app.on('window-all-closed', () => app.quit())
process.on('exit', () => backend?.stop())
