/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    // Notes is 5173/8000, GAM 5174/8001, GVC 5175/8002; all four run side by side.
    port: 5176,
    proxy: {
      // This target must track the port the dev backend is started on. Pointing it at a
      // sibling's port does not fail cleanly: that backend shares JWT_SECRET_KEY, so it
      // verifies the token and answers with another app's data.
      '/api': { target: 'http://localhost:8003', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
  },
})
