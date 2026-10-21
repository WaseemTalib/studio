import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { formatEta } from '../api'
import { rememberedService, useAIServices } from '../aiServices'
import AIModelSelect from '../components/AIModelSelect'
import { LimitDialog, LimitLine } from '../components/LimitDialog'
import {
  DIRECTOR_STAGE, directorApi, DURATIONS, formatSeconds, isWriting, PLAN_LANGUAGES, videoDirectorApi,
  type Check, type DirectorApi, type DirectorSettings, type DirectorStyle, type Package, type Plan, type Proposal,
} from '../directorApi'
import { usePolling } from '../usePolling'

const BLANK: Omit<DirectorSettings, 'aspect' | 'style'> & { aspect: DirectorSettings['aspect'] | null; style: string | null } = {
  source_kind: 'topic', source: '', url: null, aspect: null, seconds: 60, style: null, faceless: false,
  language: 'English', voice: 'suggest', voice_custom: '', tone: '', ai_service: null,
}
type Draft = typeof BLANK

const KINDS: { id: DirectorSettings['source_kind']; label: string; placeholder: string }[] = [
  { id: 'topic', label: 'Topic', placeholder: 'e.g. Why overthinking makes small tasks feel impossible' },
  { id: 'script', label: 'Script', placeholder: 'Paste your copy or notes; the meaning is kept and rewritten for narration' },
  { id: 'article', label: 'Article', placeholder: 'Paste an article, or give its link below' },
]
const VOICES: { id: DirectorSettings['voice']; label: string }[] = [
  { id: 'suggest', label: 'Suggest one for this video' },
  { id: 'male', label: 'Adult male (tested profile)' },
  { id: 'female', label: 'Adult female, bright' },
  { id: 'custom', label: 'Describe my own…' },
]

export default function StickmanDirector({ mode = 'stickman' }: { mode?: 'stickman' | 'generic' }) {
  const generic = mode === 'generic'
  const api = generic ? videoDirectorApi : directorApi
  const [params, setParams] = useSearchParams()
  const selected = params.get('plan')
  const [creating, setCreating] = useState(false)
  const [styles, setStyles] = useState<DirectorStyle[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const services = useAIServices()

  const loadPlans = useCallback(() => api.plans().then(setPlans, (e: Error) => setError(e.message)), [api])
  const loadPlan = useCallback(() => {
    if (selected) api.get(selected).then(setPlan, (e: Error) => setError(e.message))
  }, [api, selected])
  useEffect(() => {
    api.options().then((o) => setStyles(o.styles), (e: Error) => setError(e.message))
    loadPlans()
  }, [api, loadPlans])
  useEffect(() => {
    setPlan(null)
    loadPlan()
  }, [loadPlan])
  usePolling(() => { loadPlan(); loadPlans() }, 1500, !!plan && isWriting(plan))

  const open = (id: string | null) => {
    setCreating(false)
    setParams(id ? { plan: id } : {})
  }
  const startProject = () => {
    setPlan(null)
    setParams({})
    setCreating(true)
  }
  const update = (p: Plan) => {
    setPlan(p)
    loadPlans()
  }

  const planList = (
    <>
      <div className="section-title-row">
        <strong>Projects</strong>
        {(selected || creating) && <button className="small-btn" onClick={startProject}>+ Create project</button>}
      </div>
      {!plans.length && <p className="muted small">No projects yet. Create one to write your first plan.</p>}
      <ul className="plan-list">
        {plans.map((p) => (
          <li key={p.id} className={p.id === selected ? 'on' : ''}>
            <button type="button" className="plan-item" onClick={() => open(p.id)}>
              <strong>{p.title}</strong>
              <span className="muted small">
                {styles.find((s) => s.id === p.settings.style)?.short ?? p.settings.style} · {p.settings.aspect} · {formatSeconds(p.settings.seconds)}
                {' · '}{isWriting(p) ? 'writing…' : p.status === 'failed' ? 'failed' : p.has_package ? 'prompts ready' : p.approved ? 'approved' : 'proposal'}
              </span>
            </button>
            {!isWriting(p) && (
              <button type="button" className="ghost danger" aria-label="Delete plan"
                onClick={() => confirm(`Delete “${p.title}”?`) && api.remove(p.id).then(() => {
                  if (p.id === selected) open(null)
                  loadPlans()
                }, (e: Error) => setError(e.message))}>✕</button>
            )}
          </li>
        ))}
      </ul>
    </>
  )

  return (
    <div className="director">
      <div className="tool-page-head">
        <div className="tool-page-copy">
          <h1>{generic ? 'Video Director' : 'Stickman Director'}</h1>
          {generic ? (
            <p className="muted">Turn a topic, script or article into a production-ready plan for real people and
              real-world scenes, then create consistent Google Flow prompts for every ten-second clip.</p>
          ) : (
            <p className="muted">Turn a topic, script or article into a stickman animation plan: a director’s proposal to
              review, then ready-to-paste Google Flow (Omni Flash) prompts for every ten-second clip.{' '}
              <a href="https://github.com/kaomei/stickman-video-director" target="_blank" rel="noreferrer">Based on Stickman Video Director</a> (MIT).</p>
          )}
        </div>
        <button type="button" className={!selected && !creating ? 'primary' : ''}
          onClick={() => selected || creating ? open(null) : startProject()}>
          {selected || creating ? '← Projects' : '+ Create project'}
        </button>
      </div>
      {error && <div className="notice error-notice"><p>{error}</p><button onClick={() => setError(null)}>Dismiss</button></div>}

      {!selected && !creating ? (
        <section className="panel side-card tool-projects first-load-projects">
          {planList}
        </section>
      ) : <div className="workspace">
        <div className="workspace-main">
          {selected && plan ? (
            <PlanView key={plan.id} plan={plan} styles={styles} services={services} api={api} generic={generic}
              onUpdate={update} onError={setError}
              onClose={() => open(null)} />
          ) : selected ? (
            <p className="muted">Loading…</p>
          ) : creating ? (
            <section className="panel section">
              <h3><span className="step">1</span> New {generic ? 'real-world' : 'stickman'} video plan</h3>
              <PlanForm styles={styles} services={services} mode={mode} submitLabel="Write the director’s proposal"
                onSubmit={async (s) => {
                  const p = await api.create(s)
                  loadPlans()
                  open(p.id)
                }} onError={setError} />
            </section>
          ) : null}
        </div>
        <aside className="workspace-side">
          <div className="panel side-card">
            {planList}
          </div>
        </aside>
      </div>}
    </div>
  )
}

function PlanForm({ styles, services, mode, initial, submitLabel, onSubmit, onError, onCancel }: {
  styles: DirectorStyle[]
  services: ReturnType<typeof useAIServices>
  mode: 'stickman' | 'generic'
  initial?: DirectorSettings
  submitLabel: string
  onSubmit: (s: DirectorSettings) => Promise<void>
  onError: (m: string) => void
  onCancel?: () => void
}) {
  const [d, setD] = useState<Draft>(() => initial ?? { ...BLANK, ai_service: rememberedService() })
  const [custom, setCustom] = useState(!DURATIONS.includes(initial?.seconds ?? 60))
  const [busy, setBusy] = useState(false)
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }))
  const kind = KINDS.find((k) => k.id === d.source_kind)!
  const missing = [!d.source.trim() && !d.url && 'a topic, script or article', !d.aspect && 'an aspect ratio',
    !d.style && 'a style'].filter(Boolean) as string[]

  async function submit() {
    if (missing.length) return
    setBusy(true)
    try {
      await onSubmit({ ...d, mode, aspect: d.aspect!, style: d.style!, url: d.url?.trim() || null })
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="director-form">
      {!initial && (
        <>
          <div className="segmented mode">
            {KINDS.map((k) => (
              <button type="button" key={k.id} className={d.source_kind === k.id ? 'on' : ''} onClick={() => set('source_kind', k.id)}>{k.label}</button>
            ))}
          </div>
          <textarea rows={d.source_kind === 'topic' ? 3 : 9} value={d.source} placeholder={kind.placeholder}
            onChange={(e) => set('source', e.target.value)} />
          {d.source_kind === 'article' && (
            <label className="field">
              <span>Article link <em className="muted">(optional)</em></span>
              <input type="url" placeholder="https://…" value={d.url ?? ''} onChange={(e) => set('url', e.target.value || null)} />
            </label>
          )}
        </>
      )}

      <div className="field">
        <span>Aspect ratio</span>
        <div className="segmented">
          {(['16:9', '9:16'] as const).map((a) => (
            <button type="button" key={a} className={d.aspect === a ? 'on' : ''} onClick={() => set('aspect', a)}>
              {a === '16:9' ? '16:9 landscape' : '9:16 vertical'}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span>Length <em className="muted">(ten-second clips)</em></span>
        <div className="segmented wrap">
          {DURATIONS.map((s) => (
            <button type="button" key={s} className={!custom && d.seconds === s ? 'on' : ''}
              onClick={() => { setCustom(false); set('seconds', s) }}>{formatSeconds(s)} · {s / 10} clips</button>
          ))}
          <button type="button" className={custom ? 'on' : ''} onClick={() => setCustom(true)}>Custom</button>
        </div>
        {custom && (
          <span className="row">
            <input type="number" min={10} max={300} step={10} value={d.seconds}
              onChange={(e) => set('seconds', Math.max(10, Math.min(300, Math.round(Number(e.target.value) / 10) * 10 || 10)))} />
            <span className="muted small">seconds = {Math.round(d.seconds / 10)} clips (rounded to tens, up to 5 min)</span>
          </span>
        )}
      </div>

      <div className="field">
        <span>Visual style</span>
        <div className="director-styles">
          {styles.map((s) => (
            <button type="button" key={s.id} className={`director-style ${d.style === s.id ? 'on' : ''}`} onClick={() => set('style', s.id)}>
              <span className={`director-swatch s-${s.id}`} aria-hidden="true">
                {mode === 'generic' ? <RealWorldFrame /> : <StickFigure />}
              </span>
              <strong>{s.name.replace(/^Style /, '')}</strong>
              <small>{s.best_for}</small>
            </button>
          ))}
        </div>
        {mode === 'stickman' && (d.style === '1-light' || d.style === '1-dark') && (
          <label className="field check">
            <input type="checkbox" checked={d.faceless} onChange={(e) => set('faceless', e.target.checked)} />
            Faceless figure <em className="muted">(no line expressions at all)</em>
          </label>
        )}
      </div>

      <div className="row wrap">
        <label className="field">
          <span>Narrator</span>
          <select value={d.voice} onChange={(e) => set('voice', e.target.value as DirectorSettings['voice'])}>
            {VOICES.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        </label>
        {d.voice === 'custom' && (
          <label className="field grow">
            <span>Voice description</span>
            <input value={d.voice_custom} placeholder="e.g. older British woman, calm and wise" onChange={(e) => set('voice_custom', e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Plan &amp; translation language</span>
          <select value={d.language} onChange={(e) => set('language', e.target.value)}>
            {PLAN_LANGUAGES.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <AIModelSelect services={services} value={d.ai_service} onChange={(v) => set('ai_service', v)} />
      </div>
      <label className="field">
        <span>Tone <em className="muted">(optional)</em></span>
        <input value={d.tone} placeholder="e.g. playful, serious, inspiring" onChange={(e) => set('tone', e.target.value)} />
      </label>
      <p className="muted small">Narration is always English; the plan is written in the chosen language with a reference translation.</p>

      <div className="row">
        <button className="primary" disabled={busy || missing.length > 0} onClick={submit}>{busy ? 'Starting…' : submitLabel}</button>
        {onCancel && <button className="link" onClick={onCancel}>Cancel</button>}
        {missing.length > 0 && <span className="muted small">Choose {missing.join(', ')}.</span>}
      </div>
    </div>
  )
}

function StickFigure() {
  return (
    <svg viewBox="0 0 40 40" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
      <circle cx="20" cy="9" r="5" />
      <path d="M20 14v12M20 18l-8 5M20 18l8-6M20 26l-6 9M20 26l6 9" />
    </svg>
  )
}

function RealWorldFrame() {
  return (
    <svg viewBox="0 0 52 34" width="46" height="30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="1" y="1" width="50" height="32" rx="4" />
      <circle cx="21" cy="12" r="4" />
      <path d="M14 27c1-6 4-9 7-9s6 3 7 9M32 11h12M32 16h9M32 21h11" />
    </svg>
  )
}

function PlanView({ plan, styles, services, api, generic, onUpdate, onError, onClose }: {
  plan: Plan
  styles: DirectorStyle[]
  services: ReturnType<typeof useAIServices>
  api: DirectorApi
  generic: boolean
  onUpdate: (p: Plan) => void
  onError: (m: string) => void
  onClose: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [limitOpen, setLimitOpen] = useState(false)
  const writing = isWriting(plan)
  const s = plan.settings
  const style = styles.find((x) => x.id === s.style)
  const call = (p: Promise<Plan>) => p.then(onUpdate, (e: Error) => onError(e.message))
  const percent = Math.round(plan.progress * 100)

  return (
    <>
      <section className="panel section">
        <div className="section-title-row">
          <h3>{plan.proposal?.title_en ?? plan.title}</h3>
          <button className="link" onClick={onClose}>← Projects</button>
        </div>
        {plan.proposal?.title_ref && <p className="muted">{plan.proposal.title_ref}</p>}
        <p className="muted small">{style?.name ?? s.style} · {s.aspect} · {formatSeconds(s.seconds)} ({s.seconds / 10} clips) · plan in {s.language}</p>
        {writing && (
          <span className="progress-wrap">
            <span className="progress">
              <span className="progress-bar" style={{ width: `${percent}%` }} />
              <span className="progress-label">{plan.cancelling ? 'Cancelling…' : plan.stage ? DIRECTOR_STAGE[plan.stage] : 'Queued'} · {percent}%</span>
            </span>
            {plan.limit && !plan.cancelling ? <span className="progress-eta"><LimitLine state={plan.limit} /></span>
              : plan.eta_seconds != null && <span className="progress-eta">{formatEta(plan.eta_seconds)}</span>}
          </span>
        )}
        {plan.status === 'failed' && <p className="error small">{plan.error}</p>}
        <div className="row">
          {writing && <button className="link danger" disabled={plan.cancelling} onClick={() => call(api.cancel(plan.id))}>Cancel</button>}
          {writing && plan.limit?.mode === 'wait' && <button className="small-btn" onClick={() => setLimitOpen(true)}>Switch instead…</button>}
          {!writing && (plan.status === 'failed' || plan.status === 'cancelled') && (
            <button className="small-btn" onClick={() => call(api.retry(plan.id))}>Retry</button>
          )}
          {!writing && plan.proposal && <a className="button small-btn" href={`${api.base}/plans/${plan.id}/export.md`}>Download plan (.md)</a>}
          {!writing && plan.proposal && <a className="button small-btn" href={`${api.base}/plans/${plan.id}/subtitles.srt`}>Subtitles (.srt)</a>}
          {!writing && plan.proposal && <button className="link" onClick={() => setEditing((x) => !x)}>Change settings…</button>}
        </div>
        {editing && (
          <div className="director-edit">
            <p className="muted small">Changing ratio, length, style, voice or language recomposes the whole proposal and needs a new approval.</p>
            <PlanForm styles={styles} services={services} mode={generic ? 'generic' : 'stickman'} initial={s}
              submitLabel="Recompose the proposal" onError={onError}
              onCancel={() => setEditing(false)}
              onSubmit={async (next) => {
                onUpdate(await api.revise(plan.id, { feedback: '', settings: next }))
                setEditing(false)
              }} />
          </div>
        )}
      </section>

      {plan.proposal && (
        <ProposalView plan={plan} proposal={plan.proposal} disabled={writing}
          onReviseClip={(clip, text) => call(api.revise(plan.id, { feedback: text, clip }))} />
      )}

      {plan.proposal && !writing && (
        <section className="panel section director-approve">
          <h3><span className="step">{plan.package ? '✓' : '2'}</span> {plan.package ? 'Approved' : 'Review and approve'}</h3>
          <textarea rows={2} value={feedback} placeholder="What should change? e.g. make the hook more surprising, add a callback in clip 5…"
            onChange={(e) => setFeedback(e.target.value)} />
          <div className="row">
            <button disabled={!feedback.trim()} onClick={() => call(api.revise(plan.id, { feedback })).then(() => setFeedback(''))}>
              Revise the proposal
            </button>
            <button className="primary" onClick={() => call(api.approve(plan.id))}>
              {plan.package ? 'Write all prompts again' : `Approve & write ${plan.proposal.clips.length} prompts`}
            </button>
          </div>
          <p className="muted small">Revising the proposal clears approval and any prompts, as the method requires.</p>
        </section>
      )}

      {plan.package && <PackageView plan={plan} pkg={plan.package} disabled={writing}
        api={api} generic={generic} onRewrite={(clip, text) => call(api.rewriteClip(plan.id, clip, text))} />}

      {writing && plan.limit && !plan.cancelling && (plan.limit.mode === 'ask' || (plan.limit.mode === 'wait' && limitOpen)) && (
        <LimitDialog state={plan.limit} onClose={() => setLimitOpen(false)}
          onChoose={async (action, option) => onUpdate(await api.limitChoice(plan.id, action, option))}
          onCancel={async () => onUpdate(await api.cancel(plan.id))} cancelPrompt="Stop writing this plan?" />
      )}
    </>
  )
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="director-checks">
      {checks.map((c) => <li key={c.text} className={c.ok ? 'ok' : 'warn'}>{c.ok ? '✓' : '!'} {c.text}</li>)}
    </ul>
  )
}

function ProposalView({ plan, proposal: p, disabled, onReviseClip }: {
  plan: Plan
  proposal: Proposal
  disabled: boolean
  onReviseClip: (clip: number, feedback: string) => void
}) {
  const [revising, setRevising] = useState<number | null>(null)
  const [text, setText] = useState('')
  return (
    <section className="panel section">
      <h3><span className="step">A</span> Director’s proposal</h3>
      <dl className="director-facts">
        <dt>Core message</dt><dd>{p.core_message}</dd>
        <dt>Hook</dt><dd>{p.hook}</dd>
        <dt>Narrator</dt><dd>{p.voice_summary}<details><summary className="muted small">Frozen English voice block</summary><p className="small">{p.voice_block}</p></details></dd>
        <dt>Palette</dt><dd>{p.palette.map((c) => <span key={c.color} className="director-chip">{c.color} — {c.meaning}</span>)}{p.environment_mood && <div className="small muted">{p.environment_mood}</div>}</dd>
        <dt>Music</dt><dd>{p.bgm_direction}<div className="small muted">{p.music}</div></dd>
        <dt>Arc</dt><dd>{p.emotional_arc}</dd>
        {p.cast.length > 0 && <><dt>Cast</dt><dd>{p.cast.map((c) => (
          <details key={c.role}><summary><strong>{c.role}</strong> — {c.summary}</summary><p className="small">{c.anchor}</p></details>
        ))}</dd></>}
      </dl>

      <ol className="director-board">
        {p.clips.map((c, i) => {
          const r = p.rows[i]
          return (
            <li key={c.clip} className="director-clip">
              <div className="director-clip-head">
                <strong>Clip {c.clip}</strong>
                <span className="muted small">{(c.clip - 1) * 10}–{c.clip * 10}s</span>
                <span className="director-chip">{c.stage}</span>
                {c.location && <span className="muted small">📍 {c.location}</span>}
                <button type="button" className="link small" disabled={disabled}
                  onClick={() => { setRevising(revising === c.clip ? null : c.clip); setText('') }}>Revise this clip</button>
              </div>
              <p className="small muted">{c.purpose}</p>
              <p className="director-vo">“{c.vo}” <span className="muted small">({c.vo.split(/\s+/).length} words)</span></p>
              {r?.vo_translation && <p className="small muted">{r.vo_translation}</p>}
              {r && (
                <>
                  <p className="small"><strong>Scene:</strong> {r.scene}</p>
                  <ul className="director-beats small">
                    {r.beats.map((b, n) => <li key={n}><span className="muted">{['0–3s', '3–7s', '7–10s'][n] ?? ''}</span> {b}</li>)}
                  </ul>
                  <p className="small"><strong>Camera &amp; transition:</strong> {r.camera}</p>
                  <p className="small"><strong>BGM / SFX:</strong> {r.bgm_sfx}</p>
                  <p className="small"><strong>Hands to next clip:</strong> {r.end_state}</p>
                  <div className="director-devices">{r.devices.map((d) => <span key={d} className="director-chip subtle">{d}</span>)}</div>
                </>
              )}
              {revising === c.clip && (
                <div className="row">
                  <input className="grow-input" value={text} autoFocus placeholder="What should change in this clip?"
                    onChange={(e) => setText(e.target.value)} />
                  <button disabled={!text.trim()} onClick={() => { onReviseClip(c.clip, text); setRevising(null) }}>Revise</button>
                </div>
              )}
            </li>
          )
        })}
      </ol>
      {p.overlays.length > 0 && (
        <div>
          <strong className="small">Optional post-production text</strong>
          <ul className="small muted">{p.overlays.map((o) => <li key={`${o.clip}-${o.text}`}>Clip {o.clip}: “{o.text}” — {o.placement}</li>)}</ul>
        </div>
      )}
      <details open={p.checks.some((c) => !c.ok)}>
        <summary className="small">Proposal checks ({p.checks.filter((c) => c.ok).length}/{p.checks.length} passed)</summary>
        <Checks checks={p.checks} />
      </details>
      {plan.approved && !plan.package && <p className="muted small">Approved — writing the prompts…</p>}
    </section>
  )
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button type="button" className="small-btn" onClick={() => navigator.clipboard.writeText(text).then(() => {
      setDone(true)
      setTimeout(() => setDone(false), 1500)
    })}>{done ? 'Copied ✓' : label}</button>
  )
}

function PackageView({ plan, pkg, disabled, api, generic, onRewrite }: {
  plan: Plan
  pkg: Package
  disabled: boolean
  api: DirectorApi
  generic: boolean
  onRewrite: (clip: number, feedback: string) => void
}) {
  const [rewriting, setRewriting] = useState<number | null>(null)
  const [text, setText] = useState('')
  const all = pkg.clips.map((c) => `--- Clip ${c.clip}: ${c.title} ---\n${c.prompt}`).join('\n\n')
  return (
    <section className="panel section">
      <div className="section-title-row">
        <h3><span className="step">B</span> Production prompts</h3>
        <CopyButton text={all} label={`Copy all ${pkg.clips.length} prompts`} />
      </div>
      <p className="muted small">Paste each prompt into Google Flow (Omni Flash) in order, then assemble the clips and add subtitles
        ({plan.proposal ? <a href={`${api.base}/plans/${plan.id}/subtitles.srt`}>.srt</a> : '.srt'}) in your editor.</p>
      <details>
        <summary><strong>Global continuity block</strong></summary>
        <pre className="director-prompt">{pkg.global_block}</pre>
      </details>
      {pkg.clips.map((c) => (
        <div key={c.clip} className="director-prompt-card">
          <div className="director-clip-head">
            <strong>Clip {c.clip} — {c.title}</strong>
            <CopyButton text={c.prompt} />
            <button type="button" className="link small" disabled={disabled}
              onClick={() => { setRewriting(rewriting === c.clip ? null : c.clip); setText('') }}>Rewrite this prompt</button>
          </div>
          <pre className="director-prompt">{c.prompt}</pre>
          {rewriting === c.clip && (
            <div className="row">
              <input className="grow-input" value={text} autoFocus
                placeholder={generic ? 'e.g. move the scene outdoors; keep the same person and ending'
                  : 'e.g. make the figure leap instead of walk; keep the ending'}
                onChange={(e) => setText(e.target.value)} />
              <button disabled={!text.trim()} onClick={() => { onRewrite(c.clip, text); setRewriting(null) }}>Rewrite</button>
            </div>
          )}
        </div>
      ))}
      <div>
        <strong>Stitching guide</strong>
        <ul className="small">{pkg.stitching.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
      <div>
        <strong>Voice and music continuity</strong>
        <p className="small">{pkg.audio_note}</p>
      </div>
      <details open={pkg.checks.some((c) => !c.ok)}>
        <summary className="small">Prompt checks ({pkg.checks.filter((c) => c.ok).length}/{pkg.checks.length} passed)</summary>
        <Checks checks={pkg.checks} />
      </details>
    </section>
  )
}
