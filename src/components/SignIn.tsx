import { Check, Copy, ExternalLink, LoaderCircle, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { client } from '../lib/backend'
import { useStore } from '../store'
import { Button, ClaudeMark } from './ui'

function CopyBox({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <div className="flex items-center gap-2 rounded-xl border border-line bg-card py-2 pl-3.5 pr-2">
      <code className="selectable min-w-0 flex-1 truncate font-mono text-[12.5px]">{text}</code>
      <button
        aria-label="Copy"
        className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"
        onClick={() => {
          void navigator.clipboard.writeText(text)
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        }}
      >
        {done ? <Check size={15} /> : <Copy size={15} />}
      </button>
    </div>
  )
}

/** First-launch "Sign in with Claude Pro". Sign-in happens through Anthropic's own flow
 *  (`claude auth login --claudeai`) in the system browser; the app never handles credentials. */
export function SignIn() {
  const auth = useStore((s) => s.auth)
  const login = useStore((s) => s.login)
  const open = (url: string) => void window.api?.openExternal(url)

  if (!auth) return null
  const missing = !auth.cli_found
  const wrongBilling = auth.logged_in && !auth.subscription_ok

  return (
    <div className="drag flex h-full items-center justify-center overflow-y-auto bg-bg p-8">
      <div className="no-drag rise w-full max-w-[460px] text-center">
        <ClaudeMark size={52} className="mx-auto" />
        <h1 className="mt-6 font-serif text-[34px] font-medium leading-tight tracking-tight">{missing ? 'Install Claude Code' : 'Sign in with Claude Pro'}</h1>
        <p className="mx-auto mt-3 max-w-[400px] text-[14.5px] leading-relaxed text-muted">
          {missing
            ? 'Claude OS Agent runs on your Claude subscription through the official Claude Code app. Install it once, then come back here.'
            : 'Use your existing Claude subscription — flat rate, no API key. You’ll finish signing in on claude.ai in your browser.'}
        </p>

        {missing && (
          <div className="mt-6 space-y-3 text-left">
            <div className="text-[13px] font-medium">1. Open PowerShell and run the official installer:</div>
            <CopyBox text={auth.install_command} />
            <div className="text-[13px] font-medium">2. Then check again:</div>
            <div className="flex gap-2">
              <Button variant="primary" className="flex-1" onClick={() => client.send({ type: 'auth.status', force: true })}>
                <RefreshCw size={15} /> I’ve installed it — check again
              </Button>
              <Button onClick={() => open(auth.install_guide_url)}>
                <ExternalLink size={15} /> Guide
              </Button>
            </div>
          </div>
        )}

        {!missing && (
          <div className="mt-7">
            {wrongBilling && <div className="mb-4 rounded-xl bg-warn/15 px-4 py-3 text-left text-[13px] leading-relaxed text-warn">{auth.message}</div>}
            {login.active ? (
              <div className="rounded-2xl border border-line bg-card p-5">
                <div className="flex items-center justify-center gap-2.5 text-[14.5px] font-medium">
                  <LoaderCircle size={18} className="spin text-accent" /> Finish signing in in your browser…
                </div>
                <p className="mt-2 text-[13px] text-muted">This window will switch to the agent automatically as soon as you’re signed in.</p>
                {login.url && (
                  <div className="mt-4 space-y-2 text-left">
                    <div className="text-[12.5px] text-muted">Browser didn’t open? Use this link:</div>
                    <CopyBox text={login.url} />
                    <Button size="sm" onClick={() => open(login.url)}>
                      <ExternalLink size={14} /> Open sign-in page
                    </Button>
                  </div>
                )}
                <Button variant="ghost" size="sm" className="mt-4" onClick={() => client.send({ type: 'auth.cancel' })}>
                  Cancel
                </Button>
              </div>
            ) : (
              <>
                <Button variant="primary" size="lg" className="w-full" onClick={() => client.send({ type: 'auth.login' })}>
                  Sign in with Claude Pro
                </Button>
                {login.error && <p className="mt-3 text-[13px] text-danger">{login.error}</p>}
              </>
            )}
          </div>
        )}

        <p className="mx-auto mt-8 max-w-[380px] text-[12px] leading-relaxed text-muted">
          Sign-in uses Anthropic’s official flow. This app never sees your password, session cookies or tokens, and never uses an API key.
        </p>
      </div>
    </div>
  )
}
