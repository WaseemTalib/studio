import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, formatTime, type ClipDetail, type LookOptions, type Presets, type Word } from '../api'
import LookControls, { layoutLabel } from '../components/LookControls'
import { usePolling } from '../usePolling'

type Tab = 'trim' | 'captions' | 'layout' | 'details'
const NUDGE = 0.5

function lookFrom(clip: ClipDetail): LookOptions {
  const o = clip.project.options
  return {
    aspect: o.aspect, layout: o.layout ?? 'auto', caption_style: o.caption_style,
    caption_position: o.caption_position, highlight_color: o.highlight_color,
    words_per_line: o.words_per_line, title_overlay: o.title_overlay,
    auto_zoom: o.auto_zoom ?? false, resolution: o.resolution ?? '1080p',
    ...clip.look,
  }
}

const wordKey = (w: Word) => w.start.toFixed(3)

export default function ClipEditor() {
  const { id = '' } = useParams()
  const [clip, setClip] = useState<ClipDetail | null>(null)
  const [presets, setPresets] = useState<Presets | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('trim')

  // Draft state, applied on Render.
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(0)
  const [look, setLook] = useState<LookOptions | null>(null)
  const [title, setTitle] = useState('')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [clickSets, setClickSets] = useState<'start' | 'end'>('start')
  const [submitting, setSubmitting] = useState(false)

  const resetDraft = useCallback((c: ClipDetail) => {
    setStart(c.start)
    setEnd(c.end)
    setLook(lookFrom(c))
    setTitle(c.title)
    setEdits(Object.fromEntries((c.words ?? []).map((w) => [wordKey(w), w.text])))
  }, [])

  const load = useCallback(async (reset: boolean) => {
    try {
      const c = await api.getClip(id)
      setClip(c)
      if (reset) resetDraft(c)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id, resetDraft])

  useEffect(() => {
    load(true)
    api.presets().then(setPresets, (e: Error) => setError(e.message))
  }, [load])

  const rendering = clip?.status === 'rendering'
  const wasRendering = useRef(false)
  usePolling(() => load(false), 1500, rendering)
  useEffect(() => {
    // When a render finishes, adopt the saved state as the new baseline.
    if (wasRendering.current && clip && clip.status !== 'rendering') resetDraft(clip)
    wasRendering.current = rendering
  }, [rendering, clip, resetDraft])

  // Words of the transcript, with caption edits applied.
  const words = useMemo(() => {
    if (!clip) return []
    const saved = new Map((clip.words ?? []).map((w) => [wordKey(w), w.text]))
    return clip.context_words.map((w) => ({ ...w, text: edits[wordKey(w)] ?? saved.get(wordKey(w)) ?? w.text }))
  }, [clip, edits])
  const inClip = (w: Word) => w.start >= start - 0.05 && w.end <= end + 0.05
  const clipWords = words.filter(inClip)

  const dirty = !!clip && !!look && (
    Math.abs(start - clip.start) > 0.01 || Math.abs(end - clip.end) > 0.01 || title.trim() !== clip.title ||
    JSON.stringify(look) !== JSON.stringify(lookFrom(clip)) ||
    clip.context_words.some((w) => edits[wordKey(w)] !== undefined && edits[wordKey(w)] !== (clip.words?.find((s) => wordKey(s) === wordKey(w))?.text ?? w.text))
  )

  async function render() {
    if (!clip || !look) return
    const hasEdits = clip.words !== null || Object.keys(edits).length > 0
    setSubmitting(true)
    setError(null)
    try {
      await api.renderClip(clip.id, {
        start, end, look, title: title.trim() || clip.title,
        words: hasEdits ? words.filter((w) => w.text.trim()) : null,
      })
      await load(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  // Preview the selected range from the source video without re-rendering.
  const sourceRef = useRef<HTMLVideoElement>(null)
  function previewSelection() {
    const v = sourceRef.current
    if (!v) return
    v.currentTime = start
    v.play()
  }
  function onSourceTime() {
    const v = sourceRef.current
    if (v && !v.paused && v.currentTime >= end) v.pause()
  }

  if (error && !clip) return <p className="error">{error}</p>
  if (!clip || !look || !presets) return <p className="muted">Loading…</p>

  const aspectClass = `aspect-${(clip.look.aspect ?? look.aspect).replace(':', 'x')}`

  return (
    <div className="editor">
      <div className="editor-top">
        <Link to={`/projects/${clip.project_id}`} className="back">← {clip.project.title}</Link>
        <div className="editor-actions">
          {rendering && <span className="badge">Rendering…</span>}
          {clip.status === 'failed' && <span className="badge failed" title={clip.error ?? ''}>Last render failed</span>}
          <button disabled={!dirty || rendering} onClick={() => clip && resetDraft(clip)}>Discard changes</button>
          <button className="primary" disabled={!dirty || rendering || submitting} onClick={render}>
            {submitting ? 'Starting…' : 'Render'}
          </button>
          <a className={`button ${rendering ? 'disabled' : ''}`} href={clip.video} download={`${clip.title}.mp4`}>Download</a>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {clip.status === 'failed' && clip.error && <p className="error">{clip.error}</p>}

      <div className="editor-body">
        <div className={`editor-preview ${aspectClass}`}>
          <div className="player">
            <video key={clip.video} src={clip.video} poster={clip.thumbnail} controls playsInline />
            {rendering && <div className="player-overlay">Rendering new version…</div>}
          </div>
          <p className="muted small">
            Rendered: {formatTime(clip.start)} – {formatTime(clip.end)} · {Math.round(clip.end - clip.start)}s ·{' '}
            {layoutLabel(clip.layout)}
          </p>
        </div>

        <div className="editor-panel panel">
          <div className="tabs">
            {(['trim', 'captions', 'layout', 'details'] as Tab[]).map((t) => (
              <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>

          {tab === 'trim' && (
            <div className="tab-body">
              <div className="trim-row">
                <TimeField label="Start" value={start} onChange={setStart} max={end - 1} />
                <TimeField label="End" value={end} onChange={setEnd} min={start + 1} max={clip.project.duration ?? undefined} />
                <div className="field">
                  <span>Length</span>
                  <strong className="length">{(end - start).toFixed(1)}s</strong>
                </div>
              </div>
              <div className="row">
                <div className="field">
                  <span>Clicking a word sets the</span>
                  <div className="segmented">
                    <button type="button" className={clickSets === 'start' ? 'on' : ''} onClick={() => setClickSets('start')}>start</button>
                    <button type="button" className={clickSets === 'end' ? 'on' : ''} onClick={() => setClickSets('end')}>end</button>
                  </div>
                </div>
                {clip.source_video && (
                  <button type="button" className="align-end" onClick={previewSelection}>▶ Preview selection</button>
                )}
              </div>
              {clip.source_video && (
                <video ref={sourceRef} className="source-player" src={clip.source_video} onTimeUpdate={onSourceTime}
                  controls preload="metadata" />
              )}
              <div className="transcript-words">
                {words.map((w) => (
                  <button
                    type="button"
                    key={wordKey(w)}
                    className={`tw ${inClip(w) ? 'in' : ''}`}
                    title={formatTime(w.start)}
                    onClick={() => {
                      if (clickSets === 'start') {
                        if (w.start < end - 1) setStart(Math.max(0, w.start - 0.1))
                        setClickSets('end')
                      } else if (w.end > start + 1) {
                        setEnd(w.end + 0.25)
                      }
                    }}
                  >
                    {w.text}
                  </button>
                ))}
              </div>
            </div>
          )}

          {tab === 'captions' && (
            <div className="tab-body">
              <LookControls value={look} onChange={setLook} presets={presets} sections={['captions']} />
              <div className="field">
                <span>Caption text <em>(click a word to fix it)</em></span>
                <div className="caption-words">
                  {clipWords.map((w) => (
                    <input
                      key={wordKey(w)}
                      value={w.text}
                      size={Math.max(2, w.text.length)}
                      onChange={(e) => setEdits((ed) => ({ ...ed, [wordKey(w)]: e.target.value }))}
                    />
                  ))}
                </div>
                {Object.keys(edits).length > 0 && (
                  <button type="button" className="link align-start" onClick={() => setEdits({})}>
                    Reset caption text to transcript
                  </button>
                )}
              </div>
            </div>
          )}

          {tab === 'layout' && (
            <div className="tab-body">
              <LookControls value={look} onChange={setLook} presets={presets} sections={['layout']} />
              <p className="muted small">
                Last render used <strong>{layoutLabel(clip.layout)}</strong>. Layout is chosen per camera shot: split
                screen stacks two people half and half, and close-ups of one person fill the frame.
              </p>
            </div>
          )}

          {tab === 'details' && (
            <div className="tab-body">
              <label className="field">
                <span>Title</span>
                <input className="text" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
              </label>
              <LookControls value={look} onChange={setLook} presets={presets} sections={['title']} />
              <HashtagEditor clipId={clip.id} initial={clip.hashtags} />
              <div className="field">
                <span>AI notes</span>
                <p className="hook">“{clip.hook}”</p>
                <p className="small">{clip.reason}</p>
                {clip.topic && <span className="topic">{clip.topic}</span>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function TimeField({ label, value, onChange, min = 0, max }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number
}) {
  const clamp = (v: number) => Math.min(max ?? Infinity, Math.max(min, Math.round(v * 100) / 100))
  return (
    <div className="field">
      <span>{label}</span>
      <span className="nudge">
        <button type="button" onClick={() => onChange(clamp(value - NUDGE))}>−{NUDGE}s</button>
        <input type="number" step={0.1} value={value.toFixed(2)} onChange={(e) => onChange(clamp(Number(e.target.value)))} />
        <button type="button" onClick={() => onChange(clamp(value + NUDGE))}>+{NUDGE}s</button>
      </span>
      <small className="muted">{formatTime(value)}</small>
    </div>
  )
}

function HashtagEditor({ clipId, initial }: { clipId: string; initial: string[] }) {
  const [text, setText] = useState(initial.map((h) => `#${h}`).join(' '))
  const [saved, setSaved] = useState(false)
  async function save() {
    const hashtags = text.split(/[\s,]+/).map((h) => h.replace(/^#/, '')).filter(Boolean).slice(0, 10)
    await api.updateClip(clipId, { hashtags })
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }
  return (
    <label className="field">
      <span>Hashtags {saved && <em>saved</em>}</span>
      <input className="text" value={text} onChange={(e) => setText(e.target.value)} onBlur={save} />
    </label>
  )
}
