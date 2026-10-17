import { request, type LimitOption, type LimitState, type Position } from './api'
import type { TTSProvider, VoiceSettings } from './voiceApi'

export type SourceKind = 'prompt' | 'description' | 'script' | 'news'
export type Footage = 'pexels' | 'pixabay' | 'both' | 'none'
export type MakerStage = 'voice' | 'captions' | 'footage' | 'render'

export interface Scene {
  text: string
  search_terms: string[]
}

export interface DraftRequest {
  kind: SourceKind
  text: string
  url: string | null
  minutes: number
  language: string
  tone: string
  service: string | null
}

export interface Draft {
  title: string
  description: string
  hashtags: string[]
  scenes: Scene[]
  service: string
  source_title: string | null
  words: number
  estimated_seconds: number
}

export interface VideoRequest {
  title: string
  description: string
  hashtags: string[]
  scenes: Scene[]
  language: string | null
  voice: { provider: TTSProvider; voice_id: string; voice_name: string; model_id: string; settings: VoiceSettings }
  aspect: '16:9' | '9:16' | '1:1'
  resolution: '720p' | '1080p'
  footage: Footage
  clip_seconds: number
  transition: 'cut' | 'fade'
  caption_style: string
  caption_position: Position
  highlight_color: string
  words_per_line: number
  title_overlay: boolean
  music: string | null
  music_volume: number
}

export interface Credit {
  provider: string
  author: string
  page: string
}

export interface Video {
  id: string
  title: string
  request: VideoRequest
  status: 'queued' | 'processing' | 'done' | 'failed' | 'cancelled'
  stage: MakerStage | null
  progress: number
  error: string | null
  video: string | null
  thumbnail: string | null
  duration: number | null
  credits: Credit[]
  created_at: number
  eta_seconds: number | null
  eta_scope: 'import' | 'total' | null
  cancelling: boolean
  limit: LimitState | null
}

export interface Track {
  name: string
  url: string
  size: number
}

export interface MakerOptions {
  services: LimitOption[]
  default_service: string | null
  footage: { pexels: boolean; pixabay: boolean }
  music: Track[]
}

/** The script service hit a usage limit: what it was and what else can write the script. */
export class DraftLimit extends Error {
  state: LimitState
  constructor(state: LimitState) {
    super(state.limit.service_label + ' usage limit reached')
    this.state = state
  }
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const makerApi = {
  options: () => request<MakerOptions>('/api/maker/options'),
  draft: async (body: DraftRequest): Promise<Draft> => {
    const res = await fetch('/api/maker/draft', json(body))
    if (res.status === 429) {
      const { detail } = await res.json()
      throw new DraftLimit({ mode: 'ask', stage: 'script', limit: detail.limit, options: detail.options, seconds_left: null })
    }
    if (!res.ok) {
      let message = res.statusText
      try {
        const { detail } = await res.json()
        message = typeof detail === 'string' ? detail
          : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, '')).join('; ')
          : JSON.stringify(detail)
      } catch { /* non-JSON error body */ }
      throw new Error(message)
    }
    return res.json()
  },
  videos: () => request<Video[]>('/api/maker/videos'),
  create: (body: VideoRequest) => request<Video>('/api/maker/videos', json(body)),
  cancel: (id: string) => request<Video>(`/api/maker/videos/${id}/cancel`, { method: 'POST' }),
  retry: (id: string) => request<Video>(`/api/maker/videos/${id}/retry`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/api/maker/videos/${id}`, { method: 'DELETE' }),
  limitChoice: (id: string, action: 'wait' | 'switch', option?: string) =>
    request<Video>(`/api/maker/videos/${id}/limit-choice`, json({ action, option: option ?? null })),
  uploadMusic: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Track[]>('/api/maker/music', { method: 'POST', body: form })
  },
  deleteMusic: (name: string) => request<void>(`/api/maker/music/${encodeURIComponent(name)}`, { method: 'DELETE' }),
}

export const MAKER_STAGE_LABEL: Record<MakerStage, string> = {
  voice: 'Recording narration',
  captions: 'Timing captions',
  footage: 'Fetching stock footage',
  render: 'Rendering video',
}

/** Script languages: label sent to the writer, and Whisper's code for caption timing. */
export const SCRIPT_LANGUAGES: { label: string; code: string }[] = [
  { label: 'English', code: 'en' }, { label: 'Hindi', code: 'hi' }, { label: 'Urdu', code: 'ur' },
  { label: 'Spanish', code: 'es' }, { label: 'French', code: 'fr' }, { label: 'German', code: 'de' },
  { label: 'Portuguese', code: 'pt' }, { label: 'Italian', code: 'it' }, { label: 'Arabic', code: 'ar' },
  { label: 'Chinese', code: 'zh' }, { label: 'Japanese', code: 'ja' }, { label: 'Korean', code: 'ko' },
  { label: 'Russian', code: 'ru' }, { label: 'Turkish', code: 'tr' }, { label: 'Indonesian', code: 'id' },
  { label: 'Bengali', code: 'bn' }, { label: 'Dutch', code: 'nl' }, { label: 'Polish', code: 'pl' },
  { label: 'Vietnamese', code: 'vi' },
]
