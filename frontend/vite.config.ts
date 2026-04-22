import { defineConfig } from 'vite'

export default defineConfig({
  // VITE_GH_PAGES=1 avoids MSYS path-conversion (using a flag, not a path string)
  base: process.env.VITE_GH_PAGES ? '/S.T.A.S.I.S.-Mk3/' : '/',
  build: {
    outDir: 'dist',
  },
  server: {
    // During `npm run dev` proxy all backend calls to the local FastAPI server
    proxy: {
      '/ws':     { target: 'ws://127.0.0.1:8765', ws: true, rewriteWsOrigin: true },
      '/health': 'http://127.0.0.1:8765',
      '/api':    'http://127.0.0.1:8765',
    },
  },
})
