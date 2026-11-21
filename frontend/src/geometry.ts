export type Point = { x: number; y: number }
export type Rect = { left: number; top: number; width: number; height: number }

export function contentRect(containerWidth: number, containerHeight: number, videoWidth: number, videoHeight: number): Rect {
  if (!containerWidth || !containerHeight || !videoWidth || !videoHeight) return { left: 0, top: 0, width: 0, height: 0 }
  const scale = Math.min(containerWidth / videoWidth, containerHeight / videoHeight)
  const width = videoWidth * scale
  const height = videoHeight * scale
  return { left: (containerWidth - width) / 2, top: (containerHeight - height) / 2, width, height }
}

export function mapClientPoint(clientX: number, clientY: number, bounds: DOMRect, rect: Rect): Point | null {
  const x = (clientX - bounds.left - rect.left) / rect.width
  const y = (clientY - bounds.top - rect.top) / rect.height
  if (!rect.width || !rect.height || x < 0 || y < 0 || x >= 1 || y >= 1) return null
  return { x, y }
}
