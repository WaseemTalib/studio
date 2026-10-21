import { request, type LimitOption, type LimitState, type Position } from './api'
import type { TTSProvider, VoiceSettings } from './voiceApi'

export type SourceKind = 'prompt' | 'description' | 'script' | 'news'
export type Footage = 'pexels' | 'pixabay' | 'both' | 'none'
export type FootageSource = 'openverse' | 'web' | 'youtube' | 'pixabay' | 'pexels' | 'coverr' | 'commons' | 'unsplash'

export const FOOTAGE_SOURCES: { id: FootageSource; label: string; hint: string; key: string; signup: string }[] = [
  { id: 'openverse', label: 'Openverse', hint: 'live Creative Commons image search', key: '', signup: '' },
  { id: 'web', label: 'Web images', hint: 'live search · review usage rights', key: '', signup: '' },
  { id: 'youtube', label: 'YouTube', hint: 'real video results · review usage rights', key: 'yt-dlp', signup: 'installed with the backend' },
  { id: 'pixabay', label: 'Pixabay', hint: 'stock video', key: 'PIXABAY_API_KEY', signup: 'pixabay.com/api/docs' },
  { id: 'pexels', label: 'Pexels', hint: 'stock video', key: 'PEXELS_API_KEY', signup: 'pexels.com/api' },
  { id: 'coverr', label: 'Coverr', hint: 'cinematic stock video', key: 'COVERR_API_KEY', signup: 'email team@coverr.co' },
  { id: 'commons', label: 'Wikimedia Commons', hint: 'real-world & news video', key: '', signup: '' },
  { id: 'unsplash', label: 'Unsplash', hint: 'photos with a slow zoom', key: 'UNSPLASH_ACCESS_KEY', signup: 'unsplash.com/developers' },
]
export type MakerStage = 'voice' | 'captions' | 'footage' | 'render'

/** A stock clip or photo, as found by a footage search. */
export interface Media {
  key: string
  provider: 'openverse' | 'web' | 'searxng' | 'duckduckgo' | 'youtube' | 'pexels' | 'pixabay' | 'coverr' |
    'commons' | 'wikimedia' | 'unsplash' | 'upload'
  id: string
  link: string
  width: number
  height: number
  duration: number
  page: string
  author: string
  thumb: string
  preview: string
  text: string
  kind: 'video' | 'photo'
  license: string
  /** How well its description matches the search, 0..1. */
  score: number
  ping?: string
}

export interface Scene {
  text: string
  search_terms: string[]
  /** A key fact shown full screen at the start of the scene. */
  card_text: string
  quote: string
  quote_by: string
  /** A real place, for a Wikimedia Commons photo. */
  place: string
  /** Footage chosen in the review, in order; empty means chosen automatically. */
  clips: Media[]
  /** This page's id for the scene (not sent to the server). */
  uid?: string
}

export interface FootagePlan {
  scenes: ({ shots: number; clips: Media[]; candidates: Media[] } | null)[]
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
  music_mood: string
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
  /** Where footage comes from; empty means a plain background. */
  sources: FootageSource[]
  clip_seconds: number
  transition: 'cut' | 'fade'
  caption_style: string
  caption_position: Position
  highlight_color: string
  words_per_line: number
  caption_size: number
  caption_offset: number
  title_overlay: boolean
  music: string | null
  music_mode: MusicMode
  /** Search words for "match the script" music. */
  music_mood: string
  music_volume: number
  cards: boolean
  photos: boolean
  /** AI model that checks footage against the narration; null uses the default. */
  ai_service: string | null
}

export interface Credit {
  provider: string
  author: string
  page: string
  license?: string
  /** Music credits: the track's title. */
  title?: string
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

export type MusicMode = 'track' | 'random' | 'auto'

export interface Track {
  name: string
  url: string
  size: number
  /** Where a found track came from; null for uploads. */
  credit: { provider: string; title: string; author: string; page: string; license: string } | null
}

/** A free-licensed track from a music search. */
export interface FoundTrack {
  provider: 'jamendo' | 'commons'
  id: string
  title: string
  author: string
  duration: number
  preview: string
  download: string
  page: string
  license: string
  license_url: string
  tags: string
  ext: string
}

export interface MakerOptions {
  services: LimitOption[]
  default_service: string | null
  footage: Record<FootageSource, boolean>
  music: Track[]
  music_sources: { jamendo: boolean; commons: boolean }
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
  planFootage: (body: { scenes: Scene[]; sources: FootageSource[]; aspect: VideoRequest['aspect']; resolution: VideoRequest['resolution'];
    clip_seconds: number; speed: number; cards: boolean; photos: boolean; ai_service: string | null; only?: number }) =>
    request<FootagePlan>('/api/maker/footage', json(body)),
  searchFootage: (q: string, sources: (FootageSource | 'photos')[], aspect: VideoRequest['aspect'], resolution: VideoRequest['resolution']) =>
    request<Media[]>(`/api/maker/footage/search?${new URLSearchParams({ q, sources: sources.join(','), aspect, resolution })}`),
  uploads: () => request<Media[]>('/api/maker/footage/uploads'),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Media[]>('/api/maker/footage/uploads', { method: 'POST', body: form })
  },
  deleteUpload: (name: string) => request<void>(`/api/maker/footage/uploads/${encodeURIComponent(name)}`, { method: 'DELETE' }),
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
  searchMusic: (q: string, source: 'jamendo' | 'commons', instrumental: boolean) =>
    request<FoundTrack[]>(`/api/maker/music/search?${new URLSearchParams({ q, source, instrumental: String(instrumental) })}`),
  addMusic: (t: FoundTrack) => request<{ name: string; library: Track[] }>('/api/maker/music/add', json(t)),
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

let nextUid = 0
/** Gives a scene a stable id for this page, so its footage suggestions survive edits and moves. */
export const withUid = (s: Omit<Scene, 'uid'> & { uid?: string }): Scene => ({ ...s, uid: s.uid ?? `s${++nextUid}` })

export const blankScene = (text = ''): Scene =>
  withUid({ text, search_terms: [], card_text: '', quote: '', quote_by: '', place: '', clips: [] })

/** A scene as the server takes it. */
export function forApi(scene: Scene): Omit<Scene, 'uid'> {
  const copy: Partial<Scene> = { ...scene }
  delete copy.uid
  return copy as Omit<Scene, 'uid'>
}
