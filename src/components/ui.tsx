import { X } from 'lucide-react'
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from 'react'

export const cx = (...c: Array<string | false | null | undefined>): string => c.filter(Boolean).join(' ')

/** Original 12-ray starburst mark (accent coloured). */
export function ClaudeMark({ size = 24, className = '' }: { size?: number; className?: string }) {
  const rays = Array.from({ length: 12 }, (_, i) => i)
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className={cx('text-accent shrink-0', className)} aria-hidden>
      {rays.map((i) => {
        const long = i % 2 === 0
        return <rect key={i} x={24 - (long ? 2.6 : 2.1)} y={long ? 3.5 : 9} width={long ? 5.2 : 4.2} height={long ? 17 : 11} rx={2.2} fill="currentColor" transform={`rotate(${i * 30} 24 24)`} />
      })}
      <circle cx="24" cy="24" r="3.2" fill="currentColor" />
    </svg>
  )
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'sm' | 'md' | 'lg' }
export function Button({ variant = 'secondary', size = 'md', className, ...p }: BtnProps) {
  const base = 'inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none no-drag'
  const sizes = { sm: 'h-8 px-3 text-[13px]', md: 'h-9 px-4 text-sm', lg: 'h-11 px-6 text-[15px]' }
  const variants = {
    primary: 'bg-accent text-accent-fg hover:bg-accent-hover',
    secondary: 'border border-line bg-card hover:bg-hover',
    ghost: 'hover:bg-hover',
    danger: 'bg-danger text-white hover:brightness-110',
  }
  return <button className={cx(base, sizes[size], variants[variant], className)} {...p} />
}

export function IconButton({ className, children, label, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button aria-label={label} title={label} className={cx('no-drag inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40', className)} {...p}>
      {children}
    </button>
  )
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} className={cx('no-drag relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors disabled:opacity-50', checked ? 'bg-accent' : 'bg-line')}>
      <span className={cx('absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-all', checked ? 'left-[19px]' : 'left-[3px]')} />
    </button>
  )
}

export function Modal({ title, onClose, children, width = 'max-w-[780px]' }: { title: string; onClose?: () => void; children: ReactNode; width?: string }) {
  useEffect(() => {
    if (!onClose) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6 backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div role="dialog" aria-label={title} className={cx('rise flex max-h-full w-full flex-col overflow-hidden rounded-2xl border border-line bg-bg shadow-2xl', width)}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="font-serif text-lg font-semibold">{title}</h2>
          {onClose && (
            <IconButton label="Close" onClick={onClose}>
              <X size={18} />
            </IconButton>
          )}
        </div>
        {children}
      </div>
    </div>
  )
}

export function Badge({ children, tone = 'neutral', className }: { children: ReactNode; tone?: 'neutral' | 'accent' | 'warn' | 'danger' | 'ok'; className?: string }) {
  const tones = {
    neutral: 'bg-hover text-muted',
    accent: 'bg-accent/15 text-accent',
    warn: 'bg-warn/15 text-warn',
    danger: 'bg-danger/15 text-danger',
    ok: 'bg-ok/15 text-ok',
  }
  return <span className={cx('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium leading-4', tones[tone], className)}>{children}</span>
}
