/** Stable per-entity colors. Purely for distinguishing objects, not confidence. */
export const ENTITY_PALETTE = ['#2dd4bf', '#fbbf24', '#fb923c', '#a78bfa', '#f472b6', '#60a5fa']

export function entityColor(entityId: string): string {
  let h = 0
  for (let i = 0; i < entityId.length; i++) h = (h * 31 + entityId.charCodeAt(i)) | 0
  return ENTITY_PALETTE[Math.abs(h) % ENTITY_PALETTE.length]
}
