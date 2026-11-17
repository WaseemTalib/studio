import { useEffect, useRef, useState } from 'react'
import { onToast, type Toast } from '../toast'

function ToastItem({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const [paused, setPaused] = useState(false)
  const left = useRef(toast.duration)
  const [seconds, setSeconds] = useState(Math.ceil(toast.duration / 1000))
  const close = useRef(onClose)
  close.current = onClose

  // Count down while the pointer is not over the toast; close at zero.
  useEffect(() => {
    if (paused) return
    let last = Date.now()
    const id = setInterval(() => {
      const now = Date.now()
      left.current -= now - last
      last = now
      setSeconds(Math.max(0, Math.ceil(left.current / 1000)))
      if (left.current <= 0) close.current()
    }, 100)
    return () => clearInterval(id)
  }, [paused])

  return (
    <div className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <span className="toast-icon" aria-hidden="true">{toast.kind === 'error' ? '!' : toast.kind === 'info' ? 'i' : '✓'}</span>
      <span className="toast-message">{toast.message}</span>
      <span className="toast-timer" title="Closes automatically">{seconds}s</span>
      <button type="button" className="toast-close" aria-label="Dismiss" onClick={onClose}>✕</button>
      <span className="toast-bar" style={{ animationDuration: `${toast.duration}ms`,
        animationPlayState: paused ? 'paused' : 'running' }} />
    </div>
  )
}

/** Stack of toasts in the corner of the screen. */
export default function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([])
  useEffect(() => onToast((t) => setToasts((list) => [...list.slice(-3), t])), [])
  return (
    <div className="toaster" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onClose={() => setToasts((list) => list.filter((x) => x.id !== t.id))} />
      ))}
    </div>
  )
}
