import { useEffect, useRef, useState } from 'react'
import {
  api,
  captionPreviewUrl,
  DEFAULT_CAPTION_OFFSET,
  DEFAULT_CAPTION_SIZE,
  formatClock,
  formatTime,
  parseTime,
  type Presets,
  type Project,
  type ProjectOptions,
  type SourceInfo,
  type Template,
} from '../api'
import { rememberedService, useAIServices } from '../aiServices'
import AIModelSelect from './AIModelSelect'
import LookControls, { LAYOUT_LABEL } from './LookControls'

const DEFAULTS: ProjectOptions = {
  mode: 'clip',
  clip_model: 'quality',
  instructions: '',
  clip_count: 6,
  min_seconds: 15,
  max_seconds: 90,
  genre: 'auto',
  topics: [],
  range_start: null,
  range_end: null,
  language: null,
  ai_service: null,
  aspect: '9:16',
  layout: 'auto',
  caption_style: 'karaoke',
  caption_position: 'bottom',
  highlight_color: '#04f827',  // Karaoke's own green
  words_per_line: 3,
  caption_size: DEFAULT_CAPTION_SIZE,
  caption_offset: DEFAULT_CAPTION_OFFSET,
  title_overlay: false,
  auto_zoom: false,
  resolution: '1080p',
}

const PROMPT_EXAMPLES = [
  'Moments where the guest shares a personal failure',
  'Practical tips the audience can apply today',
  'Funny exchanges and reactions',
]

export default function NewProject({ onCreated, onSourceChange }: {
  onCreated: (p: Project) => void
  onSourceChange?: (hasSource: boolean) => void
}) {
  const [presets, setPresets] = useState<Presets | null>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [url, setUrl] = useState('')            // committed link (drives the source card)
  const [draftUrl, setDraftUrl] = useState('')  // what is typed in the box before committing
  const [file, setFile] = useState<File | null>(null)
  const [srt, setSrt] = useState<File | null>(null)
  const [info, setInfo] = useState<SourceInfo | null>(null)
  const [projectName, setProjectName] = useState('')
  const [nameEdited, setNameEdited] = useState(false)
  const [infoState, setInfoState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [options, setOptions] = useState<ProjectOptions>(() => ({ ...DEFAULTS, ai_service: rememberedService() }))
  const aiServices = useAIServices()
  const [lengthId, setLengthId] = useState('auto')
  const [topicDraft, setTopicDraft] = useState('')
  const [range, setRange] = useState<[number, number] | null>(null)
  const [rangeText, setRangeText] = useState({ start: '', end: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const srtInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.presets().then(setPresets, (e: Error) => setError(`Could not load presets: ${e.message}`))
    api.listTemplates().then((list) => {
      setTemplates(list)
      const def = list.find((t) => t.is_default)
      if (def) applyTemplate(def)
    }, () => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const set = <K extends keyof ProjectOptions>(key: K, value: ProjectOptions[K]) =>
    setOptions((o) => ({ ...o, [key]: value }))

  function applyTemplate(t: Template) {
    setOptions((o) => ({ ...DEFAULTS, ai_service: o.ai_service, ...t.options, range_start: o.range_start, range_end: o.range_end }))
    const match = presets?.clip_lengths.find(
      (l) => l.min_seconds === t.options.min_seconds && l.max_seconds === t.options.max_seconds)
    setLengthId(match?.id ?? 'custom')
  }

  // --- Source ---------------------------------------------------------------

  const duration = info?.duration ?? null

  useEffect(() => {
    if (!nameEdited) setProjectName(info?.title ?? '')
  }, [info, nameEdited])

  function resetRange(total: number | null) {
    setRange(total ? [0, total] : null)
    setRangeText({ start: total ? formatClock(0) : '', end: total ? formatClock(total) : '' })
  }

  /** Accept the typed or pasted link as the source once it looks like a URL. */
  function commitUrl(link: string) {
    const trimmed = link.trim()
    if (!/^https?:\/\/\S+\.\S+/.test(trimmed)) {
      if (trimmed) setError('Paste a full link starting with http:// or https://')
      return
    }
    setError(null)
    setUrl(trimmed)
    setDraftUrl('')
    lookUp(trimmed)
  }

  async function lookUp(link: string) {
    const trimmed = link.trim()
    if (!/^https?:\/\/\S+\.\S+/.test(trimmed)) return
    setInfoState('loading')
    try {
      const i = await api.sourceInfo(trimmed)
      setInfo(i)
      resetRange(i.duration)
      setInfoState('idle')
    } catch {
      setInfo(null)
      resetRange(null)
      setInfoState('error')
    }
  }

  function chooseFile(f: File | null) {
    setFile(f)
    setInfo(null)
    resetRange(null)
    if (!f) return
    // Read the duration locally so the timeframe slider works before uploading.
    const video = document.createElement('video')
    video.preload = 'metadata'
    video.onloadedmetadata = () => {
      const d = Number.isFinite(video.duration) ? video.duration : null
      setInfo({ title: f.name.replace(/\.[^.]+$/, ''), duration: d, thumbnail: null, uploader: null })
      resetRange(d)
      URL.revokeObjectURL(video.src)
    }
    video.src = URL.createObjectURL(f)
  }

  function clearSource() {
    setUrl('')
    chooseFile(null)
    setInfoState('idle')
    setProjectName('')
    setNameEdited(false)
  }

  // --- Timeframe --------------------------------------------------------------

  function setRangeValue(which: 0 | 1, value: number) {
    if (!range || !duration) return
    const next: [number, number] = [...range]
    next[which] = value
    if (next[1] - next[0] < 5) return
    setRange(next)
    setRangeText({ start: formatClock(next[0]), end: formatClock(next[1]) })
  }

  function commitRangeText(which: 'start' | 'end') {
    const v = parseTime(rangeText[which])
    if (v === null || Number.isNaN(v) || !range) return
    const clamped = Math.min(duration ?? v, Math.max(0, v))
    setRangeValue(which === 'start' ? 0 : 1, clamped)
  }

  // --- Clip finding ---------------------------------------------------------------

  function chooseLength(id: string) {
    setLengthId(id)
    const preset = presets?.clip_lengths.find((l) => l.id === id)
    if (preset) setOptions((o) => ({ ...o, min_seconds: preset.min_seconds, max_seconds: preset.max_seconds }))
  }

  function addTopic() {
    const t = topicDraft.trim().replace(/,$/, '')
    if (t && !options.topics.includes(t) && options.topics.length < 10) set('topics', [...options.topics, t])
    setTopicDraft('')
  }

  // --- Templates ------------------------------------------------------------------

  async function saveTemplate(asDefault: boolean) {
    const name = asDefault ? 'Default' : window.prompt('Template name')?.trim()
    if (!name) return
    try {
      setTemplates(await api.saveTemplate(name, options, asDefault))
      setNotice(asDefault ? 'Saved as your default settings' : `Saved template “${name}”`)
      setTimeout(() => setNotice(null), 2500)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function removeTemplate(t: Template) {
    if (!confirm(`Delete template “${t.name}”?`)) return
    await api.deleteTemplate(t.id)
    setTemplates(await api.listTemplates())
  }

  // --- Submit -------------------------------------------------------------------

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file && !url.trim()) return
    const pending = topicDraft.trim()
    const topics = pending && !options.topics.includes(pending) ? [...options.topics, pending] : options.topics
    const fullRange = !range || !duration || (range[0] <= 0.5 && range[1] >= duration - 0.5)
    setBusy(true)
    setError(null)
    try {
      const project = await api.createProject(
        file ? { file } : { url: url.trim() },
        { ...options, topics, range_start: fullRange ? null : range![0], range_end: fullRange ? null : range![1] },
        { title: projectName.trim() || info?.title, srt },
      )
      clearSource()
      setSrt(null)
      setTopicDraft('')
      onCreated(project)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const hasSource = !!file || !!url.trim()
  useEffect(() => onSourceChange?.(hasSource), [hasSource, onSourceChange])
  const span = range ? range[1] - range[0] : null
  const styleLabel = options.caption_style === 'none'
    ? 'No captions' : presets?.caption_styles.find((c) => c.id === options.caption_style)?.label
  const lengthLabel = presets?.clip_lengths.find((l) => l.id === lengthId)?.label ?? `${options.min_seconds}–${options.max_seconds}s`

  return (
    <form className="new-project" onSubmit={submit}>
      <div className="panel source-panel">
        {!hasSource ? (
          <div className="source-row">
            <span className="source-icon" aria-hidden>🔗</span>
            <input
              type="url"
              placeholder="Paste a YouTube, Vimeo or direct video link"
              value={draftUrl}
              onChange={(e) => setDraftUrl(e.target.value)}
              onPaste={(e) => { e.preventDefault(); commitUrl(e.clipboardData.getData('text')) }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitUrl(draftUrl) } }}
              onBlur={() => draftUrl.trim() && commitUrl(draftUrl)}
            />
            <div
              className={`dropzone ${dragging ? 'dragging' : ''}`}
              onClick={() => input.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                const f = e.dataTransfer.files[0]
                if (f) chooseFile(f)
              }}
            >
              ⤒ Upload
              <input ref={input} type="file" accept="video/*" hidden onChange={(e) => chooseFile(e.target.files?.[0] ?? null)} />
            </div>
          </div>
        ) : (
          <div className="source-card">
            {info?.thumbnail ? <img src={info.thumbnail} alt="" /> : <div className="source-thumb">▶</div>}
            <div className="source-meta">
              <label className="field">
                <span>Project name</span>
                <input
                  className="text name-input"
                  value={projectName}
                  maxLength={120}
                  placeholder={infoState === 'loading' ? 'Reading link…' : 'Name this project'}
                  onChange={(e) => { setProjectName(e.target.value); setNameEdited(true) }}
                />
              </label>
              <span className="muted small source-line">
                {file ? file.name : url}
                {infoState === 'error' && ' · could not read this link, you can still submit it'}
                {infoState === 'idle' && info && [info.uploader, info.duration ? formatTime(info.duration) : null]
                  .filter(Boolean).map((x) => ` · ${x}`).join('')}
              </span>
              <span className="small">
                <button type="button" className="link" onClick={() => srtInput.current?.click()}>
                  {srt ? `Subtitles: ${srt.name}` : '+ Add subtitles (.srt, optional)'}
                </button>
                {srt && <button type="button" className="link danger" onClick={() => setSrt(null)}> remove</button>}
                <input ref={srtInput} type="file" accept=".srt" hidden onChange={(e) => setSrt(e.target.files?.[0] ?? null)} />
              </span>
            </div>
            <button type="button" className="ghost" onClick={clearSource} aria-label="Remove video">✕</button>
          </div>
        )}
        <p className="muted small rights">Only use videos you own or have permission to repurpose.</p>
        {error && <p className="error">{error}</p>}
      </div>

      {presets && hasSource && (
        <div className="workspace">
          <div className="workspace-main">
            <section className="panel section">
              <div className="section-title-row">
                <h3><span className="step">1</span> What to make</h3>
                <div className="segmented mode">
                  <button type="button" className={options.mode === 'clip' ? 'on' : ''} onClick={() => set('mode', 'clip')}>
                    AI clipping
                  </button>
                  <button type="button" className={options.mode === 'full' ? 'on' : ''} onClick={() => set('mode', 'full')}>
                    Don&apos;t clip
                  </button>
                </div>
              </div>
              {options.mode === 'full' && (
                <p className="muted small">
                  Keeps the whole video (or the timeframe below) as one clip and only adds captions, layout and zoom.
                </p>
              )}

              {options.mode === 'clip' && (
                <>
                  <div className="row">
                    <AIModelSelect services={aiServices} value={options.ai_service}
                      onChange={(v) => setOptions((o) => ({ ...o, ai_service: v, clip_model: v?.endsWith('-20b') ? 'fast' : 'quality' }))} />
                    <label className="field">
                      <span>Genre</span>
                      <select value={options.genre} onChange={(e) => set('genre', e.target.value)}>
                        {presets.genres.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
                      </select>
                    </label>
                    <label className="field">
                      <span>Number of clips</span>
                      <input type="number" min={1} max={20} value={options.clip_count}
                        onChange={(e) => set('clip_count', Number(e.target.value))} />
                    </label>
                  </div>

                  <div className="field">
                    <span>Clip length</span>
                    <div className="segmented wrap">
                      {presets.clip_lengths.map((l) => (
                        <button type="button" key={l.id} className={lengthId === l.id ? 'on' : ''} onClick={() => chooseLength(l.id)}>
                          {l.label}
                        </button>
                      ))}
                      <button type="button" className={lengthId === 'custom' ? 'on' : ''} onClick={() => setLengthId('custom')}>
                        Custom
                      </button>
                    </div>
                    {lengthId === 'custom' && (
                      <span className="range">
                        <input type="number" min={5} max={180} value={options.min_seconds}
                          onChange={(e) => set('min_seconds', Number(e.target.value))} />
                        –
                        <input type="number" min={10} max={300} value={options.max_seconds}
                          onChange={(e) => set('max_seconds', Number(e.target.value))} />
                        seconds
                      </span>
                    )}
                  </div>

                  <label className="field">
                    <span>Include specific moments <em>(optional)</em></span>
                    <textarea
                      rows={3}
                      maxLength={1000}
                      value={options.instructions}
                      placeholder="Describe what the clips should include, e.g. the story about the first startup and any advice on hiring"
                      onChange={(e) => set('instructions', e.target.value)}
                    />
                    <span className="examples">
                      {PROMPT_EXAMPLES.map((ex) => (
                        <button type="button" key={ex} className="chip-button" onClick={() => set('instructions', ex)}>{ex}</button>
                      ))}
                    </span>
                  </label>

                  <div className="field">
                    <span>Topics or keywords <em>(optional)</em></span>
                    <div className="chips-input">
                      {options.topics.map((t) => (
                        <span key={t} className="chip">
                          {t}
                          <button type="button" aria-label={`Remove ${t}`}
                            onClick={() => set('topics', options.topics.filter((x) => x !== t))}>×</button>
                        </span>
                      ))}
                      <input
                        value={topicDraft}
                        placeholder={options.topics.length ? '' : 'e.g. pricing, hiring — press Enter'}
                        onChange={(e) => setTopicDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTopic() }
                          else if (e.key === 'Backspace' && !topicDraft && options.topics.length) set('topics', options.topics.slice(0, -1))
                        }}
                        onBlur={addTopic}
                      />
                    </div>
                  </div>
                </>
              )}

              <div className="row">
                <label className="field">
                  <span>Speech language</span>
                  <select value={options.language ?? ''} onChange={(e) => set('language', e.target.value || null)}>
                    {presets.languages.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                  </select>
                </label>
              </div>

              <div className="field">
                <span>
                  Processing timeframe
                  {span !== null && duration ? <em> · {formatTime(span)} of {formatTime(duration)}</em> : <em> · whole video</em>}
                </span>
                {range && duration ? (
                  <>
                    <div className="dual-range">
                      <div className="dual-track" style={{ left: `${(range[0] / duration) * 100}%`, right: `${100 - (range[1] / duration) * 100}%` }} />
                      <input type="range" min={0} max={duration} step={1} value={range[0]} aria-label="Minimum"
                        onChange={(e) => setRangeValue(0, Number(e.target.value))} />
                      <input type="range" min={0} max={duration} step={1} value={range[1]} aria-label="Maximum"
                        onChange={(e) => setRangeValue(1, Number(e.target.value))} />
                    </div>
                    <span className="range">
                      <input className="time" value={rangeText.start} aria-label="Start time (h:mm:ss)"
                        onChange={(e) => setRangeText((r) => ({ ...r, start: e.target.value }))} onBlur={() => commitRangeText('start')} />
                      –
                      <input className="time" value={rangeText.end} aria-label="End time (h:mm:ss)"
                        onChange={(e) => setRangeText((r) => ({ ...r, end: e.target.value }))} onBlur={() => commitRangeText('end')} />
                    </span>
                  </>
                ) : <span className="muted small">Available once the video length is known.</span>}
              </div>
            </section>

            <section className="panel section">
              <h3><span className="step">2</span> Captions &amp; title</h3>
              <LookControls value={options} onChange={(look) => setOptions((o) => ({ ...o, ...look }))} presets={presets}
                sections={['captions', 'title']} />
            </section>

            <section className="panel section">
              <h3><span className="step">3</span> Layout &amp; export</h3>
              <LookControls value={options} onChange={(look) => setOptions((o) => ({ ...o, ...look }))} presets={presets}
                sections={['layout']} />
            </section>
          </div>

          <aside className="workspace-side">
            <div className="panel side-card">
              <img className="side-preview" src={captionPreviewUrl(options)} alt="Caption and title preview" />
              <dl className="summary">
                <dt>Mode</dt><dd>{options.mode === 'full' ? "Don't clip" : `${options.clip_count} clips · ${lengthLabel}`}</dd>
                <dt>Captions</dt><dd>{styleLabel}</dd>
                <dt>Title</dt><dd>{options.title_overlay ? 'First 3 seconds' : 'Off'}</dd>
                <dt>Layout</dt><dd>{options.aspect} · {LAYOUT_LABEL[options.layout] ?? options.layout}</dd>
                <dt>Export</dt><dd>{options.resolution}{options.auto_zoom ? ' · auto zoom' : ''}</dd>
              </dl>
              <button className="primary big" disabled={busy || !hasSource}>
                {busy ? 'Uploading…' : options.mode === 'full' ? 'Caption video' : 'Get clips'}
              </button>
            </div>

            <div className="panel side-card templates">
              <h4>Templates</h4>
              <select value="" onChange={(e) => {
                const t = templates.find((x) => x.id === e.target.value)
                if (t) applyTemplate(t)
              }}>
                <option value="">{templates.length ? 'Apply a template…' : 'No saved templates yet'}</option>
                {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? ' (default)' : ''}</option>)}
              </select>
              <div className="template-actions">
                <button type="button" onClick={() => saveTemplate(false)}>Save as template</button>
                <button type="button" onClick={() => saveTemplate(true)}>Save as default</button>
              </div>
              {templates.length > 0 && (
                <details className="manage">
                  <summary>Manage templates</summary>
                  {templates.map((t) => (
                    <div key={t.id} className="manage-row">
                      {t.name}
                      <button type="button" className="link danger" onClick={() => removeTemplate(t)}>delete</button>
                    </div>
                  ))}
                </details>
              )}
              {notice && <span className="muted small">{notice}</span>}
            </div>
          </aside>
        </div>
      )}
    </form>
  )
}
