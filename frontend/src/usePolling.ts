import { useEffect, useRef } from 'react'

/** Run `fn` now and every `ms` while `active` is true. */
export function usePolling(fn: () => void, ms: number, active: boolean) {
  const saved = useRef(fn)
  saved.current = fn
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => saved.current(), ms)
    return () => clearInterval(id)
  }, [ms, active])
}
