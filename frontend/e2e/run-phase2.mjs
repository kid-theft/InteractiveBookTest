/**
 * Real browser E2E using Playwright's core API (bypasses the @playwright/test
 * runner, which hangs on this machine). Uses the already-installed Chrome.
 *
 * Run: node e2e/run-phase2.mjs
 * Requires the Docker stack + a video that has real SAM masks.
 */
import { chromium } from 'playwright'
import { existsSync, mkdirSync } from 'node:fs'

const BASE = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'
const API = `${BASE}/api/v1`
const chrome = [
  process.env.E2E_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
].filter(Boolean).find(p => existsSync(p))

if (!chrome) throw new Error('No system Chrome found; set E2E_CHROME_PATH')

const log = (...a) => console.log('[e2e]', ...a)
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`)
}

const get = async (path) => (await fetch(`${API}${path}`)).json()
const post = async (path, body) => (await fetch(`${API}${path}`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})).json()

const browser = await chromium.launch({ executablePath: chrome, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
  const errors = []
  let VID_UNDER_TEST = ''
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`))
  page.on('response', r => {
    if (r.status() < 400) return
    // Only fail on errors for the video under test; other library items may
    // legitimately lack playback/thumbnail (phase-1 synthetic sample).
    if (VID_UNDER_TEST && r.url().includes(`/videos/${VID_UNDER_TEST}/`)) errors.push(`${r.status()} ${r.url()}`)
  })

  // 1. Open the SPA.
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 })
  const h1 = await page.locator('h1').first().textContent()
  check('SPA loads', h1?.includes('Video Interactive Lab'), `h1=${h1}`)

  // 2. Health / real API configured.
  const health = await get('/health')
  check('Qwen + Replicate configured', health.qwen_configured && health.replicate_configured,
    `qwen=${health.qwen_configured} replicate=${health.replicate_configured}`)

  // 3. Find the analyzed real video (one that actually has an index).
  const videos = await get('/videos')
  let target = null, index = null
  const candidates = []
  for (const v of videos) {
    try {
      const i = await get(`/videos/${v.id}/index`)
      candidates.push({ v, i })
    } catch { /* skip */ }
  }
  // Prefer a completed index that actually has masks on frame 0 (seed=0
  // objects). Videos whose seeds are all > 0 legitimately have no mask at t=0.
  let picked = null
  for (const c of candidates.filter(c => ['success', 'partial'].includes(c.i.status))) {
    const fm = await get(`/videos/${c.v.id}/index?timestamp_sec=0.1`)
    if (fm.masks?.length) { picked = c; break }
  }
  picked = picked || candidates.find(c => ['success', 'partial'].includes(c.i.status))
    || candidates.find(c => c.i.status === 'running')
  if (picked) { target = picked.v; index = picked.i }
  check('library has analyzed video', !!target, target ? `${target.name} (${target.id})` : 'none')
  if (!target) throw new Error('no analyzed video')
  VID_UNDER_TEST = target.id

  const details = await get(`/videos/${target.id}`)
  check('index generated', ['success', 'partial'].includes(index.status),
    `status=${index.status} frames=${details.frame_count}`)

  // 4. Select video in the library (real UI interaction).
  await page.locator('.library li', { hasText: target.name }).first().click()
  await page.locator('.stage video').waitFor({ timeout: 10000 })
  await page.waitForTimeout(1200)
  check('player + mask canvas render',
    await page.locator('canvas.mask-layer').count() === 1 && await page.locator('.stage video').count() === 1)

  // 5. Real mask PNGs available on frame 0.
  const fm = await get(`/videos/${target.id}/index?timestamp_sec=0.1`)
  const masks = fm.masks || []
  check('real mask rows on frame 0', masks.length > 0,
    masks.map(m => `${m.track_id.slice(0, 8)}/area=${m.area}`).join(', '))
  for (const m of masks.slice(0, 3)) {
    const r = await fetch(`${BASE}${m.mask_url}`)
    const buf = Buffer.from(await r.arrayBuffer())
    check(`mask PNG valid ${m.track_id.slice(0, 8)}`,
      r.ok && r.headers.get('content-type') === 'image/png' && buf.slice(1, 4).toString() === 'PNG',
      `bytes=${buf.length}`)
  }

  // 6. Click on a real mask pixel -> matched/ambiguous (real backend mask hit).
  let hitPoint = null
  if (masks.length) {
    // Fetch the first mask PNG in the browser to find a white pixel.
    const png = await (await fetch(`${BASE}${masks[0].mask_url}`)).arrayBuffer()
    hitPoint = await page.evaluate(async (b64) => {
      const img = new Image()
      img.src = 'data:image/png;base64,' + b64
      await img.decode()
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height
      const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0)
      const d = ctx.getImageData(0, 0, img.width, img.height).data
      for (let y = 0; y < img.height; y += 4) for (let x = 0; x < img.width; x += 4) {
        if (d[(y * img.width + x) * 4] > 0) return { x: (x + 0.5) / img.width, y: (y + 0.5) / img.height }
      }
      return null
    }, Buffer.from(png).toString('base64'))
  }
  if (hitPoint) {
    const res = await post(`/videos/${target.id}/touch`, {
      timestamp_sec: 0.1, x_norm: hitPoint.x, y_norm: hitPoint.y,
    })
    check('click on mask pixel -> hit', ['matched', 'ambiguous'].includes(res.status),
      `status=${res.status} method=${res.match_method} entity=${res.selected_entity?.name || '-'}`)
    // Click the video stage at that normalized point (real UI click).
    const stage = await page.locator('.stage').boundingBox()
    const rect = await page.evaluate(() => {
      const v = document.querySelector('.stage video')
      const r = v.getBoundingClientRect()
      const scale = Math.min(r.width / v.videoWidth, r.height / v.videoHeight)
      const w = v.videoWidth * scale, h = v.videoHeight * scale
      return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h }
    })
    await page.mouse.click(rect.left + hitPoint.x * rect.width, rect.top + hitPoint.y * rect.height)
    await page.waitForTimeout(600)
  } else {
    check('click on mask pixel -> hit', false, 'no white pixel found in mask')
  }

  // 7. Empty background -> no_hit.
  const blank = await post(`/videos/${target.id}/touch`, { timestamp_sec: 0.1, x_norm: 0.01, y_norm: 0.01 })
  check('blank corner -> no_hit', blank.status === 'no_hit', `status=${blank.status}`)

  // 8. QA with real Qwen (only if an entity was hit).
  const entities = await get(`/videos/${target.id}/entities`)
  if (entities.length) {
    const q0 = await post(`/videos/${target.id}/qa`, {
      entity_id: entities[0].id, timestamp_sec: 0.1, question: '它是什么动物？',
    })
    check('real Qwen QA', !!q0.answer && !q0.detail,
      `answer=${String(q0.answer || q0.detail).slice(0, 60)} session=${q0.session_id || '-'}`)
    if (q0.session_id) {
      const q1 = await post(`/videos/${target.id}/qa`, {
        session_id: q0.session_id, entity_id: entities[0].id, timestamp_sec: 0.1, question: '它戴着什么？',
      })
      check('multi-turn QA', !!q1.answer && q1.session_id === q0.session_id,
        `answer=${String(q1.answer || q1.detail).slice(0, 60)}`)
    }
  }

  // 9. Mask overlay actually painted (non-transparent pixels on the mask canvas).
  const painted = await page.evaluate(() => {
    const c = document.querySelector('canvas.mask-layer')
    if (!c) return -1
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    let n = 0
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++
    return n
  })
  check('mask overlay painted pixels', painted > 0, `alpha_pixels=${painted}`)

  mkdirSync('test-results', { recursive: true })
  await page.screenshot({ path: 'test-results/phase2-overlay.png', fullPage: true })
  log('screenshot: test-results/phase2-overlay.png')
  check('no uncaught browser errors', errors.length === 0, errors.slice(0, 3).join(' | '))
} finally {
  await browser.close()
}

const failed = results.filter(r => !r.ok)
console.log(`\n[e2e] ${results.length - failed.length}/${results.length} checks passed`)
if (failed.length) { console.log('[e2e] FAILED:', failed.map(f => f.name).join(', ')); process.exit(1) }
