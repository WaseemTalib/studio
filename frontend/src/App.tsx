import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  AuthContext, authApi, lastActive, UNAUTHENTICATED_EVENT, useAuth, type AuthContextValue, type AuthStatus,
  type Permission,
} from './auth'
import ActiveTasks from './components/ActiveTasks'
import Toaster from './components/Toaster'
import ClipEditor from './pages/ClipEditor'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import ProjectPage from './pages/ProjectPage'
import Settings from './pages/Settings'
import StickmanDirector from './pages/StickmanDirector'
import VideoMaker from './pages/VideoMaker'
import VoiceStudio from './pages/VoiceStudio'
import { toast } from './toast'

const TOOLS: { to: string; label: string; perm: Permission }[] = [
  { to: '/', label: 'Turn to Shorts', perm: 'shorts' },
  { to: '/voice-studio', label: 'Voice Studio', perm: 'voice' },
  { to: '/video-maker', label: 'Video Maker', perm: 'video_maker' },
  { to: '/director', label: 'Video Director', perm: 'video_director' },
  { to: '/stickman', label: 'Stickman Director', perm: 'stickman' },
]

function Header() {
  const [navOpen, setNavOpen] = useState(false)
  const { pathname } = useLocation()
  const { user, can, logout } = useAuth()

  useEffect(() => setNavOpen(false), [pathname])

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand">
          <span className="brand-mark">▶</span> Shortcut Studio
        </Link>
        <button
          type="button"
          className="nav-toggle"
          aria-expanded={navOpen}
          aria-controls="primary-navigation"
          aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
          onClick={() => setNavOpen((open) => !open)}
        >
          <span aria-hidden="true">{navOpen ? '✕' : '☰'}</span>
          <span>Tools</span>
        </button>
        <nav id="primary-navigation" className={`nav ${navOpen ? 'open' : ''}`} aria-label="Tools">
          {TOOLS.filter((t) => can(t.perm)).map((t) => (
            <NavLink key={t.to} to={t.to} end={t.to === '/'}>{t.label}</NavLink>
          ))}
          <NavLink to="/settings" className="nav-settings">Settings</NavLink>
        </nav>
        <div className="user-menu">
          <span className="muted small" title={`Role: ${user.role}`}>{user.username}</span>
          <button type="button" className="ghost" onClick={logout}>Log out</button>
        </div>
      </div>
      <ActiveTasks />
    </header>
  )
}

/** Shows the page only to users with the tool's permission. */
function Guard({ perm, children }: { perm: Permission; children: ReactNode }) {
  const { can } = useAuth()
  if (can(perm)) return children
  const first = TOOLS.find((t) => can(t.perm))
  if (perm === 'shorts' && first) return <Navigate to={first.to} replace />
  return (
    <div className="panel">
      <h2>No access</h2>
      <p className="muted">Your account cannot use this tool. Ask an admin to grant access in Settings.</p>
    </div>
  )
}

function Studio() {
  return (
    <>
      <Header />
      <main className="container">
        <Routes>
          <Route path="/" element={<Guard perm="shorts"><Dashboard /></Guard>} />
          <Route path="/projects/:id" element={<Guard perm="shorts"><ProjectPage /></Guard>} />
          <Route path="/clips/:id" element={<Guard perm="shorts"><ClipEditor /></Guard>} />
          <Route path="/voice-studio" element={<Guard perm="voice"><VoiceStudio /></Guard>} />
          <Route path="/video-maker" element={<Guard perm="video_maker"><VideoMaker /></Guard>} />
          <Route path="/director" element={<Guard perm="video_director"><StickmanDirector mode="generic" /></Guard>} />
          <Route path="/stickman" element={<Guard perm="stickman"><StickmanDirector /></Guard>} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </>
  )
}

export default function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(() => {
    authApi.status().then((s) => { setStatus(s); setLoadError(null) }, (e: Error) => setLoadError(e.message))
  }, [])
  useEffect(refresh, [refresh])

  const endSession = useCallback((message: string | null) => {
    setNotice(message)
    setStatus((s) => (s ? { ...s, user: null } : s))
  }, [])

  const logout = useCallback(() => {
    authApi.logout().catch(() => undefined).finally(() => {
      endSession('You have been logged out.')
      toast('Logged out', 'info')
    })
  }, [endSession])

  // The server ended the session (idle limit, logged out elsewhere, account disabled).
  useEffect(() => {
    const onUnauth = () => endSession('Your session ended. Please log in again.')
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauth)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauth)
  }, [endSession])

  // Log out an open tab after the idle limit without activity, even when it makes no requests.
  const user = status?.user ?? null
  const idleMs = (status?.idle_hours ?? 24) * 3600_000
  useEffect(() => {
    if (!user) return
    const check = () => {
      if (Date.now() - lastActive() > idleMs) {
        authApi.logout().catch(() => undefined)
        endSession(`You were logged out after ${idleMs / 3600_000} hours without activity.`)
      }
    }
    const id = setInterval(check, 60_000)
    document.addEventListener('visibilitychange', check)
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', check) }
  }, [user, idleMs, endSession])

  const ctx = useMemo<AuthContextValue | null>(() => user && status && {
    user,
    labels: status.permissions,
    can: (p: Permission) => user.role === 'admin' || user.permissions.includes(p),
    logout,
  }, [user, status, logout])

  let page
  if (!status) {
    page = <div className="auth-screen"><p className={loadError ? 'error' : 'muted'}>{loadError ?? 'Loading…'}</p></div>
  } else if (!ctx) {
    page = <Login setup={status.setup_needed} notice={notice} onDone={(s) => {
      setNotice(null)
      setStatus(s)
      if (s.user) toast(status.setup_needed ? `Admin account created. Welcome, ${s.user.username}!` : `Welcome back, ${s.user.username}`)
    }} />
  } else {
    page = <AuthContext.Provider value={ctx}><Studio /></AuthContext.Provider>
  }
  return <>{page}<Toaster /></>
}
