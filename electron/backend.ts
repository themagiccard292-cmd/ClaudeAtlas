import { ChildProcess, spawn, spawnSync } from 'child_process'
import { app } from 'electron'
import { EventEmitter } from 'events'
import * as crypto from 'crypto'
import * as fs from 'fs'
import * as http from 'http'
import * as net from 'net'
import * as path from 'path'

export type BackendStatus = 'starting' | 'ready' | 'restarting' | 'failed' | 'stopped'
export interface BackendInfo {
  port: number
  token: string
  status: BackendStatus
  error?: string
}

/** Ask the OS for a free localhost port. */
export function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address() as net.AddressInfo
      srv.close(() => resolve(port))
    })
  })
}

function healthy(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/health', timeout: 1500 }, (res) => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('error', () => resolve(false))
    req.on('timeout', () => {
      req.destroy()
      resolve(false)
    })
  })
}

/** Where is the Python sidecar? Packaged: resources/backend/backend.exe. Dev: the venv's python. */
export function resolveBackendCommand(): { command: string; args: string[]; cwd: string } {
  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, 'backend')
    return { command: path.join(dir, process.platform === 'win32' ? 'backend.exe' : 'backend'), args: [], cwd: dir }
  }
  const backendDir = path.join(app.getAppPath(), 'backend')
  const venvPy = process.platform === 'win32' ? path.join(backendDir, '.venv', 'Scripts', 'python.exe') : path.join(backendDir, '.venv', 'bin', 'python')
  const py = fs.existsSync(venvPy) ? venvPy : process.platform === 'win32' ? 'python' : 'python3'
  return { command: py, args: ['-m', 'app.main'], cwd: backendDir }
}

export class BackendManager extends EventEmitter {
  private child: ChildProcess | null = null
  private stopping = false
  private restarts = 0
  private logStream: fs.WriteStream | null = null
  info: BackendInfo = { port: 0, token: '', status: 'starting' }

  constructor(private dataDir: string) {
    super()
  }

  private setInfo(patch: Partial<BackendInfo>): void {
    this.info = { ...this.info, ...patch }
    this.emit('status', this.info)
  }

  async start(): Promise<void> {
    this.stopping = false
    const port = await getFreePort()
    // A fresh random secret per launch: only this app's windows can talk to the backend.
    const token = crypto.randomBytes(24).toString('hex')
    this.setInfo({ port, token, status: this.restarts ? 'restarting' : 'starting', error: undefined })

    const { command, args, cwd } = resolveBackendCommand()
    fs.mkdirSync(path.join(this.dataDir, 'logs'), { recursive: true })
    this.logStream?.end()
    this.logStream = fs.createWriteStream(path.join(this.dataDir, 'logs', 'sidecar.log'), { flags: 'a' })

    const child = spawn(command, [...args, '--port', String(port), '--data-dir', this.dataDir, '--parent-pid', String(process.pid)], {
      cwd,
      windowsHide: true, // silent: no console window
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, CLAUDE_OS_AGENT_TOKEN: token, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    })
    this.child = child
    child.stdout?.on('data', (d) => this.logStream?.write(d))
    child.stderr?.on('data', (d) => this.logStream?.write(d))
    child.on('error', (err) => this.setInfo({ status: 'failed', error: `Could not start the OS-control engine: ${err.message}` }))
    child.on('exit', (code, signal) => {
      if (this.child !== child) return
      this.child = null
      if (this.stopping) return
      if (this.restarts < 3) {
        this.restarts += 1
        this.setInfo({ status: 'restarting', error: `engine exited (${signal ?? code}); restarting…` })
        setTimeout(() => void this.start(), 800 * this.restarts)
      } else {
        this.setInfo({ status: 'failed', error: `The OS-control engine keeps crashing (exit ${signal ?? code}). See logs\\sidecar.log.` })
      }
    })

    const deadline = Date.now() + 45_000
    while (Date.now() < deadline) {
      if (this.child !== child) return // exited / replaced while waiting
      if (await healthy(port)) {
        this.restarts = 0
        this.setInfo({ status: 'ready', error: undefined })
        return
      }
      await new Promise((r) => setTimeout(r, 250))
    }
    this.setInfo({ status: 'failed', error: 'The OS-control engine did not start within 45 seconds.' })
  }

  /** Kill the sidecar *and everything it spawned* (PowerShell, the claude CLI). Safe to call twice. */
  stop(): void {
    this.stopping = true
    const child = this.child
    this.child = null
    if (!child || child.exitCode !== null || child.pid === undefined) return
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 5000 })
      } else {
        child.kill('SIGTERM')
      }
    } catch {
      /* already gone */
    }
    this.logStream?.end()
    this.setInfo({ status: 'stopped' })
  }

  async restart(): Promise<void> {
    this.stop()
    this.restarts = 0
    await this.start()
  }
}
