import { describe, expect, it } from 'vitest'
import { contentRect, mapClientPoint } from './geometry'

describe('video content coordinate mapping', () => {
  it('maps a 16:9 video inside a letterboxed container', () => {
    const rect = contentRect(400, 400, 640, 360)
    expect(rect.width).toBe(400)
    expect(rect.height).toBe(225)
    expect(mapClientPoint(220, 250, new DOMRect(100, 50, 400, 400), rect)).toEqual({ x: 0.3, y: 0.5 })
  })

  it('ignores clicks in letterbox bars and on the right/bottom edge', () => {
    const rect = contentRect(400, 400, 640, 360)
    const bounds = new DOMRect(0, 0, 400, 400)
    expect(mapClientPoint(10, 20, bounds, rect)).toBeNull()
    expect(mapClientPoint(400, 250, bounds, rect)).toBeNull()
    expect(mapClientPoint(200, 200, bounds, rect)).toEqual({ x: 0.5, y: 0.5 })
  })
})
