import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import ActiveTasks from './components/ActiveTasks'
import ClipEditor from './pages/ClipEditor'
import Dashboard from './pages/Dashboard'
import ProjectPage from './pages/ProjectPage'
import StickmanDirector from './pages/StickmanDirector'
import VideoMaker from './pages/VideoMaker'
import VoiceStudio from './pages/VoiceStudio'

function Header() {
  const [navOpen, setNavOpen] = useState(false)
  const { pathname } = useLocation()

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
          <NavLink to="/" end>Turn to Shorts</NavLink>
          <NavLink to="/voice-studio">Voice Studio</NavLink>
          <NavLink to="/video-maker">Video Maker</NavLink>
          <NavLink to="/director">Video Director</NavLink>
          <NavLink to="/stickman">Stickman Director</NavLink>
        </nav>
      </div>
      <ActiveTasks />
    </header>
  )
}

export default function App() {
  return (
    <>
      <Header />
      <main className="container">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
          <Route path="/clips/:id" element={<ClipEditor />} />
          <Route path="/voice-studio" element={<VoiceStudio />} />
          <Route path="/video-maker" element={<VideoMaker />} />
          <Route path="/director" element={<StickmanDirector mode="generic" />} />
          <Route path="/stickman" element={<StickmanDirector />} />
        </Routes>
      </main>
    </>
  )
}
