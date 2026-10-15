import { request } from './api'

export type TTSProvider = 'elevenlabs' | 'local'
export type VoiceSource = 'default' | 'mine' | 'library'
export type Control = 'speed' | 'stability' | 'similarity' | 'style' | 'speaker_boost'

export interface ProviderInfo {
  id: TTSProvider
  label: string
  available: boolean
  note?: string | null
  model?: LocalModelStatus
}

export interface LocalModelStatus {
  state: 'ready' | 'idle' | 'downloading' | 'failed'
  done: number
  total: number
  error: string | null
}

export interface TTSModel {
  id: string
  name: string
  description: string
  max_chars: number
  languages: string[]
  controls: Control[]
}

export interface Voice {
  provider: TTSProvider
  id: string
  name: string
  description: string
  gender: string | null
  age: string | null
  accent: string | null
  language: string | null
  use_case: string | null
  category: string | null
  preview_url: string | null
  public_owner_id: string | null
  in_library: boolean
  popularity: number | null
}

export interface VoiceFilters {
  search?: string
  gender?: string
  age?: string
  accent?: string
  language?: string
  use_case?: string
  category?: string
  sort?: string
}

export interface VoiceSettings {
  speed: number
  stability: number
  similarity: number
  style: number
  speaker_boost: boolean
}

export const DEFAULT_SETTINGS: VoiceSettings = { speed: 1, stability: 0.5, similarity: 0.75, style: 0, speaker_boost: true }

export interface Generation {
  id: string
  provider: TTSProvider
  voice_id: string
  voice_name: string
  model_id: string
  text: string
  settings: VoiceSettings
  audio: string
  duration: number | null
  created_at: number
}

export interface Usage {
  used: number | null
  limit: number | null
  resets_at: number | null
  tier: string | null
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const voiceApi = {
  providers: () => request<ProviderInfo[]>('/api/tts/providers'),
  models: (provider: TTSProvider) => request<TTSModel[]>(`/api/tts/models?provider=${provider}`),
  voices: (provider: TTSProvider, source: VoiceSource, filters: VoiceFilters, page?: string | null) => {
    const q = new URLSearchParams({ provider, source })
    for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v)
    if (page) q.set('page', page)
    return request<{ voices: Voice[]; next: string | null }>(`/api/tts/voices?${q}`)
  },
  addLibraryVoice: (v: Voice) =>
    request<{ voice_id: string }>('/api/tts/voices/library',
      json({ public_owner_id: v.public_owner_id, voice_id: v.id, name: v.name })),
  usage: () => request<Usage | null>('/api/tts/usage'),
  downloadLocalModel: () => request<LocalModelStatus>('/api/tts/local/download', { method: 'POST' }),
  generate: (body: { provider: TTSProvider; voice_id: string; voice_name: string; model_id: string; text: string;
    settings: VoiceSettings }) => request<Generation>('/api/tts/generate', json(body)),
  history: () => request<Generation[]>('/api/tts/history'),
  deleteGeneration: (id: string) => request<void>(`/api/tts/history/${id}`, { method: 'DELETE' }),
}

/** Gradient "orb" colours for a voice, stable per id (ElevenLabs-style avatar). */
export function orbStyle(id: string): { background: string } {
  let h = 0
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0
  const a = h % 360
  const b = (a + 40 + (h >> 9) % 120) % 360
  return { background: `radial-gradient(circle at 30% 30%, hsl(${a} 90% 72%), hsl(${b} 80% 45%) 70%)` }
}

export function voiceTags(v: Voice): string[] {
  const pretty = (s: string | null) => (s ? s.replace(/_/g, ' ') : null)
  return [pretty(v.accent), pretty(v.gender), pretty(v.age), pretty(v.use_case), v.language]
    .filter((x, i, all): x is string => !!x && all.indexOf(x) === i)
}
