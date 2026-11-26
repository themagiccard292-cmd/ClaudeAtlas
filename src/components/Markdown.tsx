import { Check, Copy } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null)
  const [done, setDone] = useState(false)
  const copy = () => {
    void navigator.clipboard.writeText(ref.current?.innerText ?? '').then(() => {
      setDone(true)
      setTimeout(() => setDone(false), 1500)
    })
  }
  return (
    <div className="group relative">
      <pre ref={ref}>{children}</pre>
      <button onClick={copy} aria-label="Copy code" className="absolute right-2 top-2 rounded-md border border-line bg-card p-1.5 text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
        {done ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </div>
  )
}

export function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={`md selectable ${streaming ? 'caret' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault()
                if (href && /^https:\/\//i.test(href)) void window.api?.openExternal(href)
              }}
            >
              {children}
            </a>
          ),
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
