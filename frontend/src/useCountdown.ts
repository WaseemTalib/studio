import { useEffect, useState } from 'react'

/** Counts down from `seconds` (as last reported by the server), once per second. */
export function useCountdown(seconds: number | null | undefined): number | null {
  const [left, setLeft] = useState(seconds ?? null)
  useEffect(() => {
    if (seconds == null) {
      setLeft(null)
      return
    }
    const deadline = Date.now() + seconds * 1000
    setLeft(seconds)
    const id = setInterval(() => setLeft(Math.max(0, (deadline - Date.now()) / 1000)), 1000)
    return () => clearInterval(id)
  }, [seconds])
  return left
}

export function formatWait(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}
