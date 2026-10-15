import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Override for a second local instance, e.g. SHORTCUT_API=http://127.0.0.1:8770
  const api = loadEnv(mode, '.', 'SHORTCUT_').SHORTCUT_API ?? 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': api,
        '/media': api,
        '/voice-media': api,
      },
    },
  }
})
