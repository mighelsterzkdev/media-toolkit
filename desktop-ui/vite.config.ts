import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { sourcemap: false, target: 'es2022' },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test/setup.ts'], restoreMocks: true },
})
