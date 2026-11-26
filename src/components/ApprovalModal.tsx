import { Mic, ShieldAlert } from 'lucide-react'
import { client } from '../lib/backend'
import { useStore } from '../store'
import { Badge, Button, cx } from './ui'

const KIND_TITLE: Record<string, string> = {
  delete: 'Delete files or data',
  send: 'Send a message or email',
  purchase: 'Make a purchase or payment',
  admin: 'Run with administrator rights',
  system: 'Change system settings',
  powershell: 'Run a PowerShell command',
  gui: 'Click a sensitive control',
  other: 'Approve an action',
}

export function respond(id: string, approved: boolean): void {
  client.send({ type: 'approval.respond', id, approved })
}

/** Blocking confirmation for destructive actions. Also answerable by voice ("approve" / "deny"). */
export function ApprovalModal() {
  const approval = useStore((s) => s.approvals[0])
  const more = useStore((s) => s.approvals.length - 1)
  if (!approval) return null
  const high = approval.risk === 'high'
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-6 backdrop-blur-[3px]" role="alertdialog" aria-label="Approval required">
      <div className={cx('rise w-full max-w-[520px] overflow-hidden rounded-2xl border bg-bg shadow-2xl', high ? 'border-danger' : 'border-warn')}>
        <div className="p-5">
          <div className="flex items-center gap-3">
            <span className={cx('flex h-10 w-10 items-center justify-center rounded-full', high ? 'bg-danger/15 text-danger' : 'bg-warn/15 text-warn')}>
              <ShieldAlert size={22} />
            </span>
            <div>
              <h2 className="font-serif text-lg font-semibold">{KIND_TITLE[approval.kind] ?? KIND_TITLE.other}</h2>
              <div className="flex items-center gap-2 text-[12.5px] text-muted">
                Claude is asking for your permission <Badge tone={high ? 'danger' : 'warn'}>{high ? 'High risk' : 'Needs approval'}</Badge>
                {more > 0 && <span>· +{more} more waiting</span>}
              </div>
            </div>
          </div>
          <p className="selectable mt-4 text-[15px] font-medium">{approval.summary}</p>
          {approval.details && <pre className="selectable mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-soft p-3 font-mono text-[12px] leading-relaxed">{approval.details}</pre>}
          <p className="mt-3 flex items-center gap-1.5 text-[12px] text-muted">
            <Mic size={13} /> You can also say “approve” or “deny”. No answer within 4 minutes counts as deny.
          </p>
        </div>
        <div className="flex justify-end gap-2 border-t border-line bg-soft px-5 py-3.5">
          <Button size="lg" onClick={() => respond(approval.id, false)} autoFocus>
            Deny
          </Button>
          <Button size="lg" variant={high ? 'danger' : 'primary'} onClick={() => respond(approval.id, true)}>
            Approve
          </Button>
        </div>
      </div>
    </div>
  )
}
