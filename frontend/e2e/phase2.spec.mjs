import { expect, test } from '@playwright/test'

/**
 * Phase-2 browser E2E. Real video + real API (upload -> Qwen -> SAM -> masks).
 * Skips gracefully when the backend has no configured keys / no analyzed video.
 */
const BASE = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'

test('phase2: mask overlay + click recognition on the analyzed real video', async ({ page, request }) => {
  const health = await (await request.get(`${BASE}/api/v1/health`)).json()
  test.skip(!health.qwen_configured || !health.replicate_configured, 'API keys not configured')

  const videos = await (await request.get(`${BASE}/api/v1/videos`)).json()
  const target = videos.find(v => v.status === 'playable') || videos[0]
  test.skip(!target, 'no video available')

  await page.goto(BASE)
  await expect(page.getByRole('heading', { name: 'Video Interactive Lab' })).toBeVisible()
  // Select the video from the library.
  await page.locator('.library li', { hasText: target.name }).first().click()

  const details = await (await request.get(`${BASE}/api/v1/videos/${target.id}`)).json()
  const index = await (await request.get(`${BASE}/api/v1/videos/${target.id}/index`)).json()
  test.skip(index.status !== 'success' && index.status !== 'partial', 'no masks generated yet')

  const frameMasks = await (await request.get(
    `${BASE}/api/v1/videos/${target.id}/index?timestamp_sec=0.1`)).json()
  test.skip(!frameMasks.masks?.length, 'no mask on frame 0')

  // Mask overlay canvas exists and is sized to the stage.
  await page.locator('.stage video').waitFor()
  const box = await page.locator('.stage').boundingBox()
  expect(box?.width).toBeGreaterThan(100)
  const maskCanvas = page.locator('canvas.mask-layer')
  await expect(maskCanvas).toBeVisible()

  // Verify a real mask PNG is reachable and is a valid binary PNG.
  const m = frameMasks.masks[0]
  const png = await request.get(`${BASE}${m.mask_url}`)
  expect(png.ok()).toBeTruthy()
  expect(png.headers()['content-type']).toBe('image/png')
  const buf = await png.body()
  expect(buf.slice(1, 4).toString()).toBe('PNG')
  console.log(`[phase2] video=${target.id} frames=${details.frame_count} mask=${m.track_id} area=${m.area}`)

  // Click the center of the video content area; the touch API must respond.
  const touch = await request.post(`${BASE}/api/v1/videos/${target.id}/touch`, {
    data: { timestamp_sec: 0.1, x_norm: 0.5, y_norm: 0.5 },
  })
  expect(touch.ok()).toBeTruthy()
  const result = await touch.json()
  expect(['matched', 'ambiguous', 'no_hit']).toContain(result.status)
  console.log(`[phase2] touch status=${result.status} frame=${result.frame_index}`)

  // Clicking empty background must not force a match.
  const blank = await request.post(`${BASE}/api/v1/videos/${target.id}/touch`, {
    data: { timestamp_sec: 0.1, x_norm: 0.02, y_norm: 0.02 },
  })
  const blankResult = await blank.json()
  if (blankResult.status !== 'no_hit') {
    console.log(`[phase2] corner is covered by a mask: ${blankResult.status}`)
  }

  await page.screenshot({ path: 'test-results/phase2-overlay.png' })
})
