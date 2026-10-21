import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, formatTime, isWorking, type Project } from '../api'
import NewProject from '../components/NewProject'
import ProjectStatus, { CancelButton } from '../components/ProjectStatus'
import { usePolling } from '../usePolling'

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => {
    api.listProjects().then(setProjects, (e: Error) => setError(e.message))
  }, [])

  useEffect(load, [load])
  const active = !!projects?.some(isWorking)
  usePolling(load, 2000, active)

  async function remove(p: Project) {
    if (!confirm(`Delete “${p.title}” and all its clips?`)) return
    try {
      await api.deleteProject(p.id)
      load()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  return (
    <>
      {creating ? (
        <div className="tool-page">
          <div className="tool-page-head">
            <div className="tool-page-copy">
              <h1>Turn long videos into ready-to-post shorts</h1>
              <p className="muted">Paste a link or upload a video. AI finds the best moments, cuts on clean sentence
                boundaries, frames the speakers and adds captions.</p>
            </div>
            <button type="button" onClick={() => setCreating(false)}>← Projects</button>
          </div>
          <NewProject onCreated={(p) => {
            setProjects((list) => [p, ...(list ?? [])])
            setCreating(false)
          }} />
        </div>
      ) : <section className="projects first-load-projects">
        <div className="section-head">
          <h2>Projects</h2>
          {projects && projects.length > 0 && <span className="muted small">{projects.length} total</span>}
          <button type="button" className="primary" onClick={() => setCreating(true)}>+ Create project</button>
        </div>
        {error && <p className="error">Could not reach the API: {error}</p>}
        {projects === null && !error && <p className="muted">Loading…</p>}
        {projects?.length === 0 && (
          <div className="empty">No projects yet. Create one to get your first clips.</div>
        )}
        <div className="grid">
          {projects?.map((p) => (
            <article key={p.id} className="card">
              <Link to={`/projects/${p.id}`} className="card-media">
                {p.thumbnail ? <img src={p.thumbnail} alt="" /> : <div className="placeholder" />}
                {p.duration != null && <span className="duration">{formatTime(p.duration)}</span>}
              </Link>
              <div className="card-body">
                <Link to={`/projects/${p.id}`} className="card-title" title={p.title}>{p.title}</Link>
                <span className="muted small">
                  {new Date(p.created_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                  {' · '}{p.options.mode === 'full' ? 'Full video' : `${p.options.aspect} clips`}
                </span>
                <div className="card-meta">
                  <ProjectStatus project={p} />
                  <span className="card-actions">
                    <CancelButton project={p} className="link danger" onError={alert}
                      onCancelled={(u) => setProjects((list) => list?.map((x) => (x.id === u.id ? u : x)) ?? null)} />
                    {(p.status === 'failed' || p.status === 'cancelled') && (
                      <button className="link" onClick={() => api.retryProject(p.id).then(load, (e: Error) => alert(e.message))}>
                        Retry
                      </button>
                    )}
                    {!isWorking(p) && (
                      <button className="link danger" onClick={() => remove(p)}>Delete</button>
                    )}
                  </span>
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>}
    </>
  )
}
