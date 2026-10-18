import { request, type LimitState } from './api'

export interface DirectorStyle {
  id: string
  name: string
  short: string
  best_for: string
}

export interface DirectorSettings {
  source_kind: 'topic' | 'script' | 'article'
  source: string
  url: string | null
  aspect: '16:9' | '9:16'
  seconds: number
  style: string
  faceless: boolean
  language: string
  voice: 'suggest' | 'male' | 'female' | 'custom'
  voice_custom: string
  tone: string
  ai_service: string | null
}

export interface Check {
  ok: boolean
  text: string
}

export interface ClipOutline {
  clip: number
  stage: string
  purpose: string
  vo: string
  location: string
}

export interface StoryRow {
  clip: number
  scene: string
  beats: string[]
  devices: string[]
  camera: string
  vo_translation: string
  bgm_sfx: string
  end_state: string
  roles: string[]
}

export interface Proposal {
  title_en: string
  title_ref: string
  core_message: string
  hook: string
  voice_summary: string
  voice_block: string
  palette: { color: string; meaning: string }[]
  environment_mood: string
  music: string
  bgm_direction: string
  emotional_arc: string
  cast: { role: string; anchor: string; summary: string }[]
  clips: ClipOutline[]
  rows: StoryRow[]
  overlays: { clip: number; text: string; placement: string }[]
  checks: Check[]
}

export interface ClipPrompt {
  clip: number
  title: string
  prompt: string
  vo: string
  stitch: string
  final_frame: string
  vo_split_by_ai: boolean
}

export interface Package {
  global_block: string
  clips: ClipPrompt[]
  stitching: string[]
  audio_note: string
  overlays: Proposal['overlays']
  checks: Check[]
}

export interface Plan {
  id: string
  title: string
  settings: DirectorSettings
  status: 'queued' | 'processing' | 'done' | 'failed' | 'cancelled'
  step: 'proposal' | 'prompts'
  stage: 'outline' | 'storyboard' | 'prompts' | null
  progress: number
  error: string | null
  approved: boolean
  proposal?: Proposal | null
  package?: Package | null
  has_package?: boolean
  created_at: number
  updated_at: number
  eta_seconds: number | null
  cancelling: boolean
  limit: LimitState | null
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const directorApi = {
  options: () => request<{ styles: DirectorStyle[]; max_seconds: number }>('/api/director/options'),
  plans: () => request<Plan[]>('/api/director/plans'),
  get: (id: string) => request<Plan>(`/api/director/plans/${id}`),
  create: (s: DirectorSettings) => request<Plan>('/api/director/plans', json(s)),
  revise: (id: string, body: { feedback: string; clip?: number; settings?: DirectorSettings }) =>
    request<Plan>(`/api/director/plans/${id}/revise`, json(body)),
  approve: (id: string) => request<Plan>(`/api/director/plans/${id}/approve`, { method: 'POST' }),
  rewriteClip: (id: string, clip: number, feedback: string) =>
    request<Plan>(`/api/director/plans/${id}/clips/${clip}/rewrite`, json({ feedback })),
  cancel: (id: string) => request<Plan>(`/api/director/plans/${id}/cancel`, { method: 'POST' }),
  retry: (id: string) => request<Plan>(`/api/director/plans/${id}/retry`, { method: 'POST' }),
  limitChoice: (id: string, action: 'wait' | 'switch', option?: string) =>
    request<Plan>(`/api/director/plans/${id}/limit-choice`, json({ action, option: option ?? null })),
  remove: (id: string) => request<void>(`/api/director/plans/${id}`, { method: 'DELETE' }),
}

export const isWriting = (p: Plan) => p.status === 'queued' || p.status === 'processing'

export const DIRECTOR_STAGE: Record<string, string> = {
  outline: 'Writing the outline and narration',
  storyboard: 'Writing the storyboard',
  prompts: 'Writing the clip prompts',
}

export const DURATIONS = [30, 60, 90, 120, 180, 300]

export const PLAN_LANGUAGES = ['English', 'Urdu', 'Hindi', 'Chinese', 'Spanish', 'Arabic', 'French', 'German',
  'Portuguese', 'Japanese', 'Korean', 'Turkish', 'Indonesian', 'Bengali', 'Russian']

export function formatSeconds(s: number): string {
  return s < 60 ? `${s}s` : s % 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s / 60} min`
}
