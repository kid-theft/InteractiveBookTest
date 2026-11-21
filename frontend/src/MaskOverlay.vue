<script setup lang="ts">
/**
 * MaskOverlay — renders real SAM 2 binary PNG masks as transparent colored
 * layers on top of the video, synchronized with the actually displayed frame.
 *
 * Rendering: a mask PNG's 0 pixels become fully transparent, >0 pixels become
 * a semi-transparent entity color. The black PNG background never covers video.
 * Loading: a bounded Map cache keyed by `${entityId}:${frame}`; only the
 * current frame is awaited, a few neighbours are prefetched in the background.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type Entity, type FrameMask, type IndexStatus } from './api'
import type { Rect } from './geometry'
import { entityColor } from './entityColors'

type CachedMask = { colored: HTMLCanvasElement }
type FrameCallbackVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number
  cancelVideoFrameCallback?: (handle: number) => void
}

const props = defineProps<{
  videoId: string
  details: { width: number; height: number; frame_count: number; canonical_fps: number; duration: number }
  entities: Entity[]
  videoEl: FrameCallbackVideo | null
  canvasEl: HTMLCanvasElement | null
  rect: Rect
  selectedEntityId: string | null
  enabled: boolean
  refreshToken: number
}>()

const emit = defineEmits<{
  (e: 'frame', frame: number, mediaTime: number): void
  (e: 'stats', stats: { frame: number; loaded: number; cacheSize: number; loadMs: number | null }): void
}>()

const MAX_CACHE = 120           // bounded; never load the whole video at once
const PREFETCH = 2              // neighbours in each direction

// Selected objects are drawn white for a clearly stronger highlight.
function colorFor(entityId: string, selected: boolean): string {
  return selected ? '#ffffff' : entityColor(entityId)
}

const cache = new Map<string, CachedMask>()
const inflight = new Map<string, Promise<CachedMask | null>>()
const frameMasks = new Map<number, FrameMask[]>()
const frameIndexKnown = new Map<number, boolean>()
let frameHandle: number | undefined
let lastFrame = -1
let lastMediaTime = -1
let resizeObserver: ResizeObserver | undefined
let fullscreenHandler: (() => void) | undefined
let destroyed = false
const maskLoadMs = ref<number | null>(null)
const loadedCount = ref(0)

function key(entityId: string, frame: number, variant: string) { return `${entityId}:${frame}:${variant}` }
function variantFor(entityId: string) { return props.selectedEntityId === entityId ? 'selected' : 'normal' }

function evictIfNeeded() {
  while (cache.size > MAX_CACHE) {
    const oldest = cache.keys().next().value as string | undefined
    if (oldest === undefined) break
    cache.delete(oldest)   // canvas is GC'd; bounded memory, no whole-video loading
  }
}

/** Convert a binary PNG into a transparent colored canvas (no black background). */
async function loadColored(url: string, color: string, alpha: number): Promise<CachedMask | null> {
  const t0 = performance.now()
  const res = await fetch(url, { cache: 'force-cache' })
  if (!res.ok) return null
  const blob = await res.blob()
  let bitmap: ImageBitmap | HTMLImageElement
  if (typeof createImageBitmap === 'function') {
    bitmap = await createImageBitmap(blob)
  } else {
    const img = new Image()
    img.src = URL.createObjectURL(blob)
    await img.decode()
    bitmap = img
  }
  const w = 'width' in bitmap ? bitmap.width : 0
  const h = 'height' in bitmap ? bitmap.height : 0
  const tmp = document.createElement('canvas')
  tmp.width = w; tmp.height = h
  const tctx = tmp.getContext('2d', { willReadFrequently: true })
  if (!tctx) return null
  tctx.drawImage(bitmap as CanvasImageSource, 0, 0)
  const data = tctx.getImageData(0, 0, w, h)
  const px = data.data
  const rgb = hexToRgb(color)
  // Recolor only non-zero mask pixels; everything else stays fully transparent.
  for (let i = 0; i < px.length; i += 4) {
    const hit = px[i] > 0 || px[i + 1] > 0 || px[i + 2] > 0
    if (hit) {
      px[i] = rgb.r; px[i + 1] = rgb.g; px[i + 2] = rgb.b
      px[i + 3] = Math.round(255 * alpha)
    } else {
      px[i + 3] = 0
    }
  }
  tctx.putImageData(data, 0, 0)
  maskLoadMs.value = Math.round(performance.now() - t0)
  loadedCount.value++
  if ('close' in bitmap) (bitmap as ImageBitmap).close()
  if (bitmap instanceof HTMLImageElement && bitmap.src.startsWith('blob:')) URL.revokeObjectURL(bitmap.src)
  return { colored: tmp }
}

function hexToRgb(hex: string) {
  const n = parseInt(hex.slice(1), 16)
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}

async function getMask(entityId: string, frame: number, variant = 'normal'): Promise<CachedMask | null> {
  const k = key(entityId, frame, variant)
  const cached = cache.get(k)
  if (cached) { cache.delete(k); cache.set(k, cached); return cached }  // LRU touch
  const pending = inflight.get(k)
  if (pending) return pending
  const selected = variant === 'selected'
  const url = api.maskUrl(props.videoId, entityId, frame)
  const p = loadColored(url, colorFor(entityId, selected), selected ? 0.6 : 0.34).then(item => {
    if (item && !destroyed) { cache.set(k, item); evictIfNeeded() }
    return item
  }).finally(() => inflight.delete(k))
  inflight.set(k, p)
  return p
}

async function fetchFrameMasks(frame: number): Promise<FrameMask[]> {
  if (frameMasks.has(frame)) return frameMasks.get(frame)!
  // pts of a CFR video: frame / fps. Ask the index endpoint for this exact time.
  const t = Math.min(frame / props.details.canonical_fps + 1e-4, Math.max(0, props.details.duration - 1e-3))
  try {
    const idx: IndexStatus = await api.index(props.videoId, t)
    const rows: FrameMask[] = (idx.masks ?? []).filter(m => !!m.mask_url)
    frameMasks.set(frame, rows)
    frameIndexKnown.set(frame, true)
    return rows
  } catch {
    return []
  }
}

function draw() {
  const canvas = props.canvasEl
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  // If the canvas has not been sized to its CSS box yet, drawing would scale the
  // mask against the default 300x150 buffer and look shifted. Skip until ready.
  const cssBox = canvas.getBoundingClientRect()
  if (!cssBox.width || !cssBox.height) return
  // Self-heal the backing buffer: it must match the CSS box at the current DPR,
  // otherwise the mask is drawn into a stale 300x150 buffer and appears shifted.
  const wantW = Math.round(cssBox.width * (window.devicePixelRatio || 1))
  const wantH = Math.round(cssBox.height * (window.devicePixelRatio || 1))
  if (canvas.width !== wantW || canvas.height !== wantH) {
    canvas.width = wantW
    canvas.height = wantH
  }
  // Map the drawing surface to the canvas' actual CSS box (not the stage box):
  // the canvas element can differ from the stage by a sub-pixel, and using the
  // wrong reference shifts/scales the whole mask layer.
  const sx = canvas.width / cssBox.width
  const sy = canvas.height / cssBox.height
  ctx.setTransform(sx, 0, 0, sy, 0, 0)
  ctx.clearRect(0, 0, cssBox.width, cssBox.height)
  const r = props.rect
  if (!r.width || !props.enabled) return
  const rows = frameMasks.get(lastFrame) ?? []
  for (const m of rows) {
    const selected = props.selectedEntityId === m.track_id
    const item = cache.get(key(m.track_id, lastFrame, variantFor(m.track_id)))
    if (!item) continue
    ctx.save()
    ctx.globalAlpha = 1
    ctx.drawImage(item.colored, r.left, r.top, r.width, r.height)
    if (selected) {
      if (m.bbox && m.bbox.length === 4) {
        const [x1, y1, x2, y2] = m.bbox
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 2
        ctx.setLineDash([6, 4])
        ctx.strokeRect(r.left + x1 / props.details.width * r.width,
                       r.top + y1 / props.details.height * r.height,
                       (x2 - x1) / props.details.width * r.width,
                       (y2 - y1) / props.details.height * r.height)
      }
      ctx.globalAlpha = 0.35
      ctx.drawImage(item.colored, r.left, r.top, r.width, r.height)
    }
    ctx.restore()
  }
}

async function renderFrame(frame: number, mediaTime: number) {
  if (destroyed || frame < 0 || frame >= props.details.frame_count) return
  const rows = await fetchFrameMasks(frame)
  if (destroyed || frame !== lastFrame) return
  // Current frame first (await), then prefetch neighbours in the background.
  await Promise.all(rows.map(m => getMask(m.track_id, frame, variantFor(m.track_id))))
  if (destroyed || frame !== lastFrame) return
  draw()
  emit('stats', { frame, loaded: loadedCount.value, cacheSize: cache.size, loadMs: maskLoadMs.value })
  prefetch(frame)
}

function prefetch(frame: number) {
  for (let d = 1; d <= PREFETCH; d++) {
    for (const f of [frame - d, frame + d]) {
      if (f < 0 || f >= props.details.frame_count || frameIndexKnown.get(f)) continue
      frameIndexKnown.set(f, true)   // avoid duplicate prefetch requests
      void (async () => {
        const rows = await fetchFrameMasks(f)
        for (const m of rows) {
          void getMask(m.track_id, f, 'normal')
        }
      })()
    }
  }
}

function frameFromTime(t: number): number {
  // Must match backend displayed_frame(): the frame whose PTS <= t (bisect_right-1),
  // i.e. floor for a CFR video. round() would shift masks by one frame at boundaries.
  const f = Math.floor(t * props.details.canonical_fps + 1e-6)
  return Math.max(0, Math.min(props.details.frame_count - 1, f))
}

function onFrame(_now: number, meta: { mediaTime: number }) {
  const video = props.videoEl
  if (!video || destroyed) return
  lastMediaTime = meta.mediaTime
  const frame = frameFromTime(meta.mediaTime)
  if (frame !== lastFrame) {
    lastFrame = frame
    emit('frame', frame, meta.mediaTime)
    void renderFrame(frame, meta.mediaTime)
  }
  frameHandle = video.requestVideoFrameCallback?.(onFrame)
}

/** Re-render on pause / seek / ratechange / ended, when rvfc does not fire. */
function syncFromVideo() {
  const video = props.videoEl
  if (!video || destroyed) return
  const t = video.currentTime
  lastMediaTime = t
  const frame = frameFromTime(t)
  if (frame !== lastFrame) {
    lastFrame = frame
    emit('frame', frame, t)
    void renderFrame(frame, t)
  } else {
    draw()
  }
}

function clearCache() {
  cache.clear(); inflight.clear(); frameMasks.clear(); frameIndexKnown.clear()
  lastFrame = -1
}

function attach() {
  const video = props.videoEl
  if (!video) return
  if (frameHandle !== undefined) video.cancelVideoFrameCallback?.(frameHandle)
  if (video.requestVideoFrameCallback) {
    frameHandle = video.requestVideoFrameCallback(onFrame)
  }
  video.addEventListener('seeked', syncFromVideo)
  video.addEventListener('pause', syncFromVideo)
  video.addEventListener('play', syncFromVideo)
  video.addEventListener('ratechange', syncFromVideo)
  video.addEventListener('ended', syncFromVideo)
  video.addEventListener('timeupdate', onTimeUpdate)
}

function onTimeUpdate() {
  // Fallback for browsers without requestVideoFrameCallback.
  if (!props.videoEl?.requestVideoFrameCallback) syncFromVideo()
}

function detach() {
  const video = props.videoEl
  if (video) {
    if (frameHandle !== undefined) video.cancelVideoFrameCallback?.(frameHandle)
    video.removeEventListener('seeked', syncFromVideo)
    video.removeEventListener('pause', syncFromVideo)
    video.removeEventListener('play', syncFromVideo)
    video.removeEventListener('ratechange', syncFromVideo)
    video.removeEventListener('ended', syncFromVideo)
    video.removeEventListener('timeupdate', onTimeUpdate)
  }
  frameHandle = undefined
}

watch(() => [props.videoId, props.refreshToken, props.enabled], () => {
  clearCache(); detach(); attach(); syncFromVideo()
})
watch(() => props.selectedEntityId, () => {
  // Variant is part of the cache key, so the newly selected entity is simply
  // loaded with its highlight color; no wholesale cache invalidation needed.
  if (lastFrame >= 0) void renderFrame(lastFrame, lastMediaTime)
  else draw()
})
watch(() => props.rect, () => draw(), { deep: true })

onMounted(() => {
  attach()
  resizeObserver = new ResizeObserver(() => draw())
  if (props.canvasEl) resizeObserver.observe(props.canvasEl)
  // Draw once layout is stable (the canvas may still be at its default size).
  requestAnimationFrame(() => draw())
  setTimeout(() => draw(), 120)
  fullscreenHandler = () => setTimeout(() => draw(), 60)
  document.addEventListener('fullscreenchange', fullscreenHandler)
  syncFromVideo()
})

onBeforeUnmount(() => {
  destroyed = true
  detach()
  resizeObserver?.disconnect()
  if (fullscreenHandler) document.removeEventListener('fullscreenchange', fullscreenHandler)
  clearCache()
})

defineExpose({ redraw: draw, currentFrame: () => lastFrame })
</script>

<template>
  <!-- Logic-only component: draws into the canvas supplied by the parent. -->
  <span data-mask-overlay hidden />
</template>
