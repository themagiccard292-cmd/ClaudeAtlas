import { Mic, OctagonX } from 'lucide-react'
import { useEffect, useState } from 'react'
import { client } from '../lib/backend'
import { useStore } from '../store'
import { Visualizer } from './Visualizer'
import { respond } from './ApprovalModal'

/** Floating always-on-top pill shown while Claude controls the mouse/keyboard. */
export function Hud() {
  const agentState = useStore((s) => s.agentState)
  const hud = useStore((s) => s.hud)
  const approval = useStore((s) => s.approvals[0])
  const [voice, setVoice] = useState({ level: 0, mode: 'idle' })

  useEffect(() => window.api?.onVoiceLevel((v) => setVoice(v)), [])
  useEffect(() => {
    // the HUD is a separate window: keep the transparent page free of scrollbars/selection
    document.documentElement.classList.add('hud')
  }, [])

  const paused = agentState === 'paused'
  const talking = voice.mode === 'listening' || voice.mode === 'speaking'
  const status = approval ? approval.summary : paused ? 'Paused — listening for your instruction…' : hud.status || 'Claude is working…'

  return (
    <div className="drag flex h-full w-full items-center justify-center p-[10px]">
      <div className="flex h-[60px] w-full items-center gap-3 rounded-full border border-white/10 bg-[#1f1e1d]/95 pl-4 pr-2 text-[#f5f4ee] shadow-[0_8px_30px_rgba(0,0,0,.45)] backdrop-blur-md">
        <span className={`h-3 w-3 shrink-0 rounded-full ${approval ? 'bg-amber-400' : paused ? 'bg-sky-400' : 'pulse-ring bg-[#d97757]'}`} />
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate text-[13px] font-medium" title={status}>{status}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-white/55">
            <span>{approval ? 'Approval needed' : paused ? 'Paused' : `Step ${hud.steps}`}</span>
            {talking && (
              <>
                <Mic size={11} />
                <span>{voice.mode === 'speaking' ? 'Speaking' : 'Listening'}</span>
              </>
            )}
          </div>
        </div>
        {!approval && <Visualizer level={voice.level} active={talking || agentState === 'running'} bars={16} height={24} className="no-drag hidden w-[88px] sm:flex" color="#d97757" />}
        {approval ? (
          <div className="no-drag flex shrink-0 items-center gap-1.5">
            <button onClick={() => respond(approval.id, false)} className="h-10 rounded-full bg-white/10 px-4 text-[13px] font-medium hover:bg-white/20">Deny</button>
            <button onClick={() => respond(approval.id, true)} className="h-10 rounded-full bg-[#d97757] px-4 text-[13px] font-semibold text-[#1f1e1d] hover:brightness-110">Approve</button>
          </div>
        ) : (
          <button
            onClick={() => client.send({ type: 'agent.stop' })}
            title="Stop Claude and take over (or press Esc twice)"
            className="no-drag flex h-11 shrink-0 items-center gap-2 rounded-full bg-[#c23b2b] px-5 text-[13px] font-bold tracking-wide text-white transition hover:bg-[#d4493a] active:scale-95"
          >
            <OctagonX size={17} /> STOP / TAKE OVER
          </button>
        )}
      </div>
    </div>
  )
}
