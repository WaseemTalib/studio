import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, canSwitchLimit, formatTime, isWorking, type Clip, type Project } from '../api'
import { layoutLabel } from '../components/LookControls'
import { LimitDialog, LimitLine } from '../components/LimitDialog'
import ProjectStatus, { CancelButton } from '../components/ProjectStatus'
import { usePolling } from '../usePolling'

export default function ProjectPage() {
  const { id = '' } = useParams()
  const [project, setProject] = useState<Project | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<'score' | 'time'>('score')
  const [limitOpen, setLimitOpen] = useState(false)  // reopened while waiting, to switch instead

  const load = useCallback(async () => {
    try {
      const p = await api.getProject(id)
      setProject(p)
      if (p.status === 'done') setClips(await api.listClips(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])
  usePolling(load, 2000, (!!project && isWorking(project)) || clips.some((c) => c.status === 'rendering'))

  // Rank follows score (the API order); sorting by time keeps each clip's rank.
  const ranked = useMemo(() => clips.map((clip, n) => ({ clip, rank: n + 1 })), [clips])
  const shown = sort === 'time' ? [...ranked].sort((a, b) => a.clip.start - b.clip.start) : ranked

  if (error && !project) return <p className="error">{error}</p>
  if (!project) return <p className="muted">Loading…</p>

  const o = project.options
  const ready = clips.filter((c) => c.status !== 'rendering')
  const working = isWorking(project)

  return (
    <>
      <Link to="/" className="back">← All projects</Link>

      <header className="project-head">
        <div className="project-title">
          <ProjectName project={project} onRenamed={setProject} />
          <div className="meta-chips">
            <span>{project.source_type === 'url' ? new URL(project.source).hostname.replace('www.', '') : 'Upload'}</span>
            {project.duration != null && <span>{formatTime(project.duration)}</span>}
            <span>{o.aspect}</span>
            {o.mode === 'full' ? <span>Full video</span> : <span>{o.min_seconds}–{o.max_seconds}s clips</span>}
            {o.topics?.length ? <span>Topics: {o.topics.join(', ')}</span> : null}
          </div>
        </div>
        <div className="project-actions">
          <ProjectStatus project={project} />
          <CancelButton project={project} onCancelled={setProject} onError={setError} />
          {project.status === 'done' && (
            <a className={`button primary ${ready.length ? '' : 'disabled'}`} href={`/api/projects/${project.id}/download`}
              title="Download every clip as a .zip, with titles and hashtags">
              ⬇ Download all ({ready.length})
            </a>
          )}
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {project.status === 'failed' && (
        <div className="notice error-notice">
          <p>{project.error}</p>
          <button onClick={() => api.retryProject(project.id).then(setProject, (e: Error) => setError(e.message))}>
            Retry
          </button>
        </div>
      )}
      {project.status === 'cancelled' && (
        <div className="notice">
          <p>Processing was cancelled. Retrying reuses the downloaded video and transcript.</p>
          <button onClick={() => api.retryProject(project.id).then(setProject, (e: Error) => setError(e.message))}>
            Retry
          </button>
        </div>
      )}
      {working && project.limit && canSwitchLimit(project.limit) && (
        <div className="notice limit-notice">
          <p><LimitLine state={project.limit} /></p>
          <button onClick={() => setLimitOpen(true)}>Switch model now…</button>
        </div>
      )}
      {working && !project.limit && (
        <div className="notice">
          <p>Working on it — this page updates automatically. Long videos can take several minutes.</p>
        </div>
      )}
      {working && project.limit && !project.cancelling
        && (project.limit.mode === 'ask' || (canSwitchLimit(project.limit) && limitOpen)) && (
        <LimitDialog state={project.limit} onClose={() => setLimitOpen(false)}
          onChoose={async (action, option) => setProject(await api.limitChoice(project.id, action, option))}
          onCancel={async () => setProject(await api.cancelProject(project.id))}
          cancelPrompt="Cancel this project? The downloaded video and transcript are kept for a retry." />
      )}

      {clips.length > 0 && (
        <div className="clips-toolbar">
          <span><strong>{clips.length}</strong> <span className="muted">{clips.length === 1 ? 'clip' : 'clips'}</span></span>
          <div className="segmented">
            <button type="button" className={sort === 'score' ? 'on' : ''} onClick={() => setSort('score')}>Best score</button>
            <button type="button" className={sort === 'time' ? 'on' : ''} onClick={() => setSort('time')}>Timeline</button>
          </div>
        </div>
      )}

      <div className={`clip-list aspect-${o.aspect.replace(':', 'x')}`}>
        {shown.map(({ clip, rank }) => (
          <ClipCard key={clip.id} clip={clip} rank={rank}
            onChange={(u) => setClips((cs) => cs.map((x) => (x.id === u.id ? u : x)))} />
        ))}
      </div>
    </>
  )
}

function ProjectName({ project, onRenamed }: { project: Project; onRenamed: (p: Project) => void }) {
  const [name, setName] = useState(project.title)
  useEffect(() => setName(project.title), [project.title])

  async function save() {
    const next = name.trim()
    if (!next || next === project.title) return setName(project.title)
    try {
      onRenamed(await api.renameProject(project.id, next))
    } catch {
      setName(project.title)
    }
  }

  return (
    <input
      className="project-name"
      value={name}
      maxLength={120}
      aria-label="Project name"
      title="Click to rename"
      onChange={(e) => setName(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') { setName(project.title); (e.target as HTMLInputElement).blur() }
      }}
    />
  )
}

function ClipCard({ clip, rank, onChange }: { clip: Clip; rank: number; onChange: (c: Clip) => void }) {
  const [title, setTitle] = useState(clip.title)
  const [copied, setCopied] = useState<'title' | 'description' | 'hashtags' | null>(null)
  useEffect(() => setTitle(clip.title), [clip.title])

  async function saveTitle() {
    const next = title.trim()
    if (!next || next === clip.title) return setTitle(clip.title)
    onChange(await api.updateClip(clip.id, { title: next }))
  }

  const hashtagText = clip.hashtags.map((h) => `#${h}`).join(' ')
  // Ready-to-post description: the hook (or the title when there is none) followed by the hashtags.
  const description = [clip.hook || title.trim() || clip.title, hashtagText].filter(Boolean).join('\n\n')

  function copy(what: 'title' | 'description' | 'hashtags') {
    const text = what === 'title' ? title.trim() || clip.title : what === 'description' ? description : hashtagText
    navigator.clipboard.writeText(text).then(() => {
      setCopied(what)
      setTimeout(() => setCopied(null), 1500)
    })
  }

  const scoreClass = clip.score >= 80 ? 'high' : clip.score >= 60 ? 'mid' : 'low'
  const isFull = clip.reason === 'Full video'

  return (
    <article className="clip-row">
      <div className="clip-player">
        <video key={clip.video} src={clip.video} poster={clip.thumbnail} controls preload="none" playsInline />
        {clip.status === 'rendering' && <div className="player-overlay">Rendering…</div>}
        <span className="rank">#{rank}</span>
      </div>

      <div className="clip-info">
        <div className="clip-info-top">
          {!isFull && (
            <div className={`virality ${scoreClass}`} title="Predicted virality score">
              <strong>{(clip.score / 10).toFixed(1)}</strong><span>/10</span>
            </div>
          )}
          <div className="clip-facts">
            <span>{formatTime(clip.start)} – {formatTime(clip.end)}</span>
            <span>{Math.round(clip.end - clip.start)}s</span>
            {clip.layout && <span>{layoutLabel(clip.layout)}</span>}
            {clip.topic && <span className="topic">{clip.topic}</span>}
          </div>
        </div>

        <div className="clip-title-row">
          <input className="clip-title" value={title} aria-label="Clip title" onChange={(e) => setTitle(e.target.value)}
            onBlur={saveTitle} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
        </div>

        <div className="clip-actions">
          <Link className="button primary" to={`/clips/${clip.id}`}>✎ Edit clip</Link>
          <button onClick={() => copy('title')}>{copied === 'title' ? '✓ Copied' : '⧉ Copy title'}</button>
          <button onClick={() => copy('description')} title={description}>
            {copied === 'description' ? '✓ Copied' : '¶ Copy description'}
          </button>
          <button onClick={() => copy('hashtags')} disabled={!clip.hashtags.length}>
            {copied === 'hashtags' ? '✓ Copied' : '# Copy hashtags'}
          </button>
        </div>

        {clip.hashtags.length > 0 && (
          <div className="hashtags">{clip.hashtags.map((h) => <span key={h}>#{h}</span>)}</div>
        )}

        {clip.hook && (
          <div className="clip-section">
            <h5>Hook</h5>
            <p className="hook">“{clip.hook}”</p>
          </div>
        )}
        {!isFull && clip.reason && (
          <div className="clip-section">
            <h5>Why this clip</h5>
            <p>{clip.reason}</p>
          </div>
        )}
        <details className="clip-section">
          <summary>Transcript</summary>
          <p className="transcript">{clip.transcript}</p>
        </details>
      </div>
    </article>
  )
}
