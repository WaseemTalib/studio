import { useState } from 'react'
import { api, formatEta, STAGE_LABEL, type Project } from '../api'

export default function ProjectStatus({ project }: { project: Project }) {
  if (project.status === 'failed') return <span className="badge failed" title={project.error ?? ''}>Failed</span>
  if (project.status === 'cancelled') return <span className="badge">Cancelled</span>
  if (project.status === 'done') return <span className="badge done">{project.clip_count} clips</span>
  const label = project.cancelling ? 'Cancelling…' : project.stage ? STAGE_LABEL[project.stage] : 'Queued'
  const percent = Math.round(project.progress * 100)
  const eta = project.cancelling || project.status === 'queued' ? null
    : project.eta_seconds == null ? 'estimating time…' : formatEta(project.eta_seconds, project.eta_scope)
  return (
    <span className="progress-wrap">
      <span className="progress" title={label}>
        <span className="progress-bar" style={{ width: `${percent}%` }} />
        <span className="progress-label">{label} · {percent}%</span>
      </span>
      {eta && <span className="progress-eta">{eta}</span>}
    </span>
  )
}

/** Stops a queued or running project; the download and transcript are kept for a retry. */
export function CancelButton({ project, onCancelled, onError, className }: {
  project: Project
  onCancelled: (p: Project) => void
  onError: (message: string) => void
  className?: string
}) {
  const [busy, setBusy] = useState(false)
  if (project.status !== 'queued' && project.status !== 'processing') return null

  async function cancel() {
    if (!confirm(`Stop processing “${project.title}”? You can retry later; the downloaded video and transcript are kept.`)) return
    setBusy(true)
    try {
      onCancelled(await api.cancelProject(project.id))
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button className={className} onClick={cancel} disabled={busy || project.cancelling}>
      {project.cancelling ? 'Cancelling…' : 'Cancel'}
    </button>
  )
}
