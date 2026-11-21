import { expect, test } from '@playwright/test'

test('SPA loads and shows the three work areas', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Video Interactive Lab' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '上传视频' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '交互播放器' })).toBeVisible()
  await expect(page.getByRole('heading', { name: /识别对象/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /对象问答/ })).toBeVisible()
})

test('health reaches backend through Vite proxy', async ({ request }) => {
  const res = await request.get('/api/v1/health')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  expect(body.status).toBe('ok')
  expect(typeof body.qwen_configured).toBe('boolean')
  expect(typeof body.replicate_configured).toBe('boolean')
})
