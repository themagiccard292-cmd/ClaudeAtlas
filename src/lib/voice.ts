import { useStore } from '../store'
import type { BackendClient, BackendEvent } from './backend'

const SAMPLE_RATE = 16000
const FRAME = 1024 // ~64 ms at 16 kHz
const PRE_ROLL_FRAMES = 6
const START_FRAMES = 2 // consecutive loud frames that count as "speech started"
const END_SILENCE_FRAMES = 14 // ~0.9 s of quiet ends an utterance
const MIN_SPEECH_FRAMES = 5 // shorter than ~0.3 s = a cough, not a command
const MAX_UTTERANCE_FRAMES = Math.round((30 * SAMPLE_RATE) / FRAME)
const MIC_WARM_MS = 60_000

export function encodeWav(samples: Float32Array, sampleRate = SAMPLE_RATE): Uint8Array {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const v = new DataView(buf)
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  w(0, 'RIFF')
  v.setUint32(4, 36 + samples.length * 2, true)
  w(8, 'WAVE')
  w(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true) // PCM
  v.setUint16(22, 1, true) // mono
  v.setUint32(24, sampleRate, true)
  v.setUint32(28, sampleRate * 2, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  w(36, 'data')
  v.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]))
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true)
  }
  return new Uint8Array(buf)
}

export function toBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

const rms = (f: Float32Array): number => {
  let s = 0
  for (let i = 0; i < f.length; i++) s += f[i] * f[i]
  return Math.sqrt(s / f.length)
}

class VoiceEngine {
  private client: BackendClient | null = null
  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private proc: ScriptProcessorNode | null = null
  private handsFree = false
  private pttHeld = false
  private recording: 'ptt' | 'vad' | null = null
  private frames: Float32Array[] = []
  private preRoll: Float32Array[] = []
  private noise = 0.008
  private loud = 0
  private quiet = 0
  private speechFrames = 0
  private warmTimer: number | undefined
  private lastLevelPush = 0
  private starting: Promise<boolean> | null = null
  // playback
  private playCtx: AudioContext | null = null
  private outAnalyser: AnalyserNode | null = null
  private queue: AudioBuffer[] = []
  private current: AudioBufferSourceNode | null = null
  private speaking = false
  private ttsStreaming = false // server is still synthesising sentences for us
  private meterRaf = 0

  init(client: BackendClient): void {
    this.client = client
  }

  private store = () => useStore.getState()

  // ---------------------------------------------------------------- microphone
  private async ensureMic(): Promise<boolean> {
    window.clearTimeout(this.warmTimer)
    if (this.stream && this.ctx) return true
    if (this.starting) return this.starting
    this.starting = (async () => {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        })
        this.ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
        const src = this.ctx.createMediaStreamSource(this.stream)
        this.proc = this.ctx.createScriptProcessor(FRAME, 1, 1)
        this.proc.onaudioprocess = (e) => this.onFrame(new Float32Array(e.inputBuffer.getChannelData(0)))
        src.connect(this.proc)
        this.proc.connect(this.ctx.destination) // required for processing; we output silence
        this.store().setVoice({ micError: '' })
        return true
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        this.store().setVoice({ micError: msg })
        this.store().toast(`Microphone unavailable: ${msg}`, 'error')
        this.releaseMic()
        return false
      } finally {
        this.starting = null
      }
    })()
    return this.starting
  }

  private releaseMic(): void {
    this.proc?.disconnect()
    this.proc = null
    void this.ctx?.close()
    this.ctx = null
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
  }

  private scheduleRelease(): void {
    window.clearTimeout(this.warmTimer)
    if (this.handsFree || this.pttHeld) return
    // keep the mic "warm" briefly so a second push-to-talk doesn't clip the first word
    this.warmTimer = window.setTimeout(() => !this.handsFree && !this.pttHeld && this.releaseMic(), MIC_WARM_MS)
  }

  // ---------------------------------------------------------------- hands-free
  async setHandsFree(on: boolean): Promise<void> {
    this.handsFree = on
    if (on) {
      if (!(await this.ensureMic())) {
        this.handsFree = false
        this.store().toast('Could not start hands-free listening.', 'error')
      }
    } else {
      if (this.recording === 'vad') this.cancelUtterance()
      this.scheduleRelease()
    }
    this.syncMode()
  }

  // ---------------------------------------------------------------- push-to-talk
  async pttDown(): Promise<void> {
    if (this.pttHeld) return
    this.pttHeld = true
    this.stopSpeaking()
    this.client?.send({ type: 'voice.speech_start' }) // pause whatever Claude is doing right now
    if (!(await this.ensureMic()) || !this.pttHeld) {
      this.pttHeld = false
      this.client?.send({ type: 'voice.speech_cancel' })
      return
    }
    this.frames = []
    this.recording = 'ptt'
    this.syncMode()
  }

  pttUp(): void {
    if (!this.pttHeld) return
    this.pttHeld = false
    if (this.recording === 'ptt') this.finishUtterance('ptt')
    this.scheduleRelease()
  }

  /** Alt+Space fallback when the backend's low-level key hook is unavailable (press toggles). */
  pttToggle(): void {
    if (this.pttHeld) this.pttUp()
    else void this.pttDown()
  }

  // ---------------------------------------------------------------- frame processing
  private onFrame(frame: Float32Array): void {
    const level = rms(frame)
    this.pushLevel(this.speaking ? this.outLevel() : level)

    if (this.recording === 'ptt') {
      this.frames.push(frame)
      if (this.frames.length > MAX_UTTERANCE_FRAMES) this.finishUtterance('ptt')
      return
    }
    if (!this.handsFree) return

    // adaptive threshold; much higher while Claude is talking (speaker bleed) so only real barge-in triggers
    const gate = Math.max(0.018, this.noise * 3.2) * (this.speaking ? 2.6 : 1)
    if (this.recording === 'vad') {
      this.frames.push(frame)
      if (level > gate) this.speechFrames++
      this.quiet = level < gate * 0.6 ? this.quiet + 1 : 0
      if (this.quiet >= END_SILENCE_FRAMES || this.frames.length > MAX_UTTERANCE_FRAMES) {
        if (this.speechFrames < MIN_SPEECH_FRAMES) this.cancelUtterance()
        else this.finishUtterance('vad')
      }
      return
    }
    this.preRoll.push(frame)
    if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift()
    if (level > gate) {
      this.loud++
      if (this.loud >= START_FRAMES) {
        this.recording = 'vad'
        this.frames = [...this.preRoll]
        this.speechFrames = this.loud
        this.quiet = 0
        this.loud = 0
        this.stopSpeaking() // barge-in: stop talking...
        this.client?.send({ type: 'voice.speech_start' }) // ...and pause execution immediately
        this.syncMode()
      }
    } else {
      this.loud = 0
      this.noise = this.noise * 0.97 + level * 0.03
    }
  }

  private cancelUtterance(): void {
    this.recording = null
    this.frames = []
    this.speechFrames = 0
    this.client?.send({ type: 'voice.speech_cancel' })
    this.syncMode()
  }

  private finishUtterance(mode: 'ptt' | 'vad'): void {
    const frames = this.frames
    this.frames = []
    this.recording = null
    this.speechFrames = 0
    this.syncMode()
    const total = frames.reduce((n, f) => n + f.length, 0)
    if (total < FRAME * MIN_SPEECH_FRAMES) {
      this.client?.send({ type: 'voice.speech_cancel' })
      return
    }
    const pcm = new Float32Array(total)
    let o = 0
    for (const f of frames) {
      pcm.set(f, o)
      o += f.length
    }
    this.client?.send({ type: 'voice.utterance', mode, audio_b64: toBase64(encodeWav(pcm)), chat_id: this.store().activeChatId })
  }

  // ---------------------------------------------------------------- UI plumbing
  private syncMode(): void {
    const st = this.store()
    const mode = this.speaking ? 'speaking' : this.recording || this.handsFree ? 'listening' : st.voice.mode === 'transcribing' ? 'transcribing' : 'idle'
    if (st.voice.mode !== mode) st.setVoice({ mode })
  }

  private pushLevel(level: number): void {
    const now = performance.now()
    if (now - this.lastLevelPush < 45) return
    this.lastLevelPush = now
    const v = Math.min(1, level * 9)
    this.store().setVoice({ level: v })
    window.api?.sendVoiceLevel(v, this.store().voice.mode)
  }

  // ---------------------------------------------------------------- backend events (TTS + hotkeys)
  handleBackendEvent(e: BackendEvent): void {
    if (e.type === 'tts.chunk') {
      this.ttsStreaming = !e.last
      void this.enqueue(e.audio_b64 as string)
    }
    else if (e.type === 'tts.stop') this.stopLocal() // server told us to stop: do NOT echo tts.cancel back (would loop)
    else if (e.type === 'hotkey') {
      if (e.name === 'ptt_down') void this.pttDown()
      else if (e.name === 'ptt_up') this.pttUp()
    }
  }

  speak(text: string): void {
    this.client?.send({ type: 'tts.speak', text })
  }

  private outLevel(): number {
    const a = this.outAnalyser
    if (!a) return 0
    const buf = new Float32Array(a.fftSize)
    a.getFloatTimeDomainData(buf)
    return rms(buf)
  }

  private async enqueue(b64: string): Promise<void> {
    try {
      this.playCtx ??= new AudioContext()
      if (!this.outAnalyser) {
        this.outAnalyser = this.playCtx.createAnalyser()
        this.outAnalyser.fftSize = 512
        this.outAnalyser.connect(this.playCtx.destination)
      }
      const bin = atob(b64)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
      this.queue.push(await this.playCtx.decodeAudioData(bytes.buffer))
      if (!this.current) this.playNext()
    } catch {
      /* undecodable chunk: skip */
    }
  }

  private playNext(): void {
    const buf = this.queue.shift()
    if (!buf || !this.playCtx || !this.outAnalyser) {
      this.current = null
      this.speaking = false
      cancelAnimationFrame(this.meterRaf)
      this.syncMode()
      return
    }
    const src = this.playCtx.createBufferSource()
    src.buffer = buf
    src.connect(this.outAnalyser)
    src.onended = () => {
      if (this.current === src) this.playNext()
    }
    this.current = src
    this.speaking = true
    this.syncMode()
    const meter = () => {
      if (!this.speaking) return
      if (!this.recording && !this.pttHeld) this.pushLevel(this.outLevel())
      this.meterRaf = requestAnimationFrame(meter)
    }
    cancelAnimationFrame(this.meterRaf)
    meter()
    src.start()
  }

  /** Stop playback on this client only. */
  private stopLocal(): void {
    this.queue = []
    const cur = this.current
    this.current = null
    if (cur) {
      cur.onended = null
      try {
        cur.stop()
      } catch {
        /* already stopped */
      }
    }
    this.speaking = false
    this.ttsStreaming = false
    cancelAnimationFrame(this.meterRaf)
    this.syncMode()
  }

  /** Stop playback and tell the backend to stop synthesising the rest. */
  stopSpeaking(): void {
    const wasActive = this.speaking || this.queue.length > 0 || this.ttsStreaming
    this.stopLocal()
    if (wasActive) this.client?.send({ type: 'tts.cancel' })
  }
}

export const voice = new VoiceEngine()
