import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    outDir: 'dist',
  },
  server: {
    // During `npm run dev` proxy API calls to the FastAPI backend
    proxy: {
      '/ws':     { target: 'ws://127.0.0.1:8765', ws: true, rewriteWsOrigin: true },
      '/health': 'http://127.0.0.1:8765',
      '/logo.png': 'http://127.0.0.1:8765',
    },
  },
})
