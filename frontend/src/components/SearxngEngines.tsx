import { useCallback, useEffect, useState } from 'react'
import { makerApi, type SearxngStatus } from '../makerApi'

/** The SearXNG instance's health, and which of its engines the SearXNG sources search. */
export default function SearxngEngines({ images, videos, value, onChange }: {
  images: boolean
  videos: boolean
  /** Chosen engines; empty uses the instance's defaults. */
  value: string[]
  onChange: (engines: string[]) => void
}) {
  const [status, setStatus] = useState<SearxngStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const check = useCallback(() => {
    setChecking(true)
    makerApi.searxngStatus()
      .then((s) => { setStatus(s); setError(null) }, (e: Error) => setError(e.message))
      .finally(() => setChecking(false))
  }, [])
  useEffect(check, [check])

  const ok = !!status && status.json && !status.error
  const message = error ?? (!status ? 'Checking SearXNG…' : ok
    ? `Connected${status.version ? ` · v${status.version}` : ''} · ${status.url}` : status.error)
  const toggle = (name: string) => onChange(value.includes(name) ? value.filter((e) => e !== name) : [...value, name])
  const groups = ([['Image engines', images, status?.engines.images], ['Video engines', videos, status?.engines.videos]] as const)
    .filter(([, shown, names]) => shown && names?.length)

  return (
    <div className="searxng-panel">
      <div className="searxng-status">
        <span className={`status-dot ${!status && !error ? 'wait' : ok ? 'ok' : 'bad'}`} />
        <span className="small">{message}</span>
        <button type="button" className="chip-button" onClick={check} disabled={checking}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
      </div>
      {ok && groups.map(([label, , names]) => (
        <div key={label} className="searxng-engines">
          <span className="muted small">{label} <em>(none ticked: the instance’s defaults)</em></span>
          <div className="engine-list">
            {names!.map((name) => (
              <button type="button" key={name} className={`engine-chip ${value.includes(name) ? 'on' : ''}`}
                aria-pressed={value.includes(name)} onClick={() => toggle(name)}>{name}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
