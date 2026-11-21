import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'

// Prefer an already-installed Chrome so no large browser download is needed.
// Override with E2E_CHROME_PATH when Chrome lives elsewhere.
const candidates = [
  process.env.E2E_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean)
const chromePath = candidates.find(p => existsSync(p))

export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  expect: { timeout: 10000 },
  workers: 1,
  reporter: [['line']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    headless: true,
    ...(chromePath ? { launchOptions: { executablePath: chromePath, args: ['--no-sandbox', '--disable-dev-shm-usage'] } } : {}),
    screenshot: 'only-on-failure',
  },
  // The compose stack already serves the SPA; don't start a second dev server.
  webServer: undefined,
})
