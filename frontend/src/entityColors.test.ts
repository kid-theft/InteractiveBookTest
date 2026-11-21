import { describe, expect, it } from 'vitest'
import { ENTITY_PALETTE, entityColor } from './entityColors'

describe('stable entity colors', () => {
  it('returns a palette color deterministically for the same id', () => {
    const a = entityColor('9a7d7f96e01a4364b651a58cc964b639')
    expect(ENTITY_PALETTE).toContain(a)
    expect(entityColor('9a7d7f96e01a4364b651a58cc964b639')).toBe(a)
    for (let i = 0; i < 50; i++) {
      expect(entityColor(`entity-${i}`)).toBe(entityColor(`entity-${i}`))
    }
  })

  it('uses more than one color across different entities', () => {
    const colors = new Set(Array.from({ length: 30 }, (_, i) => entityColor(`e${i}`)))
    expect(colors.size).toBeGreaterThan(1)
  })
})
