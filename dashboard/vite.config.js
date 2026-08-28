import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API binds loopback and refuses non-loopback peers (ASSUMPTIONS #15), so the dev server
// proxies rather than letting the browser call it cross-origin. `continuum.api` also allows
// 5173/4173 by CORS, so either route works — the proxy is here so a demo does not depend on the
// CORS list being right, and so the frontend has one relative base URL in every environment.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: false,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
