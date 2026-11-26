import type { BackendInfo } from './types'

type Unsub = () => void

declare global {
  interface Window {
    api?: {
      platform: string
      backendInfo(): Promise<BackendInfo | null>
      restartBackend(): Promise<BackendInfo | null>
      onBackendStatus(cb: (info: BackendInfo) => void): Unsub
      openExternal(url: string): Promise<void>
      setTheme(theme: 'light' | 'dark' | 'system'): Promise<void>
      setHud(on: boolean): Promise<void>
      appInfo(): Promise<{ version: string; dataDir: string; packaged: boolean }>
      sendVoiceLevel(level: number, mode: string): void
      onVoiceLevel(cb: (v: { level: number; mode: string }) => void): Unsub
      onPttToggle(cb: () => void): Unsub
    }
  }
}
export {}
