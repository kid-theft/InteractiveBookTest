<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  api, errorMessage, taskLabel,
  type Entity, type IndexStatus, type Task, type TouchResult, type VideoDetails,
} from './api'
import { contentRect, mapClientPoint, type Rect } from './geometry'
import MaskOverlay from './MaskOverlay.vue'
import { entityColor } from './entityColors'

type ChatMessage = { role: 'user' | 'assistant'; text: string; evidence?: number[] }
type LibraryItem = { id: string; name: string; status: string }
type FrameCallbackVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number
  cancelVideoFrameCallback?: (handle: number) => void
}

const library = ref<LibraryItem[]>([])
const videoId = ref('')
const details = ref<VideoDetails | null>(null)
const entities = ref<Entity[]>([])
const indexStatus = ref<IndexStatus | null>(null)
const health = ref<{ qwen_configured: boolean; replicate_configured: boolean } | null>(null)

const uploadBusy = ref(false)
const busy = ref(false)
const notice = ref('')
const error = ref('')
const task = ref<Task | null>(null)

const fileInput = ref<HTMLInputElement | null>(null)
const videoEl = ref<FrameCallbackVideo | null>(null)
const stageEl = ref<HTMLDivElement | null>(null)
const overlay = ref<HTMLCanvasElement | null>(null)
const maskCanvas = ref<HTMLCanvasElement | null>(null)
const maskOverlayRef = ref<InstanceType<typeof MaskOverlay> | null>(null)

const mediaTime = ref(0)
const currentFrame = ref(0)
const overlayStats = ref<{ frame: number; loaded: number; cacheSize: number; loadMs: number | null } | null>(null)
const displayRect = ref<Rect>({ left: 0, top: 0, width: 0, height: 0 })
const showMasks = ref(true)
const maskVersion = ref(0)
const touchResult = ref<TouchResult | null>(null)
const selected = ref<Entity | null>(null)
const marker = ref<{ x: number; y: number } | null>(null)

const question = ref('')
const sessionId = ref('')
const chat = ref<ChatMessage[]>([])
const chatBusy = ref(false)

let pollTimer: number | undefined
let frameHandle: number | undefined
let resizeObserver: ResizeObserver | undefined

const canAnalyze = computed(() =>
  !!details.value && details.value.status === 'playable' && !busy.value &&
  !!health.value?.qwen_configured && !!health.value?.replicate_configured)

const stagePercent = computed(() => task.value?.progress ?? 0)
const stageText = computed(() => task.value ? taskLabel(task.value.stage) : '')
const configuredBadge = computed(() => {
  if (!health.value) return '检查服务中…'
  const q = health.value.qwen_configured ? 'Qwen ✓' : 'Qwen 未配置'
  const r = health.value.replicate_configured ? 'Replicate ✓' : 'Replicate 未配置'
  return `${q} · ${r}`
})

function setError(err: unknown) {
  error.value = errorMessage(err)
}

function stopPolling() {
  if (pollTimer !== undefined) { clearInterval(pollTimer); pollTimer = undefined }
}

async function refreshHealth() {
  try { health.value = (await api.health()).data } catch (err) { setError(err) }
}

async function refreshLibrary() {
  try { library.value = await api.library() } catch (err) { setError(err) }
}

async function loadVideo(id: string, autoAnalyze = false) {
  stopPolling()
  videoId.value = id
  details.value = null
  entities.value = []
  indexStatus.value = null
  touchResult.value = null
  selected.value = null
  chat.value = []
  sessionId.value = ''
  task.value = null
  error.value = ''
  try {
    details.value = await api.details(id)
    await Promise.all([refreshEntities(), refreshIndex()])
    await nextTick()
    observeStage()
    setupFrameCallback()
    // Layout can settle after stats/notice lines appear; re-measure shortly after.
    requestAnimationFrame(() => measure())
    setTimeout(() => measure(), 250)
    if (autoAnalyze && canAnalyze.value) await startAnalysis()
  } catch (err) { setError(err) }
}

async function refreshEntities() {
  if (!videoId.value) return
  try { entities.value = await api.entities(videoId.value) } catch (err) { setError(err) }
}

async function refreshIndex() {
  if (!videoId.value) return
  try { indexStatus.value = await api.index(videoId.value) } catch (err) { setError(err) }
}

async function onUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    const created = await api.upload(file)
    await refreshLibrary()
    if (created.cached || !created.task_id) {
      notice.value = created.message || '命中已完成索引，跳过全部 AI 调用。'
      await loadVideo(created.video_id, false)
      return
    }
    notice.value = '上传成功，正在标准化视频…'
    await waitForTask(created.task_id, '预处理')
    await loadVideo(created.video_id, true)
    notice.value = '视频已就绪，可以开始分析。'
  } catch (err) {
    setError(err)
    notice.value = ''
  } finally {
    uploadBusy.value = false
    input.value = ''
  }
}

async function waitForTask(taskId: string, label: string): Promise<Task> {
  busy.value = true
  try {
    for (;;) {
      const t = await api.task(taskId)
      task.value = t
      notice.value = `${label}：${taskLabel(t.stage)} ${t.progress}%${t.error ? ' · ' + t.error : ''}`
      if (['completed', 'failed', 'partial'].includes(t.stage)) return t
      await new Promise(resolve => setTimeout(resolve, 900))
    }
  } finally { busy.value = false }
}

async function startAnalysis() {
  if (!videoId.value) return
  error.value = ''
  try {
    const created = await api.analyze(videoId.value)
    notice.value = '已启动 Qwen 识别 + SAM 2 分割…'
    const finished = await waitForTask(created.task_id, '分析')
    await Promise.all([refreshEntities(), refreshIndex(), api.details(videoId.value).then(d => details.value = d)])
    maskVersion.value++
    notice.value = finished.stage === 'completed'
      ? '分析完成：播放视频并点击画面中的对象。'
      : `分析${taskLabel(finished.stage)}，请查看对象分割详情。`
  } catch (err) { setError(err) }
}

async function resegment(entity: Entity) {
  if (!videoId.value || !details.value) return
  try {
    const frame = indexStatus.value?.frame_index ?? entity.seed_frame ?? 0
    const created = await api.segment(videoId.value, entity.id, {
      entity_id: entity.id,
      frame_index: frame,
      x: entity.seed_point[0],
      y: entity.seed_point[1],
    })
    await waitForTask(created.task_id, '重新分割')
    await refreshIndex()
    maskVersion.value++
  } catch (err) { setError(err) }
}

function setupFrameCallback() {
  const video = videoEl.value
  if (!video || !video.requestVideoFrameCallback) return
  if (frameHandle !== undefined) video.cancelVideoFrameCallback?.(frameHandle)
  const cb = (_now: number, meta: { mediaTime: number }) => {
    mediaTime.value = meta.mediaTime
    frameHandle = video.requestVideoFrameCallback?.(cb)
  }
  frameHandle = video.requestVideoFrameCallback(cb)
}

let measureAttempts = 0

function measure() {
  const stage = stageEl.value
  const video = videoEl.value
  if (!stage || !video || !details.value) return
  const bounds = stage.getBoundingClientRect()
  const dpr = window.devicePixelRatio || 1
  let sized = 0
  for (const canvas of [overlay.value, maskCanvas.value]) {
    if (!canvas) continue
    canvas.style.width = ''
    canvas.style.height = ''
    const cb = canvas.getBoundingClientRect()
    if (!cb.width || !cb.height) continue   // not laid out yet; keep default and retry
    canvas.width = Math.round(cb.width * dpr)
    canvas.height = Math.round(cb.height * dpr)
    sized++
  }
  // Content rect must be expressed in the same CSS box the canvases occupy.
  const ref = maskCanvas.value?.getBoundingClientRect() ?? bounds
  displayRect.value = contentRect(ref.width, ref.height, details.value.width, details.value.height)
  drawOverlay()
  maskOverlayRef.value?.redraw()
  // If a canvas was not laid out yet, retry on the next frames (bounded).
  if ((sized < 2 || !ref.width) && measureAttempts < 12) {
    measureAttempts++
    requestAnimationFrame(measure)
  }
}

function observeStage() {
  // stageEl only exists after a video is selected (v-if), so it cannot be
  // observed in onMounted; (re)bind whenever the stage element appears.
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => measure())
  if (stageEl.value) resizeObserver.observe(stageEl.value)
  measure()
}

function onOverlayFrame(frame: number, t: number) {
  currentFrame.value = frame
  mediaTime.value = t
}

function onOverlayStats(s: { frame: number; loaded: number; cacheSize: number; loadMs: number | null }) {
  overlayStats.value = s
}

function drawOverlay() {
  const canvas = overlay.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const box = canvas.getBoundingClientRect()
  ctx.setTransform(canvas.width / (box.width || 1), 0, 0, canvas.height / (box.height || 1), 0, 0)
  const width = box.width
  const height = box.height
  ctx.clearRect(0, 0, width, height)
  const rect = displayRect.value
  if (!rect.width) return
  // Dim the letterbox area so the actual video content is unambiguous.
  ctx.fillStyle = 'rgba(12, 14, 18, 0.55)'
  ctx.fillRect(0, 0, width, rect.top)
  ctx.fillRect(0, rect.top + rect.height, width, height - rect.top - rect.height)
  ctx.fillRect(0, rect.top, rect.left, rect.height)
  ctx.fillRect(rect.left + rect.width, rect.top, width - rect.left - rect.width, rect.height)
  if (showMasks.value) {
    ctx.strokeStyle = 'rgba(255,255,255,0.18)'
    ctx.lineWidth = 1
    ctx.strokeRect(rect.left + 0.5, rect.top + 0.5, rect.width - 1, rect.height - 1)
  }
  if (marker.value) {
    const x = rect.left + marker.value.x * rect.width
    const y = rect.top + marker.value.y * rect.height
    ctx.strokeStyle = touchResult.value?.status === 'no_hit' ? '#f87171' : '#fbbf24'
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(x - 14, y); ctx.lineTo(x + 14, y)
    ctx.moveTo(x, y - 14); ctx.lineTo(x, y + 14); ctx.stroke()
  }
}

async function onStageClick(event: MouseEvent) {
  const stage = stageEl.value
  const video = videoEl.value
  const d = details.value
  if (!stage || !video || !d) return
  const bounds = stage.getBoundingClientRect()
  measure()
  const point = mapClientPoint(event.clientX, event.clientY, bounds, displayRect.value)
  if (!point) {
    notice.value = '点击位置在视频留白区域，已忽略。'
    touchResult.value = null
    marker.value = null
    drawOverlay()
    return
  }
  // Prefer requestVideoFrameCallback mediaTime; fall back to currentTime.
  const timestamp = Number.isFinite(mediaTime.value) && mediaTime.value >= 0
    ? mediaTime.value : video.currentTime
  marker.value = point
  drawOverlay()
  try {
    const result = await api.touch(videoId.value, {
      timestamp_sec: timestamp, x_norm: point.x, y_norm: point.y,
    })
    touchResult.value = result
    if (result.status === 'matched' && result.selected_entity) {
      const entity = entities.value.find(e => e.id === result.selected_entity!.entity_id) || null
      selectEntity(entity)
    } else if (result.status === 'no_hit') {
      selectEntity(null)
      notice.value = `空白区域：未命中任何对象（第 ${result.frame_index} 帧）。`
    } else {
      notice.value = `命中 ${result.candidates.length} 个候选对象，请在右侧选择。`
    }
    drawOverlay()
  } catch (err) { setError(err) }
}

function selectEntity(entity: Entity | null) {
  selected.value = entity
  if (entity) {
    sessionId.value = ''
    chat.value = []
    notice.value = `已选中：${entity.label}`
  }
}

async function ask() {
  const text = question.value.trim()
  if (!text || !selected.value || chatBusy.value) return
  const d = details.value
  const video = videoEl.value
  if (!d || !video) return
  chatBusy.value = true
  error.value = ''
  const timestamp = Number.isFinite(mediaTime.value) && mediaTime.value >= 0
    ? mediaTime.value : video.currentTime
  chat.value.push({ role: 'user', text })
  question.value = ''
  try {
    const res = await api.qa(videoId.value, {
      session_id: sessionId.value || undefined,
      entity_id: selected.value.id,
      timestamp_sec: Math.min(timestamp, d.duration - 0.001),
      question: text,
    })
    sessionId.value = res.session_id
    chat.value.push({ role: 'assistant', text: res.answer, evidence: res.evidence_frames })
  } catch (err) {
    chat.value.push({ role: 'assistant', text: `问答失败：${errorMessage(err)}` })
  } finally { chatBusy.value = false }
}

watch([showMasks, maskVersion, indexStatus], () => drawOverlay())

onMounted(async () => {
  await Promise.all([refreshHealth(), refreshLibrary()])
  window.addEventListener('resize', measure)
  observeStage()
})

onBeforeUnmount(() => {
  stopPolling()
  resizeObserver?.disconnect()
  window.removeEventListener('resize', measure)
  const video = videoEl.value
  if (video && frameHandle !== undefined) video.cancelVideoFrameCallback?.(frameHandle)
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">V</span>
        <div>
          <h1>Video Interactive Lab</h1>
          <p>纸书智伴 · Qwen VLM × SAM 2 交互式视频识别与问答</p>
        </div>
      </div>
      <div class="badge" :class="{ warn: !health?.qwen_configured || !health?.replicate_configured }">
        {{ configuredBadge }}
      </div>
    </header>

    <main class="layout">
      <!-- Left: upload + library -->
      <aside class="panel left">
        <section class="card">
          <h2>上传视频</h2>
          <p class="hint">MP4 / MOV / AVI · ≤100MB · 首版 ≤30 秒</p>
          <label class="upload">
            <input ref="fileInput" type="file" accept="video/mp4,video/quicktime,video/x-msvideo,.mp4,.mov,.avi"
                   :disabled="uploadBusy || busy" @change="onUpload" />
            <span>{{ uploadBusy ? '上传中…' : '选择视频文件' }}</span>
          </label>
          <p class="hint">上传后自动 FFmpeg 标准化（H.264 / 24fps / 长边 1280），随后自动开始分析。</p>
        </section>

        <section class="card grow">
          <h2>视频库 <button class="link" @click="refreshLibrary">刷新</button></h2>
          <ul class="library">
            <li v-for="item in library" :key="item.id"
                :class="{ active: item.id === videoId }" @click="loadVideo(item.id)">
              <span class="dot" :class="item.status" />
              <span class="name" :title="item.name">{{ item.name }}</span>
              <small>{{ item.status }}</small>
            </li>
            <li v-if="!library.length" class="empty">暂无视频，先上传一段 MP4。</li>
          </ul>
        </section>

        <section v-if="details" class="card">
          <h2>视频信息</h2>
          <dl class="meta">
            <dt>分辨率</dt><dd>{{ details.width }} × {{ details.height }}</dd>
            <dt>帧率</dt><dd>{{ details.canonical_fps }} fps（原始 {{ details.original_fps?.toFixed?.(2) }}）</dd>
            <dt>帧数</dt><dd>{{ details.frame_count }}</dd>
            <dt>时长</dt><dd>{{ details.duration?.toFixed?.(2) }} s</dd>
            <dt>关键帧</dt><dd>{{ details.keyframes?.length ?? 0 }} 张（每 1 秒）</dd>
          </dl>
          <button class="primary" :disabled="!canAnalyze" @click="startAnalysis">
            {{ busy ? '处理中…' : '开始 / 重新分析' }}
          </button>
          <div v-if="task" class="progress">
            <div class="progress-bar"><i :style="{ width: stagePercent + '%' }" /></div>
            <small>{{ stageText }} {{ stagePercent }}%</small>
            <small v-if="task.error" class="err">{{ task.error }}</small>
          </div>
        </section>
      </aside>

      <!-- Center: player -->
      <section class="panel center">
        <div class="card player-card">
          <div class="player-head">
            <h2>交互播放器</h2>
            <label class="toggle"><input type="checkbox" v-model="showMasks" /> 显示 SAM Mask</label>
          </div>
          <div v-if="!videoId" class="placeholder">
            <p>请从左侧上传或选择视频</p>
          </div>
          <div v-else ref="stageEl" class="stage" @click="onStageClick">
            <video ref="videoEl" :src="api.playbackUrl(videoId)" controls playsinline preload="metadata"
                   @loadedmetadata="measure" @seeked="measure" />
            <canvas ref="maskCanvas" class="overlay mask-layer" />
            <canvas ref="overlay" class="overlay" />
            <MaskOverlay v-if="details" ref="maskOverlayRef"
              :video-id="videoId" :details="details" :entities="entities"
              :video-el="videoEl" :canvas-el="maskCanvas" :rect="displayRect"
              :selected-entity-id="selected?.id ?? null" :enabled="showMasks"
              :refresh-token="maskVersion" @frame="onOverlayFrame" @stats="onOverlayStats" />
          </div>
          <p v-if="overlayStats" class="hint mask-stats">
            Mask 叠加：帧 {{ overlayStats.frame }} · 已加载 {{ overlayStats.loaded }} 张 ·
            缓存 {{ overlayStats.cacheSize }} 张 · 单张加载 {{ overlayStats.loadMs ?? '—' }} ms
          </p>
          <p v-if="notice" class="notice">{{ notice }}</p>
          <p v-if="error" class="err-banner">{{ error }}</p>
          <div v-if="touchResult" class="touch-result" :data-status="touchResult.status">
            <strong>{{ touchResult.status === 'matched' ? '命中对象'
                      : touchResult.status === 'ambiguous' ? '候选对象' : '未命中' }}</strong>
            <span>帧 {{ touchResult.frame_index }} · t={{ touchResult.timestamp_sec.toFixed(2) }}s</span>
            <span v-if="touchResult.match_method">{{ touchResult.match_method }}</span>
            <span v-for="c in touchResult.candidates" :key="c.entity_id" class="chip"
                  @click="selectEntity(entities.find(e => e.id === c.entity_id) || null)">
              {{ c.name }}（{{ c.confidence.toFixed(1) }}）
            </span>
          </div>
          <p class="hint">点击视频画面中的对象；留白区域（object-fit: contain）自动忽略。</p>
        </div>
      </section>

      <!-- Right: objects + QA -->
      <aside class="panel right">
        <section class="card">
          <h2>识别对象 <small v-if="indexStatus">索引 {{ indexStatus.status }}</small></h2>
          <div v-if="indexStatus?.status === 'failed'" class="err">分析失败，请重新分析或检查密钥 / 额度。</div>
          <ul class="entities">
            <li v-for="e in entities" :key="e.id" :class="{ active: selected?.id === e.id }"
                @click="selectEntity(e)">
              <div class="entity-title">
                <span class="color-dot" :style="{ background: entityColor(e.id) }" />
                <strong>{{ e.label }}</strong><small>{{ e.category }}</small>
              </div>
              <p>{{ e.description }}</p>
              <p class="attr" v-if="e.attributes && Object.keys(e.attributes).length">
                {{ Object.entries(e.attributes).map(([k, v]) => `${k}: ${v}`).join(' · ') }}
              </p>
              <p class="attr" v-if="e.action">动作：{{ e.action }}</p>
              <p class="prediction" :class="e.prediction?.status">
                分割：{{ e.prediction?.status ?? 'pending' }}
                <span v-if="e.prediction?.error"> · {{ e.prediction.error }}</span>
                <button class="link" @click.stop="resegment(e)">重新分割</button>
              </p>
            </li>
            <li v-if="!entities.length" class="empty">尚无对象，先执行「开始分析」。</li>
          </ul>
        </section>

        <section class="card grow chat-card">
          <h2>对象问答 <small v-if="selected">· {{ selected.label }}</small></h2>
          <div v-if="!selected" class="empty">点击视频中的对象或右侧列表后开始提问。</div>
          <div v-else class="chat">
            <div v-for="(m, i) in chat" :key="i" class="bubble" :class="m.role">
              <p>{{ m.text }}</p>
              <small v-if="m.evidence?.length">证据帧：{{ m.evidence.join(', ') }}</small>
            </div>
            <div v-if="chatBusy" class="bubble assistant"><p>Qwen 正在依据画面回答…</p></div>
          </div>
          <form v-if="selected" class="ask" @submit.prevent="ask">
            <input v-model="question" maxlength="2000" placeholder="例如：它是什么动物？它戴着什么？" />
            <button class="primary" :disabled="chatBusy || !question.trim()">提问</button>
          </form>
          <p class="hint">回答只依据当前帧画面与关键帧观察；视频未展示的信息会明确说明无法确定。</p>
        </section>
      </aside>
    </main>
  </div>
</template>
