import axios from 'axios'

const http = axios.create({ baseURL: '/api/v1', timeout: 30000 })

export type Task = {
  id: string
  video_id: string
  stage: string
  progress: number
  error: string
}

export type VideoDetails = {
  id: string
  name: string
  width: number
  height: number
  duration: number
  canonical_fps: number
  original_fps: number
  frame_count: number
  status: string
  version: number
  keyframes: { frame_index: number; pts_us: number }[]
}

export type Prediction = {
  status?: string
  error?: string
  id?: string
  seed_frame?: number
  seed_point?: number[]
  elapsed_sec?: number
  [key: string]: unknown
}

export type Entity = {
  id: string
  label: string
  category: string
  description: string
  attributes: Record<string, string>
  action: string
  seed_frame: number
  seed_point: number[]
  prediction: Prediction | null
}

export type Hit = {
  entity_id: string
  name: string
  category: string
  description: string
  area: number
  confidence: number
}

export type TouchResult = {
  status: 'matched' | 'ambiguous' | 'no_hit'
  video_id: string
  frame_index: number
  timestamp_sec: number
  selected_entity: Hit | null
  candidates: Hit[]
  match_method: string | null
  match_score: number
}

export type FrameMask = {
  track_id: string
  area: number
  pts_us: number
  bbox: number[] | null
  mask_url: string
}

export type IndexStatus = {
  status: string
  version: number
  frame_index?: number | null
  masks?: FrameMask[]
}

export const api = {
  health: () => http.get<{ status: string; qwen_configured: boolean; replicate_configured: boolean }>('/health'),

  upload: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<{ video_id: string; task_id: string | null; cached?: boolean; message?: string }>('/videos', form)
    return data
  },

  task: async (id: string) => (await http.get<Task>(`/tasks/${id}`)).data,

  library: async () => (await http.get<{ id: string; name: string; status: string }[]>('/videos')).data,

  details: async (id: string) => (await http.get<VideoDetails>(`/videos/${id}`)).data,

  playbackUrl: (id: string) => `/api/v1/videos/${id}/playback`,
  thumbnailUrl: (id: string) => `/api/v1/videos/${id}/thumbnail`,

  analyze: async (id: string) => (await http.post<{ task_id: string }>(`/videos/${id}/analyze`)).data,

  segment: async (id: string, entityId?: string, seed?: { entity_id: string; frame_index: number; x: number; y: number }) =>
    (await http.post<{ task_id: string; version: number; reused: boolean }>(
      `/videos/${id}/segment`, entityId ? { entity_id: entityId, seed } : null)).data,

  entities: async (id: string) => (await http.get<Entity[]>(`/videos/${id}/entities`)).data,

  index: async (id: string, timestampSec?: number) => {
    const params = timestampSec === undefined ? {} : { timestamp_sec: timestampSec }
    return (await http.get<IndexStatus>(`/videos/${id}/index`, { params })).data
  },

  maskUrl: (id: string, entityId: string, frame: number) =>
    `/api/v1/videos/${id}/masks/${encodeURIComponent(entityId)}/${frame}`,

  touch: async (id: string, payload: { timestamp_sec: number; x_norm: number; y_norm: number }) =>
    (await http.post<TouchResult>(`/videos/${id}/touch`, payload)).data,

  qa: async (id: string, payload: {
    session_id?: string
    entity_id: string
    timestamp_sec: number
    question: string
  }) => (await http.post<{ session_id: string; answer: string; evidence_frames: number[] }>(
    `/videos/${id}/qa`, payload)).data,
}

export function taskLabel(stage: string): string {
  const labels: Record<string, string> = {
    uploaded: '排队中',
    preprocessing: 'FFmpeg 标准化处理',
    vlm_analyzing: 'Qwen 识别关键帧对象',
    entity_association: '关联对象身份',
    sam_segmenting: 'SAM 2 视频分割',
    building_index: '建立 Mask 索引',
    completed: '完成',
    partial: '部分完成',
    failed: '失败',
  }
  return labels[stage] || stage
}

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail) return JSON.stringify(detail)
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
