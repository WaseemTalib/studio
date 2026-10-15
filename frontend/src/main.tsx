import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import './index.css'
import ClipEditor from './pages/ClipEditor'
import Dashboard from './pages/Dashboard'
import ProjectPage from './pages/ProjectPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            <span className="brand-mark">▶</span> Shortcut Studio
          </Link>
          <nav className="nav">
            <NavLink to="/" end>Projects</NavLink>
          </nav>
          <a href="/#new" className="button primary small-btn">+ New project</a>
        </div>
      </header>
      <main className="container">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
          <Route path="/clips/:id" element={<ClipEditor />} />
        </Routes>
      </main>
    </BrowserRouter>
  </StrictMode>,
)
