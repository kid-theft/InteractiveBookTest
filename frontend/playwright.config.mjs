import { defineConfig } from '@playwright/test'

// Smoke test only: verifies the SPA boots and reaches the API through Vite's
// proxy. Real Qwen/SAM browser flows require configured API keys.
export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    headless: true,
    // Use an installed Chrome rather than downloading Playwright's pinned
    // Chromium build (offline-friendly). Override with E2E_CHANNEL=chromium
    // when the matching browser revision is available.
    channel: process.env.E2E_CHANNEL || 'chrome',
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60000,
  },
})
