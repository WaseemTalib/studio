import { createContext, useContext } from 'react'
import { toastSuccess } from './toast'

export type Permission = 'shorts' | 'voice' | 'video_maker' | 'video_director' | 'stickman'
export type Role = 'admin' | 'user'

export interface User {
  id: string
  username: string
  role: Role
  permissions: Permission[]
  disabled: boolean
  created_at: number
  session_expires_at?: number
}

export interface AuthStatus {
  setup_needed: boolean
  user: User | null
  permissions: Record<Permission, string>
  idle_hours: number
}

/** Fired when the server says the session is gone (logged out elsewhere or idle too long). */
export const UNAUTHENTICATED_EVENT = 'studio:unauthenticated'

// --- User activity ---------------------------------------------------------------------------------
// Requests made while nobody is using the page (polling) tell the server not to count them as activity,
// so a forgotten open tab still logs out after the idle limit.
const ACTIVITY_KEY = 'studio-last-activity'
const RECENT_MS = 10 * 60 * 1000
let lastActivity = Date.now()

function markActive() {
  const now = Date.now()
  if (now - lastActivity < 15_000) return
  lastActivity = now
  try { localStorage.setItem(ACTIVITY_KEY, String(now)) } catch { /* private mode */ }
}

for (const ev of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
  window.addEventListener(ev, markActive, { passive: true, capture: true })
}

/** Last time the user did something in any tab of the app. */
export function lastActive(): number {
  let shared = 0
  try { shared = Number(localStorage.getItem(ACTIVITY_KEY)) || 0 } catch { /* private mode */ }
  return Math.max(lastActivity, shared)
}

export function activityHeaders(): Record<string, string> {
  return Date.now() - lastActive() > RECENT_MS ? { 'X-Studio-Background': '1' } : {}
}

// --- API -------------------------------------------------------------------------------------------

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...activityHeaders(), ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail
        : Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join('; ')
          : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  toastSuccess(init?.method, path)
  return (res.status === 204 ? undefined : await res.json()) as T
}

const post = (body: unknown, method = 'POST'): RequestInit => ({ method, body: JSON.stringify(body) })

export const authApi = {
  status: () => call<AuthStatus>('/api/auth/status'),
  setup: (username: string, password: string) => call<AuthStatus>('/api/auth/setup', post({ username, password })),
  login: (username: string, password: string) => call<AuthStatus>('/api/auth/login', post({ username, password })),
  logout: () => call<void>('/api/auth/logout', { method: 'POST' }),
  changePassword: (current_password: string, new_password: string) =>
    call<void>('/api/auth/password', post({ current_password, new_password })),
  users: () => call<User[]>('/api/users'),
  addUser: (u: { username: string; password: string; role: Role; permissions: Permission[] }) =>
    call<User>('/api/users', post(u)),
  updateUser: (id: string, u: Partial<{ username: string; password: string; role: Role; permissions: Permission[];
    disabled: boolean }>) => call<User>(`/api/users/${id}`, post(u, 'PATCH')),
  activity: () => call<ActivityItem[]>('/api/users/activity'),
  deleteUser: (id: string) => call<void>(`/api/users/${id}`, { method: 'DELETE' }),
}

export interface ActivityItem {
  id: string
  tool: Permission
  title: string
  status: string
  created_at: number
  created_by: string | null
  created_by_name: string | null
}

// --- Context ---------------------------------------------------------------------------------------

export interface AuthContextValue {
  user: User
  labels: Record<Permission, string>
  can: (p: Permission) => boolean
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthContext')
  return ctx
}
