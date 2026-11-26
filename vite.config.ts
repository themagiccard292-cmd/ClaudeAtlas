import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Strict CSP for the packaged app only (the dev server needs inline scripts for HMR).
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data: blob: http://127.0.0.1:*",
  "media-src 'self' blob:",
  "connect-src 'self' ws://127.0.0.1:* http://127.0.0.1:*",
  "object-src 'none'",
  "base-uri 'none'",
].join('; ')

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'inject-csp',
      apply: 'build',
      transformIndexHtml: (html) => html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
    },
  ],
  base: './', // the packaged app loads dist/index.html from disk (file://)
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 1500 },
})
