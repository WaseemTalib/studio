import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, formatEta, STAGE_LABEL, type ActiveTask, type TaskKind } from '../api'
import { DIRECTOR_STAGE } from '../directorApi'
import { MAKER_STAGE_LABEL } from '../makerApi'
import { usePolling } from '../usePolling'
import { LimitLine } from './LimitDialog'

const TOOLS: Record<TaskKind, { label: string; href: (id: string) => string }> = {
  shorts: { label: 'Turn to Shorts', href: (id) => `/projects/${id}` },
  'video-maker': { label: 'Video Maker', href: (id) => `/video-maker#video-${id}` },
  'video-director': { label: 'Video Director', href: (id) => `/director?plan=${id}` },
  'stickman-director': { label: 'Stickman Director', href: (id) => `/stickman?plan=${id}` },
}

const STAGES: Record<TaskKind, Record<string, string>> = {
  shorts: STAGE_LABEL,
  'video-maker': MAKER_STAGE_LABEL,
  'video-director': DIRECTOR_STAGE,
  'stickman-director': DIRECTOR_STAGE,
}

const TRACKED_TASKS_KEY = 'shortcut-running-tasks'
const taskKey = (task: Pick<ActiveTask, 'kind' | 'id'>) => `${task.kind}:${task.id}`

function loadTrackedTasks(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(TRACKED_TASKS_KEY) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

function saveTrackedTasks(tasks: Set<string>) {
  try {
    sessionStorage.setItem(TRACKED_TASKS_KEY, JSON.stringify([...tasks]))
  } catch {
    // Progress still works when session storage is unavailable; completion notices just will not survive a reload.
  }
}

function isResultPage(task: ActiveTask, pathname: string, search: string, hash: string): boolean {
  if (task.kind === 'shorts') return pathname === `/projects/${task.id}`
  if (task.kind === 'video-maker') return pathname === '/video-maker' && hash === `#video-${task.id}`
  const route = task.kind === 'video-director' ? '/director' : '/stickman'
  return pathname === route && new URLSearchParams(search).get('plan') === task.id
}

export default function ActiveTasks() {
  const location = useLocation()
  const [tasks, setTasks] = useState<ActiveTask[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const trackedTasks = useRef(loadTrackedTasks())

  const load = useCallback(() => {
    api.activeTasks().then((items) => {
      const available = new Set(items.map(taskKey))
      for (const task of items) if (task.status !== 'done') trackedTasks.current.add(taskKey(task))
      for (const key of trackedTasks.current) if (!available.has(key)) trackedTasks.current.delete(key)
      const visible = items.filter((task) => {
        if (task.status !== 'done') return true
        const key = taskKey(task)
        if (!trackedTasks.current.has(key)) return false
        if (!isResultPage(task, location.pathname, location.search, location.hash)) return true
        trackedTasks.current.delete(key)
        return false
      })
      saveTrackedTasks(trackedTasks.current)
      setTasks(visible)
      setError(null)
    }, (e: Error) => setError(e.message))
  }, [location.hash, location.pathname, location.search])

  useEffect(load, [load])
  usePolling(load, 1500, true)

  async function cancel(task: ActiveTask) {
    if (!confirm(`Cancel “${task.title}”? You can retry it from ${TOOLS[task.kind].label}.`)) return
    setBusy(`${task.kind}:${task.id}`)
    try {
      await api.cancelTask(task.kind, task.id)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  function dismissCompleted(task: ActiveTask) {
    trackedTasks.current.delete(taskKey(task))
    saveTrackedTasks(trackedTasks.current)
    setTasks((items) => items.filter((item) => taskKey(item) !== taskKey(task)))
  }

  if (!tasks.length && !error) return null

  const running = tasks.filter((task) => task.status !== 'done')
  const completed = tasks.length - running.length

  return (
    <aside className="active-tasks" aria-label="Tasks" aria-live="polite">
      <div className="active-tasks-inner">
        <div className="active-tasks-heading">
          <span className={running.length ? 'task-pulse' : 'task-check'}>{running.length ? '' : '✓'}</span>
          <strong>{running.length
            ? `${running.length} ${running.length === 1 ? 'task' : 'tasks'} running`
            : `${completed} completed ${completed === 1 ? 'task' : 'tasks'}`}</strong>
          <span>{running.length ? 'Tasks keep running when you change pages.' : 'Open a completed task to view its result.'}</span>
        </div>
        {error && <span className="active-tasks-error">Progress unavailable: {error}</span>}
        <div className="active-task-list">
          {tasks.map((task) => {
            const tool = TOOLS[task.kind]
            const done = task.status === 'done'
            const percent = done ? 100 : Math.round(task.progress * 100)
            const stage = done ? 'Completed' : task.cancelling ? 'Cancelling…'
              : task.stage ? STAGES[task.kind][task.stage] ?? task.stage : 'Queued'
            const eta = !done && !task.cancelling && task.status !== 'queued' && task.eta_seconds != null
              ? formatEta(task.eta_seconds, task.eta_scope) : null
            return (
              <article className={`active-task${done ? ' completed' : ''}`} key={`${task.kind}:${task.id}`}>
                <div className="active-task-main">
                  <span className="active-task-copy">
                    <span className="active-task-tool">{tool.label}</span>
                    <strong title={task.title}>{task.title}</strong>
                  </span>
                  <span className="active-task-progress">
                    <span className="active-task-bar" style={{ width: `${percent}%` }} />
                    <span>{stage} · {percent}%</span>
                  </span>
                  {task.limit && !task.cancelling
                    ? <span className="active-task-eta"><LimitLine state={task.limit} /></span>
                    : eta && <span className="active-task-eta">{eta}</span>}
                </div>
                <span className="active-task-actions">
                  <Link className="active-task-open" to={tool.href(task.id)}
                    onClick={() => done && dismissCompleted(task)}>{done ? 'View result' : 'View progress'} →</Link>
                  {!done && <button type="button" className="ghost danger"
                    disabled={busy === `${task.kind}:${task.id}` || task.cancelling}
                    onClick={() => cancel(task)}>{task.cancelling ? 'Stopping…' : 'Cancel'}</button>}
                </span>
              </article>
            )
          })}
        </div>
      </div>
    </aside>
  )
}
