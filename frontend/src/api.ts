export type Aspect = '9:16' | '1:1' | '16:9'
export type ProjectStatus = 'queued' | 'processing' | 'done' | 'failed' | 'cancelled'
export type Stage = 'ingest' | 'transcribe' | 'highlights' | 'render'

export type Position = 'top' | 'middle' | 'bottom'
export type Layout = 'auto' | 'fill' | 'track' | 'split' | 'fit'
export type Resolution = '720p' | '1080p' | '4k'

export interface LookOptions {
  aspect: Aspect
  layout: Layout
  caption_style: string
  caption_position: Position
  highlight_color: string
  words_per_line: number
  title_overlay: boolean
  auto_zoom: boolean
  resolution: Resolution
}

export interface ProjectOptions extends LookOptions {
  mode: 'clip' | 'full'
  clip_model: 'fast' | 'quality'
  instructions: string
  clip_count: number
  min_seconds: number
  max_seconds: number
  genre: string
  topics: string[]
  range_start: number | null
  range_end: number | null
  language: string | null
}

export interface Word {
  start: number
  end: number
  text: string
}

export interface SourceInfo {
  title: string | null
  duration: number | null
  thumbnail: string | null
  uploader: string | null
}

export interface Template {
  id: string
  name: string
  options: Partial<ProjectOptions>
  is_default: boolean
}

export interface CaptionStyleInfo {
  id: string
  label: string
  animated: boolean
  group: 'opus' | 'classic' | 'more'
  /** Highlight colour the style is designed for; picking the style switches to it. */
  highlight: string
}

export interface Presets {
  caption_styles: CaptionStyleInfo[]
  languages: { id: string; label: string }[]
  genres: { id: string; label: string }[]
  clip_lengths: { id: string; label: string; min_seconds: number; max_seconds: number }[]
}

export function captionPreviewUrl(o: Pick<LookOptions, 'caption_style' | 'highlight_color' | 'caption_position' | 'aspect' | 'title_overlay'>) {
  const q = new URLSearchParams({
    style: o.caption_style,
    color: o.highlight_color,
    position: o.caption_position,
    aspect: o.aspect,
    title: String(o.title_overlay),
  })
  return `/api/caption-preview.png?${q}`
}

/** Format seconds as h:mm:ss. */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

/** Parse "1:23", "1:02:03" or "83" into seconds; empty → null; invalid → NaN. */
export function parseTime(text: string): number | null {
  const t = text.trim()
  if (!t) return null
  if (!/^\d+(:\d{1,2}){0,2}$/.test(t)) return NaN
  return t.split(':').reduce((acc, part) => acc * 60 + Number(part), 0)
}

export interface Project {
  id: string
  title: string
  source_type: 'upload' | 'url'
  source: string
  options: ProjectOptions
  status: ProjectStatus
  stage: Stage | null
  progress: number
  /** Estimated seconds until processing finishes; null while it can't be estimated yet. */
  eta_seconds: number | null
  /** What eta_seconds covers: just the import (download), or the whole job. */
  eta_scope: 'import' | 'total' | null
  /** A cancel was requested and the job is stopping. */
  cancelling: boolean
  /** A usage limit the job is waiting on or asking about; null when none. */
  limit: LimitState | null
  error: string | null
  duration: number | null
  thumbnail: string | null
  clip_count: number
  created_at: number
}

export interface LimitInfo {
  service: string
  service_label: string
  model: string
  /** e.g. "tokens per day" */
  name: string
  /** e.g. "TPD"; null when the service only says "try again later" */
  code: string | null
  limit: number | null
  used: number | null
  requested: number | null
  remaining: number | null
  /** Seconds until this request fits again, when the project was fetched. */
  resets_in: number
  message: string
  /** Latest rate-limit headers from the service, if it sends them. */
  recent: {
    requests_per_day: number | null
    requests_left_today: number | null
    requests_reset_in: number | null
    tokens_per_minute: number | null
    tokens_left_this_minute: number | null
    tokens_reset_in: number | null
  } | null
}

export interface LimitOption {
  id: string
  label: string
  detail: string
  available: boolean
  reason: string | null
}

export interface LimitState {
  /** ask: waiting for the user's choice; wait: user chose to wait; auto_wait: short per-minute wait */
  mode: 'ask' | 'wait' | 'auto_wait'
  stage: 'transcribe' | 'highlights' | null
  limit: LimitInfo
  options: LimitOption[]
  seconds_left: number | null
}

export interface Clip {
  id: string
  project_id: string
  rank: number
  title: string
  topic: string
  hook: string
  reason: string
  score: number
  hashtags: string[]
  start: number
  end: number
  transcript: string
  video: string
  thumbnail: string
  look: Partial<LookOptions>
  layout: string
  words: Word[] | null
  status: 'ready' | 'rendering' | 'failed'
  error: string | null
  version: number
}

export interface ClipDetail extends Clip {
  project: Project
  context_words: Word[]
  source_video: string | null
}

export interface ClipRender {
  start: number
  end: number
  look: LookOptions
  title: string
  words: Word[] | null
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export const api = {
  presets: () => request<Presets>('/api/presets'),
  listProjects: () => request<Project[]>('/api/projects'),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  listClips: (id: string) => request<Clip[]>(`/api/projects/${id}/clips`),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  renameProject: (id: string, title: string) =>
    request<Project>(`/api/projects/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  retryProject: (id: string) => request<Project>(`/api/projects/${id}/retry`, { method: 'POST' }),
  cancelProject: (id: string) => request<Project>(`/api/projects/${id}/cancel`, { method: 'POST' }),
  limitChoice: (id: string, action: 'wait' | 'switch', option?: string) =>
    request<Project>(`/api/projects/${id}/limit-choice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, option: option ?? null }),
    }),
  createProject: (
    source: { url: string } | { file: File },
    options: ProjectOptions,
    extra: { title?: string | null; srt?: File | null } = {},
  ) => {
    const form = new FormData()
    form.append('options', JSON.stringify(options))
    if ('url' in source) form.append('url', source.url)
    else form.append('file', source.file)
    if (extra.title) form.append('title', extra.title)
    if (extra.srt) form.append('srt', extra.srt)
    return request<Project>('/api/projects', { method: 'POST', body: form })
  },
  sourceInfo: (url: string) =>
    request<SourceInfo>('/api/source-info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  listTemplates: () => request<Template[]>('/api/templates'),
  saveTemplate: (name: string, options: ProjectOptions, isDefault: boolean) =>
    request<Template[]>('/api/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, options, is_default: isDefault }),
    }),
  deleteTemplate: (id: string) => request<void>(`/api/templates/${id}`, { method: 'DELETE' }),
  getClip: (id: string) => request<ClipDetail>(`/api/clips/${id}`),
  renderClip: (id: string, body: ClipRender) =>
    request<Clip>(`/api/clips/${id}/render`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  updateClip: (id: string, patch: Partial<Pick<Clip, 'title' | 'hashtags'>>) =>
    request<Clip>(`/api/clips/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
}

export const isWorking = (p: Project) => p.status === 'queued' || p.status === 'processing'

/** Rough time left, e.g. "about 4 min left" or "about 1 h 5 min left to import". */
export function formatEta(seconds: number, scope: 'import' | 'total' | null = 'total'): string {
  const suffix = scope === 'import' ? ' to import' : ''
  if (seconds < 45) return `less than a minute left${suffix}`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `about ${minutes} min left${suffix}`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `about ${h} h${m ? ` ${m} min` : ''} left${suffix}`
}

export const STAGE_LABEL: Record<Stage, string> = {
  ingest: 'Importing video',
  transcribe: 'Transcribing',
  highlights: 'Finding highlights',
  render: 'Rendering clips',
}

export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}
