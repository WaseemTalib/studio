import { useState } from 'react'
import type { LimitInfo, LimitState } from '../api'
import { formatWait, useCountdown } from '../useCountdown'

/** "200,000 tokens", "7,200 audio seconds (2h 00m)". */
function amount(value: number | null, code: string | null): string {
  if (value == null) return '—'
  const n = Math.round(value).toLocaleString()
  if (code?.startsWith('AS')) return `${n} audio seconds (${formatWait(value)})`
  if (code?.startsWith('R')) return `${n} requests`
  if (code?.includes('T')) return `${n} tokens`
  return n
}

const STAGE: Record<string, string> = {
  transcribe: 'Transcription', highlights: 'Finding highlights', script: 'Writing the script',
  captions: 'Caption timing', footage: 'Finding stock footage',
}

function LimitFacts({ info, left }: { info: LimitInfo; left: number | null }) {
  const r = info.recent
  const resetAt = left != null ? new Date(Date.now() + left * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : null
  return (
    <dl className="limit-facts">
      <dt>Service</dt><dd>{info.service_label} · {info.model}</dd>
      <dt>Limit reached</dt><dd>{info.code ? `${info.name} (${info.code})` : 'rate limit'}</dd>
      {info.limit != null && <><dt>Allowance</dt><dd>{amount(info.limit, info.code)}</dd></>}
      {info.used != null && <><dt>Used</dt><dd>{amount(info.used, info.code)}</dd></>}
      {info.remaining != null && <><dt>Remaining</dt><dd>{amount(info.remaining, info.code)}</dd></>}
      {info.requested != null && <><dt>This request needs</dt><dd>{amount(info.requested, info.code)}</dd></>}
      <dt>Frees up in</dt>
      <dd><strong>{left != null ? formatWait(left) : '—'}</strong>{resetAt && <span className="muted"> (around {resetAt})</span>}</dd>
      {r?.requests_left_today != null && r.requests_per_day != null && (
        <><dt>Requests left today</dt><dd>{r.requests_left_today.toLocaleString()} of {r.requests_per_day.toLocaleString()}
          {r.requests_reset_in != null && <span className="muted"> · resets in {formatWait(r.requests_reset_in)}</span>}</dd></>
      )}
      {r?.tokens_left_this_minute != null && r.tokens_per_minute != null && (
        <><dt>Tokens left this minute</dt><dd>{r.tokens_left_this_minute.toLocaleString()} of {r.tokens_per_minute.toLocaleString()}</dd></>
      )}
    </dl>
  )
}

/** Asks what to do when a usage limit is reached: wait for it to free up, or switch.
 *  `onChoose` sends the answer; `onCancel` stops the job (omit it where there is no job). */
export function LimitDialog({ state, onChoose, onCancel, cancelPrompt, onClose }: {
  state: LimitState
  onChoose: (action: 'wait' | 'switch', option?: string) => Promise<void>
  onCancel?: () => Promise<void>
  cancelPrompt?: string
  onClose?: () => void
}) {
  const left = useCountdown(state.limit.resets_in)
  const [pick, setPick] = useState<string>('wait')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const waiting = state.mode === 'wait'

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await onChoose(pick === 'wait' ? 'wait' : 'switch', pick === 'wait' ? undefined : pick)
      onClose?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function cancelJob() {
    if (!onCancel || (cancelPrompt && !confirm(cancelPrompt))) return
    try {
      await onCancel()
      onClose?.()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="vs-modal-backdrop" onClick={waiting ? onClose : undefined}>
      <div className="vs-modal limit-dialog" role="dialog" aria-label="Usage limit reached" onClick={(e) => e.stopPropagation()}>
        <div className="vs-modal-head">
          <h2>{state.limit.service_label} usage limit reached</h2>
          {waiting && <button type="button" className="ghost" onClick={onClose} aria-label="Close">✕</button>}
        </div>
        <p className="muted small">
          {STAGE[state.stage ?? ''] ?? 'Processing'} is paused. Nothing switches until you choose{onCancel ? '; finished work is kept' : ''}.
        </p>
        <LimitFacts info={state.limit} left={left} />

        <div className="limit-options" role="radiogroup">
          <label className={`limit-option ${pick === 'wait' ? 'on' : ''}`}>
            <input type="radio" name="limit-choice" checked={pick === 'wait'} onChange={() => setPick('wait')} />
            <span>
              <strong>{waiting ? 'Keep waiting' : 'Wait until it frees up'}</strong>
              <span className="muted small">
                Continues with {state.limit.model} automatically{left != null ? ` in about ${formatWait(left)}` : ''}.
              </span>
            </span>
          </label>
          {state.options.map((o) => (
            <label key={o.id} className={`limit-option ${pick === o.id ? 'on' : ''} ${o.available ? '' : 'disabled'}`}>
              <input type="radio" name="limit-choice" disabled={!o.available} checked={pick === o.id} onChange={() => setPick(o.id)} />
              <span>
                <strong>Switch to {o.label}</strong>
                <span className="muted small">{o.detail}</span>
                {!o.available && o.reason && <span className="small limit-reason">{o.reason}</span>}
              </span>
            </label>
          ))}
        </div>

        {error && <p className="error small">{error}</p>}
        <div className="limit-actions">
          {onCancel ? <button type="button" className="link danger" onClick={cancelJob}>Cancel</button> : <span />}
          <button type="button" className="primary" disabled={busy} onClick={submit}>
            {pick === 'wait' ? (waiting ? 'Keep waiting' : 'Wait') : 'Switch and continue'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** One line for progress displays: what limit the job is waiting on, with a countdown. */
export function LimitLine({ state }: { state: LimitState }) {
  const left = useCountdown(state.seconds_left ?? state.limit.resets_in)
  const l = state.limit
  if (state.mode === 'ask') return <span className="limit-line needs">Needs your choice: {l.service_label} {l.name} limit reached</span>
  const rest = left != null ? formatWait(left) : '…'
  if (state.mode === 'wait') return <span className="limit-line">Waiting for {l.service_label}'s {l.name} limit · continues in {rest}</span>
  const tokens = l.remaining != null && l.limit != null ? ` (${Math.round(l.remaining).toLocaleString()} of ${Math.round(l.limit).toLocaleString()} left)` : ''
  return <span className="limit-line">Waiting {rest} for {l.service_label}'s {l.name} limit{tokens}</span>
}
