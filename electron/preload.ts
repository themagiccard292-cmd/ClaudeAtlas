import { contextBridge, ipcRenderer } from 'electron'

type Unsub = () => void
const on = <T>(channel: string, cb: (payload: T) => void): Unsub => {
  const handler = (_e: unknown, payload: T) => cb(payload)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

contextBridge.exposeInMainWorld('api', {
  platform: process.platform,
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  onBackendStatus: (cb: (info: unknown) => void) => on('backend:status', cb),
  openExternal: (url: string) => ipcRenderer.invoke('shell:openExternal', url),
  setTheme: (theme: 'light' | 'dark' | 'system') => ipcRenderer.invoke('theme:set', theme),
  setRunUi: (o: { active: boolean; hud: boolean; overlay: boolean; minimizeMain: boolean }) => ipcRenderer.invoke('run:ui', o),
  onApprovalKey: (cb: (approve: boolean) => void) => on('approval:key', cb),
  onUpdateStatus: (cb: (s: unknown) => void) => on('update:status', cb),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  appInfo: () => ipcRenderer.invoke('app:info'),
  sendVoiceLevel: (level: number, mode: string) => ipcRenderer.send('voice:level', { level, mode }),
  onVoiceLevel: (cb: (v: { level: number; mode: string }) => void) => on('voice:level', cb),
  onPttToggle: (cb: () => void) => on('ptt:toggle', cb),
})
