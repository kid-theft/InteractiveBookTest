import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    allowedHosts: ['web'],
    proxy: { '/api': process.env.API_PROXY_TARGET || 'http://api:8000' },
  },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts'] },
})
