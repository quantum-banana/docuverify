import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const runtimeEnvironment = (
  globalThis as typeof globalThis & {
    process?: { env?: Record<string, string | undefined> }
  }
).process?.env ?? {}

const apiProxyTarget = runtimeEnvironment.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/tests/setup.ts',
    css: true,
  },
})
