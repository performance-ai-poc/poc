import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    proxy: {
      '/api/analytics': {
        target: 'http://127.0.0.1:8002',
        ws: true, // proxy the real-time dashboard WebSocket in `vite dev` too
        rewrite: (path) => path.replace(/^\/api\/analytics/, ''),
      },
      '/api': {
        target: 'http://127.0.0.1:8001',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
