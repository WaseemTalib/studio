import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import {
  api, canSwitchLimit, captionPreviewUrl, DEFAULT_CAPTION_OFFSET, DEFAULT_CAPTION_SIZE, formatEta, formatTime,
  type LimitState, type LookOptions, type Presets,
} from '../api'
import { LimitDialog, LimitLine } from '../components/LimitDialog'
import { rememberedService, useAIServices } from '../aiServices'
import AIModelSelect from '../components/AIModelSelect'
import FootageReview, { type Suggestions } from '../components/FootageReview'
import LookControls from '../components/LookControls'
import MusicPicker from '../components/MusicPicker'
import {
  blankScene, DraftLimit, FOOTAGE_SOURCES, forApi, MAKER_STAGE_LABEL, makerApi, SCRIPT_LANGUAGES, withUid,
  type DraftRequest, type FootageSource, type MakerOptions, type Scene, type SourceKind, type Video, type VideoRequest,
} from '../makerApi'
import { usePolling } from '../usePolling'
import { formatWait, useCountdown } from '../useCountdown'
import { DEFAULT_SETTINGS, voiceApi, type ProviderInfo, type TTSModel, type TTSProvider, type Voice } from '../voiceApi'

const KINDS: { id: SourceKind; label: string; hint: string; placeholder: string }[] = [
  { id: 'prompt', label: 'Prompt', hint: 'A topic or question; the AI writes the whole script.',
    placeholder: 'e.g. The history of the internet in plain words' },
  { id: 'description', label: 'Description', hint: 'Describe the video you want: angle, key points, audience.',
    placeholder: 'e.g. A calm explainer for beginners on how compound interest works, with three everyday examples…' },
  { id: 'script', label: 'My script', hint: 'Your own narration, used word for word. The AI only picks footage.',
    placeholder: 'Paste your script. Blank lines separate scenes.' },
  { id: 'news', label: 'News', hint: 'A news article link or its text; the script sticks to the article’s facts.',
    placeholder: 'Or paste the article text here (for sites that need a login)' },
]
const MINUTES = [1, 2, 3, 5, 8, 10, 15, 20]

const DEFAULT_SETTINGS_REQUEST: Omit<VideoRequest, 'title' | 'description' | 'hashtags' | 'scenes' | 'language' | 'ai_service'> = {
  voice: { provider: 'local', voice_id: 'af_heart', voice_name: 'Heart', model_id: 'kokoro-v1.0', settings: DEFAULT_SETTINGS },
  aspect: '16:9',
  resolution: '1080p',
  footage: 'pexels',
  sources: ['openverse', 'pixabay', 'coverr', 'unsplash'],
  clip_seconds: 5,
  transition: 'fade',
  caption_style: 'simple',
  caption_position: 'bottom',
  highlight_color: '#facc15',
  words_per_line: 4,
  caption_size: DEFAULT_CAPTION_SIZE,
  caption_offset: DEFAULT_CAPTION_OFFSET,
  title_overlay: false,
  music: null,
  music_mode: 'track',
  music_mood: '',
  music_volume: 0.12,
  cards: true,
  photos: true,
}
type Settings = typeof DEFAULT_SETTINGS_REQUEST

interface Script {
  title: string
  description: string
  hashtags: string[]
  scenes: Scene[]
}

const wordCount = (scenes: Scene[]) => scenes.reduce((n, s) => n + s.text.split(/\s+/).filter(Boolean).length, 0)
const CREDIT_SOURCE: Record<string, string> = {
  pexels: 'Pexels', pixabay: 'Pixabay', wikimedia: 'Wikimedia Commons', commons: 'Wikimedia Commons', jamendo: 'Jamendo',
  openverse: 'Openverse', web: 'Web image', searxng: 'SearXNG', duckduckgo: 'DuckDuckGo', youtube: 'YouTube',
}
const isWorking = (v: Video) => v.status === 'queued' || v.status === 'processing'

export default function VideoMaker() {
  const location = useLocation()
  const [creating, setCreating] = useState(false)
  const [presets, setPresets] = useState<Presets | null>(null)
  const [options, setOptions] = useState<MakerOptions | null>(null)
  const [source, setSource] = useState<DraftRequest>({
    kind: 'prompt', text: '', url: null, minutes: 3, language: 'English', tone: '', service: rememberedService(),
  })
  const aiServices = useAIServices()
  const [script, setScript] = useState<Script | null>(null)
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS_REQUEST)
  const [videos, setVideos] = useState<Video[]>([])
  const [drafting, setDrafting] = useState(false)
  const [draftLimit, setDraftLimit] = useState<LimitState | null>(null)
  const [making, setMaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestions>({})
  const [finding, setFinding] = useState<number | 'all' | null>(null)
  const scriptRef = useRef<HTMLDivElement>(null)

  const loadVideos = useCallback(() => makerApi.videos().then(setVideos, (e: Error) => setError(e.message)), [])
  useEffect(() => {
    api.presets().then(setPresets, (e: Error) => setError(e.message))
    makerApi.options().then((o) => {
      setOptions(o)
      // Keep the selected keyless research sources and any stock sources whose keys are configured.
      setSettings((s) => ({ ...s, sources: s.sources.filter((src) => o.footage[src]) }))
    }, (e: Error) => setError(e.message))
    loadVideos()
  }, [loadVideos])
  usePolling(loadVideos, 2000, videos.some(isWorking))
  useEffect(() => {
    if (!location.hash || !videos.length) return
    const id = decodeURIComponent(location.hash.slice(1))
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
    return () => window.clearTimeout(timer)
  }, [location.hash, videos.length])

  const setSrc = <K extends keyof DraftRequest>(k: K, v: DraftRequest[K]) => setSource((s) => ({ ...s, [k]: v }))
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setSettings((s) => ({ ...s, [k]: v }))
  const kind = KINDS.find((k) => k.id === source.kind)!
  const languageCode = SCRIPT_LANGUAGES.find((l) => l.label === source.language)?.code ?? null

  async function writeScript(service: string | null = source.service) {
    setDrafting(true)
    setError(null)
    setDraftLimit(null)
    try {
      const d = await makerApi.draft({ ...source, service, url: source.kind === 'news' ? source.url : null })
      setScript({ title: d.title, description: d.description, hashtags: d.hashtags, scenes: d.scenes.map(withUid) })
      if (d.music_mood) setSettings((s) => ({ ...s, music_mood: d.music_mood }))
      setSuggestions({})
      setTimeout(() => scriptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
    } catch (e) {
      if (e instanceof DraftLimit) setDraftLimit(e.state)
      else setError((e as Error).message)
    } finally {
      setDrafting(false)
    }
  }

  async function makeVideo() {
    if (!script) return
    setMaking(true)
    setError(null)
    try {
      const scenes = script.scenes.map((s) => forApi({ ...s, text: s.text.trim() })).filter((s) => s.text)
      const v = await makerApi.create({ ...settings, ...script, scenes, language: languageCode, ai_service: source.service })
      setVideos((all) => [v, ...all])
      setCreating(false)
      setTimeout(() => document.getElementById('maker-videos')?.scrollIntoView({ behavior: 'smooth' }), 50)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setMaking(false)
    }
  }

  /** Find footage for every scene (or one) for review; replaces the choices of the scenes searched. */
  async function findFootage(only?: number) {
    if (!script) return
    setFinding(only ?? 'all')
    setError(null)
    try {
      const plan = await makerApi.planFootage({
        scenes: script.scenes.map((s) => forApi({ ...s, text: s.text.trim() || '…', clips: [] })),
        sources: settings.sources, aspect: settings.aspect, resolution: settings.resolution,
        clip_seconds: settings.clip_seconds, speed: settings.voice.settings.speed, cards: settings.cards,
        photos: settings.photos, ai_service: source.service, ...(only !== undefined ? { only } : {}),
      })
      setScript((cur) => cur && {
        ...cur,
        scenes: cur.scenes.map((s, i) => {
          const found = plan.scenes[i]
          return found && script.scenes[i]?.uid === s.uid ? { ...s, clips: found.clips } : s
        }),
      })
      setSuggestions((cur) => {
        const next = { ...cur }
        script.scenes.forEach((s, i) => {
          const found = plan.scenes[i]
          if (found) next[s.uid!] = { shots: found.shots, candidates: found.candidates }
        })
        return next
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setFinding(null)
    }
  }

  function reuse(v: Video) {
    setCreating(true)
    const { title, description, hashtags, scenes, language, ai_service, ...rest } = v.request
    if (ai_service) setSrc('service', ai_service)
    setScript({ title, description, hashtags, scenes: scenes.map((sc) => withUid({ ...blankScene(), ...sc, uid: undefined })) })
    const legacy: FootageSource[] = rest.footage === 'none' ? [] : rest.footage === 'both' ? ['pexels', 'pixabay'] : [rest.footage]
    setSettings({ ...DEFAULT_SETTINGS_REQUEST, ...rest, sources: rest.sources ?? legacy })
    setSuggestions({})
    const label = SCRIPT_LANGUAGES.find((l) => l.code === language)?.label
    if (label) setSrc('language', label)
    setTimeout(() => scriptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  const look: LookOptions = {
    aspect: settings.aspect, layout: 'fill', caption_style: settings.caption_style,
    caption_position: settings.caption_position, highlight_color: settings.highlight_color,
    words_per_line: settings.words_per_line, caption_size: settings.caption_size, caption_offset: settings.caption_offset,
    title_overlay: settings.title_overlay, auto_zoom: false,
    resolution: settings.resolution,
  }
  const words = script ? wordCount(script.scenes) : 0
  const seconds = Math.round(words / 150 * 60 / settings.voice.settings.speed)
  const startProject = () => {
    setSource((current) => ({ ...current, kind: 'prompt', text: '', url: null, tone: '' }))
    setScript(null)
    setSuggestions({})
    setCreating(true)
  }

  return (
    <div className="maker">
      <div className="tool-page-head">
        <div className="tool-page-copy">
          <h1>Video Maker</h1>
          <p className="muted">Turn a prompt, description, script or news story into a narrated video with live research,
            stock footage, captions and music.</p>
        </div>
        <button type="button" className={creating ? '' : 'primary'} onClick={() => creating ? setCreating(false) : startProject()}>
          {creating ? '← Projects' : '+ Create project'}
        </button>
      </div>
      {error && <div className="notice error-notice"><p>{error}</p><button onClick={() => setError(null)}>Dismiss</button></div>}

      {creating && <div className="workspace">
        <div className="workspace-main">
          <section className="panel section">
            <h3><span className="step">1</span> What is the video about?</h3>
            <div className="segmented mode">
              {KINDS.map((k) => (
                <button type="button" key={k.id} className={source.kind === k.id ? 'on' : ''}
                  onClick={() => setSrc('kind', k.id)}>{k.label}</button>
              ))}
            </div>
            <p className="muted small">{kind.hint}</p>
            {source.kind === 'news' && (
              <label className="field">
                <span>Article link</span>
                <input type="url" placeholder="https://…" value={source.url ?? ''} onChange={(e) => setSrc('url', e.target.value || null)} />
              </label>
            )}
            <textarea className="maker-text" rows={source.kind === 'script' || source.kind === 'news' ? 10 : 4}
              placeholder={kind.placeholder} value={source.text} onChange={(e) => setSrc('text', e.target.value)} />
            <div className="row wrap">
              {source.kind !== 'script' && (
                <label className="field">
                  <span>Length</span>
                  <select value={source.minutes} onChange={(e) => setSrc('minutes', Number(e.target.value))}>
                    {MINUTES.map((m) => <option key={m} value={m}>{m} min</option>)}
                  </select>
                </label>
              )}
              <label className="field">
                <span>Language</span>
                <select value={source.language} onChange={(e) => setSrc('language', e.target.value)}>
                  {SCRIPT_LANGUAGES.map((l) => <option key={l.code}>{l.label}</option>)}
                </select>
              </label>
              {source.kind !== 'script' && (
                <label className="field grow">
                  <span>Tone <em className="muted">(optional)</em></span>
                  <input placeholder="e.g. energetic, documentary, humorous" value={source.tone}
                    onChange={(e) => setSrc('tone', e.target.value)} />
                </label>
              )}
              <AIModelSelect services={aiServices} value={source.service} onChange={(v) => setSrc('service', v)} />
            </div>
            <div className="row">
              <button className="primary" disabled={drafting || (!source.text.trim() && !(source.kind === 'news' && source.url))}
                onClick={() => writeScript()}>
                {drafting ? 'Writing…' : source.kind === 'script' ? 'Prepare scenes' : script ? 'Write a new script' : 'Write script'}
              </button>
              {source.kind !== 'script' && !script && (
                <button type="button" className="link" onClick={() => {
                  setScript({ title: 'Untitled video', description: '', hashtags: [], scenes: [blankScene()] })
                }}>or start from a blank script</button>
              )}
            </div>
          </section>

          {script && (
            <div ref={scriptRef}>
              <ScriptEditor script={script} onChange={setScript} />
            </div>
          )}

          {script && (
            <>
              <section className="panel section">
                <h3><span className="step">3</span> Voice</h3>
                <VoicePicker value={settings.voice} language={source.language} onChange={(v) => set('voice', v)} />
              </section>

              <section className="panel section">
                <h3><span className="step">4</span> Footage &amp; format</h3>
                <div className="field">
                  <span>Footage sources <em className="muted">(none ticked: a plain background)</em></span>
                  <div className="source-list">
                    {FOOTAGE_SOURCES.map((f) => {
                      const ok = options?.footage[f.id] ?? false
                      return (
                        <label key={f.id} className={`source-option ${ok ? '' : 'disabled'}`}
                          title={ok ? '' : `${f.key} is unavailable (${f.signup}); restart the backend after installing or configuring it`}>
                          <input type="checkbox" disabled={!ok} checked={settings.sources.includes(f.id)}
                            onChange={(e) => set('sources', e.target.checked
                              ? [...settings.sources, f.id] : settings.sources.filter((x) => x !== f.id))} />
                          <span><strong>{f.label}</strong> <span className="muted small">{f.hint}{ok ? '' : ` · needs ${f.key}`}</span></span>
                        </label>
                      )
                    })}
                  </div>
                </div>
                <div className="row wrap">
                  <div className="field">
                    <span>Aspect ratio</span>
                    <div className="segmented">
                      {(['16:9', '9:16', '1:1'] as const).map((a) => (
                        <button type="button" key={a} className={settings.aspect === a ? 'on' : ''} onClick={() => set('aspect', a)}>
                          {a}{a === '16:9' ? ' YouTube' : a === '9:16' ? ' Shorts' : ''}
                        </button>
                      ))}
                    </div>
                  </div>
                  <label className="field">
                    <span>Quality</span>
                    <select value={settings.resolution} onChange={(e) => set('resolution', e.target.value as Settings['resolution'])}>
                      <option value="720p">720p (faster)</option>
                      <option value="1080p">1080p</option>
                    </select>
                  </label>
                </div>
                <div className="row wrap">
                  <label className="field grow">
                    <span>Longest shot: {settings.clip_seconds}s</span>
                    <input type="range" min={2} max={15} step={1} value={settings.clip_seconds}
                      onChange={(e) => set('clip_seconds', Number(e.target.value))} />
                  </label>
                  <div className="field">
                    <span>Between scenes</span>
                    <div className="segmented">
                      <button type="button" className={settings.transition === 'fade' ? 'on' : ''} onClick={() => set('transition', 'fade')}>Fade</button>
                      <button type="button" className={settings.transition === 'cut' ? 'on' : ''} onClick={() => set('transition', 'cut')}>Cut</button>
                    </div>
                  </div>
                </div>
                <div className="row wrap">
                  <label className="field check">
                    <input type="checkbox" checked={settings.cards} onChange={(e) => set('cards', e.target.checked)} />
                    Fact &amp; quote cards <em className="muted">(full-screen, from the script)</em>
                  </label>
                  <label className="field check">
                    <input type="checkbox" checked={settings.photos} onChange={(e) => set('photos', e.target.checked)} />
                    Photos of named places <em className="muted">(free-licensed, Wikimedia Commons)</em>
                  </label>
                </div>
                <FootageReview scenes={script.scenes} suggestions={suggestions} sources={settings.sources}
                  aspect={settings.aspect} resolution={settings.resolution} busy={finding} onFind={findFootage}
                  onChange={(i, clips) => setScript((cur) => cur && {
                    ...cur, scenes: cur.scenes.map((s, n) => (n === i ? { ...s, clips } : s)),
                  })} />
              </section>

              <section className="panel section">
                <h3><span className="step">5</span> Captions</h3>
                {presets && (
                  <LookControls value={look} presets={presets} sections={['captions', 'title']}
                    onChange={(l) => setSettings((s) => ({
                      ...s, caption_style: l.caption_style, caption_position: l.caption_position,
                      highlight_color: l.highlight_color, words_per_line: l.words_per_line, title_overlay: l.title_overlay,
                      caption_size: l.caption_size, caption_offset: l.caption_offset,
                    }))} />
                )}
              </section>

              <section className="panel section">
                <h3><span className="step">6</span> Background music</h3>
                <MusicPicker options={options} onOptions={setOptions} value={settings} onError={setError}
                  onChange={(next) => setSettings((s) => ({ ...s, ...next }))} />
              </section>
            </>
          )}
        </div>

        <aside className="workspace-side">
          <div className="panel side-card">
            <img className={`side-preview ${settings.aspect === '16:9' ? 'wide' : ''}`} src={captionPreviewUrl(look)} alt="Caption preview" />
            <dl className="summary">
              <dt>Script</dt><dd>{script ? `${script.scenes.length} scenes · ${words} words` : 'Not written yet'}</dd>
              <dt>Length</dt><dd>{script ? `about ${formatTime(seconds)}` : `${source.minutes} min target`}</dd>
              <dt>Voice</dt><dd>{settings.voice.voice_name} · {settings.voice.provider === 'local' ? 'Kokoro' : 'ElevenLabs'}</dd>
              <dt>Footage</dt><dd>{settings.sources.length
                ? settings.sources.map((s) => FOOTAGE_SOURCES.find((f) => f.id === s)?.label).join(', ') : 'Plain background'}
                {' '}· {settings.aspect} · {settings.resolution}</dd>
              <dt>Music</dt><dd>{settings.music_mode === 'random' ? 'Random from library'
                : settings.music_mode === 'auto' ? `Matches “${settings.music_mood || 'the script'}”` : settings.music ?? 'None'}</dd>
            </dl>
            <button className="primary block" disabled={!script || making || !script.scenes.some((s) => s.text.trim())}
              onClick={makeVideo}>
              {making ? 'Starting…' : 'Make video'}
            </button>
            {!script && <p className="muted small">Write or paste a script first.</p>}
          </div>
        </aside>
      </div>}

      {!creating && <section id="maker-videos" className="maker-videos first-load-projects">
        <div className="section-head">
          <h2>Projects</h2>
          {videos.length > 0 && <span className="muted small">{videos.length} total</span>}
        </div>
        {videos.length === 0 && <div className="empty">No projects yet. Create one to make your first video.</div>}
        <div className="maker-grid">
          {videos.map((v) => (
            <VideoCard key={v.id} video={v} onReuse={() => reuse(v)} onError={setError}
              onUpdate={(nv) => setVideos((all) => all.map((x) => (x.id === nv.id ? nv : x)))}
              onRemoved={() => setVideos((all) => all.filter((x) => x.id !== v.id))} />
          ))}
        </div>
      </section>}

      {draftLimit && (
        <DraftLimitDialog state={draftLimit} onClose={() => setDraftLimit(null)}
          onRetry={(service) => {
            if (service) setSrc('service', service)
            writeScript(service ?? source.service)
          }} />
      )}
    </div>
  )
}

/** The script service hit a usage limit while writing: wait (then retry automatically) or switch. */
function DraftLimitDialog({ state, onRetry, onClose }: {
  state: LimitState
  onRetry: (service: string | null) => void
  onClose: () => void
}) {
  const [waiting, setWaiting] = useState(false)
  const fired = useRef(false)
  const left = useCountdown(waiting ? state.limit.resets_in : null)
  useEffect(() => {
    if (waiting && left === 0 && !fired.current) {
      fired.current = true
      onRetry(null)
    }
  }, [waiting, left, onRetry])
  if (waiting) {
    return (
      <div className="notice limit-notice maker-wait">
        <p>Waiting for {state.limit.service_label}’s {state.limit.name} limit · writes the script in {left != null ? formatWait(left) : '…'}</p>
        <button onClick={onClose}>Stop waiting</button>
      </div>
    )
  }
  return (
    <LimitDialog state={state} onClose={onClose}
      onChoose={async (action, option) => (action === 'wait' ? setWaiting(true) : onRetry(option ?? null))} />
  )
}

function ScriptEditor({ script, onChange }: { script: Script; onChange: (s: Script) => void }) {
  const words = wordCount(script.scenes)
  const setScene = (i: number, scene: Scene) => onChange({ ...script, scenes: script.scenes.map((s, n) => (n === i ? scene : s)) })
  const insert = (i: number) => onChange({ ...script, scenes: [...script.scenes.slice(0, i), blankScene(), ...script.scenes.slice(i)] })
  const remove = (i: number) => onChange({ ...script, scenes: script.scenes.filter((_, n) => n !== i) })
  const move = (i: number, d: -1 | 1) => {
    const scenes = [...script.scenes]
    const j = i + d
    if (j < 0 || j >= scenes.length) return
    ;[scenes[i], scenes[j]] = [scenes[j], scenes[i]]
    onChange({ ...script, scenes })
  }
  return (
    <section className="panel section">
      <div className="section-title-row">
        <h3><span className="step">2</span> Review the script</h3>
        <span className="muted small">{script.scenes.length} scenes · {words} words · about {formatTime(words / 150 * 60)} narrated</span>
      </div>
      <label className="field">
        <span>Title</span>
        <input value={script.title} maxLength={120} onChange={(e) => onChange({ ...script, title: e.target.value })} />
      </label>
      <div className="row wrap">
        <label className="field grow">
          <span>Description</span>
          <input value={script.description} onChange={(e) => onChange({ ...script, description: e.target.value })} />
        </label>
        <label className="field grow">
          <span>Hashtags</span>
          <input value={script.hashtags.map((h) => `#${h}`).join(' ')}
            onChange={(e) => onChange({ ...script, hashtags: e.target.value.split(/[\s,]+/).map((h) => h.replace(/^#/, '')).filter(Boolean) })} />
        </label>
      </div>
      <ol className="scene-list">
        {script.scenes.map((s, i) => (
          <li key={s.uid ?? i} className="scene">
            <div className="scene-head">
              <strong>Scene {i + 1}</strong>
              <span className="scene-tools">
                <button type="button" className="ghost" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" className="ghost" title="Move down" disabled={i === script.scenes.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" className="ghost" title="Add a scene after this one" onClick={() => insert(i + 1)}>＋</button>
                <button type="button" className="ghost danger" title="Delete scene" disabled={script.scenes.length === 1} onClick={() => remove(i)}>✕</button>
              </span>
            </div>
            <textarea rows={3} value={s.text} placeholder="What the narrator says in this scene"
              onChange={(e) => setScene(i, { ...s, text: e.target.value })} />
            <TermsInput terms={s.search_terms} onChange={(t) => setScene(i, { ...s, search_terms: t })} />
            <SceneExtras scene={s} onChange={(next) => setScene(i, next)} />
          </li>
        ))}
      </ol>
    </section>
  )
}

/** Optional per scene: a fact card, a quote card and a place to show a photo of. */
function SceneExtras({ scene, onChange }: { scene: Scene; onChange: (s: Scene) => void }) {
  const has = !!(scene.card_text || scene.quote || scene.place)
  const [open, setOpen] = useState(has)
  if (!open) {
    return <button type="button" className="link small scene-extras-toggle" onClick={() => setOpen(true)}>+ Card, quote or place photo</button>
  }
  return (
    <div className="scene-extras">
      <label className="field">
        <span>Fact card</span>
        <input value={scene.card_text} maxLength={80} placeholder="e.g. 158 YEARS IN PRISON"
          onChange={(e) => onChange({ ...scene, card_text: e.target.value })} />
      </label>
      <label className="field">
        <span>Quote</span>
        <input value={scene.quote} maxLength={400} placeholder="Exact words someone said"
          onChange={(e) => onChange({ ...scene, quote: e.target.value })} />
      </label>
      <label className="field">
        <span>Said by</span>
        <input value={scene.quote_by} maxLength={120} placeholder="Name, role"
          onChange={(e) => onChange({ ...scene, quote_by: e.target.value })} />
      </label>
      <label className="field">
        <span>Place photo</span>
        <input value={scene.place} maxLength={120} placeholder="e.g. Ensenada, Baja California"
          onChange={(e) => onChange({ ...scene, place: e.target.value })} />
      </label>
    </div>
  )
}

function TermsInput({ terms, onChange }: { terms: string[]; onChange: (t: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const t = draft.trim().toLowerCase()
    if (t && !terms.includes(t)) onChange([...terms, t].slice(0, 6))
    setDraft('')
  }
  return (
    <div className="chips-input" title="Stock footage searches for this scene (English works best)">
      <span className="muted small">Footage:</span>
      {terms.map((t) => (
        <span key={t} className="chip">{t}<button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(terms.filter((x) => x !== t))}>×</button></span>
      ))}
      <input value={draft} placeholder={terms.length ? 'add search…' : 'add a search, e.g. city skyline'} onChange={(e) => setDraft(e.target.value)}
        onBlur={add} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add() } }} />
    </div>
  )
}

function VoicePicker({ value, language, onChange }: {
  value: VideoRequest['voice']
  language: string
  onChange: (v: VideoRequest['voice']) => void
}) {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [voices, setVoices] = useState<Voice[]>([])
  const [models, setModels] = useState<TTSModel[]>([])
  const [error, setError] = useState<string | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const provider = value.provider
  const local = providers.find((p) => p.id === 'local')
  const modelState = local?.model?.state

  const loadProviders = useCallback(() => voiceApi.providers().then(setProviders, (e: Error) => setError(e.message)), [])
  useEffect(() => { loadProviders() }, [loadProviders])
  usePolling(loadProviders, 1500, modelState === 'downloading')

  useEffect(() => {
    setError(null)
    voiceApi.voices(provider, 'default', {}).then((r) => setVoices(r.voices), (e: Error) => setError(e.message))
    voiceApi.models(provider).then(setModels, () => setModels([]))
  }, [provider])

  // Kokoro voices speak one language each; show the ones for the script's language when there are any.
  const shown = useMemo(() => {
    if (provider !== 'local') return voices
    const match = voices.filter((v) => v.language === language)
    return match.length ? match : voices
  }, [voices, provider, language])
  const noLocalVoice = provider === 'local' && voices.length > 0 && !voices.some((v) => v.language === language)

  function pickProvider(p: TTSProvider) {
    if (p === provider) return
    onChange(p === 'local'
      ? { ...value, provider: 'local', voice_id: 'af_heart', voice_name: 'Heart', model_id: 'kokoro-v1.0' }
      : { ...value, provider: 'elevenlabs', voice_id: '', voice_name: '', model_id: 'eleven_multilingual_v2' })
  }

  useEffect(() => {  // pick the first matching voice when the current one is not offered
    if (shown.length && !shown.some((v) => v.id === value.voice_id)) {
      onChange({ ...value, voice_id: shown[0].id, voice_name: shown[0].name })
    }
  }, [shown]) // eslint-disable-line react-hooks/exhaustive-deps

  function preview(v: Voice) {
    if (!v.preview_url) return
    audio.current?.pause()
    audio.current = new Audio(v.preview_url)
    audio.current.play().catch(() => setError('Could not play the preview'))
  }

  const current = voices.find((v) => v.id === value.voice_id)
  return (
    <>
      <div className="segmented">
        {providers.map((p) => (
          <button type="button" key={p.id} className={provider === p.id ? 'on' : ''} disabled={!p.available}
            title={p.note ?? ''} onClick={() => pickProvider(p.id)}>
            {p.id === 'local' ? 'Kokoro (free, on this Mac)' : `ElevenLabs${p.available ? '' : ' — needs key'}`}
          </button>
        ))}
      </div>
      {provider === 'local' && modelState && modelState !== 'ready' && (
        <div className="notice">
          <p>{modelState === 'downloading' ? `Downloading the voice model… ${local?.model?.total ? Math.round(100 * local.model.done / local.model.total) : 0}%`
            : modelState === 'failed' ? `Voice model download failed: ${local?.model?.error}` : 'The free local voice model (about 330 MB) is downloaded once.'}</p>
          {modelState !== 'downloading' && <button onClick={() => voiceApi.downloadLocalModel().then(loadProviders)}>Download</button>}
        </div>
      )}
      {noLocalVoice && <p className="small limit-reason">Kokoro has no {language} voice; choose ElevenLabs for {language} narration.</p>}
      {error && <p className="error small">{error}</p>}
      <div className="row wrap">
        <label className="field grow">
          <span>Voice</span>
          <select value={value.voice_id} onChange={(e) => {
            const v = voices.find((x) => x.id === e.target.value)
            if (v) onChange({ ...value, voice_id: v.id, voice_name: v.name })
          }}>
            {shown.map((v) => (
              <option key={v.id} value={v.id}>{v.name}{[v.accent, v.gender, v.language].filter(Boolean).length ? ` — ${[v.accent, v.gender, v.language].filter(Boolean).join(', ')}` : ''}</option>
            ))}
          </select>
        </label>
        {current?.preview_url && <button type="button" className="align-end" onClick={() => preview(current)}>▶ Preview</button>}
        {provider === 'elevenlabs' && models.length > 0 && (
          <label className="field">
            <span>Model</span>
            <select value={value.model_id} onChange={(e) => onChange({ ...value, model_id: e.target.value })}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>
        )}
        <label className="field">
          <span>Speed: {value.settings.speed.toFixed(2)}×</span>
          <input type="range" min={0.7} max={1.2} step={0.05} value={value.settings.speed}
            onChange={(e) => onChange({ ...value, settings: { ...value.settings, speed: Number(e.target.value) } })} />
        </label>
      </div>
    </>
  )
}

function VideoCard({ video, onUpdate, onRemoved, onReuse, onError }: {
  video: Video
  onUpdate: (v: Video) => void
  onRemoved: () => void
  onReuse: () => void
  onError: (m: string) => void
}) {
  const [limitOpen, setLimitOpen] = useState(false)
  const working = isWorking(video)
  const call = (p: Promise<Video>) => p.then(onUpdate, (e: Error) => onError(e.message))
  const percent = Math.round(video.progress * 100)
  const label = video.cancelling ? 'Cancelling…' : video.stage ? MAKER_STAGE_LABEL[video.stage] : 'Queued'
  return (
    <article id={`video-${video.id}`} className="panel maker-card">
      {video.status === 'done' && video.video ? (
        <video className={`maker-player ${video.request.aspect === '16:9' ? 'wide' : ''}`} controls preload="metadata"
          src={video.video} poster={video.thumbnail ?? undefined} />
      ) : (
        <div className={`maker-player placeholder ${video.request.aspect === '16:9' ? 'wide' : ''}`}>
          {video.status === 'failed' ? 'Failed' : video.status === 'cancelled' ? 'Cancelled' : `${percent}%`}
        </div>
      )}
      <div className="maker-card-body">
        <strong className="maker-card-title">{video.title}</strong>
        <span className="muted small">
          {video.request.scenes.length} scenes · {video.request.aspect}
          {video.duration ? ` · ${formatTime(video.duration)}` : ''} · {new Date(video.created_at * 1000).toLocaleString()}
        </span>
        {working && (
          <span className="progress-wrap">
            <span className="progress" title={label}>
              <span className="progress-bar" style={{ width: `${percent}%` }} />
              <span className="progress-label">{label} · {percent}%</span>
            </span>
            {video.limit && !video.cancelling ? <span className="progress-eta"><LimitLine state={video.limit} /></span>
              : video.eta_seconds != null && !video.cancelling && <span className="progress-eta">{formatEta(video.eta_seconds)}</span>}
          </span>
        )}
        {video.status === 'failed' && <p className="error small">{video.error}</p>}
        <div className="maker-actions">
          {video.status === 'done' && video.video && (
            <a className="button small-btn" href={video.video} download={`${video.title}.mp4`}>Download</a>
          )}
          {working && video.limit && canSwitchLimit(video.limit) &&
            <button className="small-btn" onClick={() => setLimitOpen(true)}>Switch model now…</button>}
          {working && (
            <button className="link danger" disabled={video.cancelling}
              onClick={() => confirm(`Stop making “${video.title}”?`) && call(makerApi.cancel(video.id))}>Cancel</button>
          )}
          {!working && video.status !== 'done' && <button className="small-btn" onClick={() => call(makerApi.retry(video.id))}>Retry</button>}
          {!working && <button className="link" onClick={onReuse}>Edit &amp; remake</button>}
          {!working && (
            <button className="link danger" onClick={() => confirm(`Delete “${video.title}”?`)
              && makerApi.remove(video.id).then(onRemoved, (e: Error) => onError(e.message))}>Delete</button>
          )}
        </div>
        {video.credits.length > 0 && (
          <details className="small muted">
            <summary>Credits ({video.credits.length})</summary>
            <ul className="maker-credits">
              {video.credits.map((c) => (
                <li key={c.page || c.title}>
                  {c.title && <>Music “{c.title}” by </>}
                  <a href={c.page} target="_blank" rel="noreferrer">{c.author || 'Unknown'}</a>
                  {' '}on {CREDIT_SOURCE[c.provider] ?? c.provider}
                  {c.license && ` (${c.license})`}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {working && video.limit && !video.cancelling &&
        (video.limit.mode === 'ask' || (canSwitchLimit(video.limit) && limitOpen)) && (
        <LimitDialog state={video.limit} onClose={() => setLimitOpen(false)}
          onChoose={async (action, option) => onUpdate(await makerApi.limitChoice(video.id, action, option))}
          onCancel={async () => onUpdate(await makerApi.cancel(video.id))}
          cancelPrompt={`Stop making “${video.title}”? Narration already recorded is kept for a retry.`} />
      )}
    </article>
  )
}
