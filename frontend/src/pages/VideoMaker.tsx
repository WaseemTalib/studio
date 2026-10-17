import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, captionPreviewUrl, formatEta, formatTime, type LimitState, type LookOptions, type Presets } from '../api'
import { LimitDialog, LimitLine } from '../components/LimitDialog'
import LookControls from '../components/LookControls'
import {
  DraftLimit, MAKER_STAGE_LABEL, makerApi, SCRIPT_LANGUAGES,
  type DraftRequest, type Footage, type MakerOptions, type Scene, type SourceKind, type Video, type VideoRequest,
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
const FOOTAGE: { id: Footage; label: string }[] = [
  { id: 'pexels', label: 'Pexels' }, { id: 'pixabay', label: 'Pixabay' }, { id: 'both', label: 'Both' },
  { id: 'none', label: 'Plain background' },
]
const KEY_HELP: Record<'pexels' | 'pixabay', string> = {
  pexels: 'PEXELS_API_KEY (free at pexels.com/api)',
  pixabay: 'PIXABAY_API_KEY (free at pixabay.com/api/docs)',
}

const DEFAULT_SETTINGS_REQUEST: Omit<VideoRequest, 'title' | 'description' | 'hashtags' | 'scenes' | 'language'> = {
  voice: { provider: 'local', voice_id: 'af_heart', voice_name: 'Heart', model_id: 'kokoro-v1.0', settings: DEFAULT_SETTINGS },
  aspect: '16:9',
  resolution: '1080p',
  footage: 'pexels',
  clip_seconds: 5,
  transition: 'fade',
  caption_style: 'simple',
  caption_position: 'bottom',
  highlight_color: '#facc15',
  words_per_line: 4,
  title_overlay: false,
  music: null,
  music_volume: 0.12,
}
type Settings = typeof DEFAULT_SETTINGS_REQUEST

interface Script {
  title: string
  description: string
  hashtags: string[]
  scenes: Scene[]
}

const wordCount = (scenes: Scene[]) => scenes.reduce((n, s) => n + s.text.split(/\s+/).filter(Boolean).length, 0)
const isWorking = (v: Video) => v.status === 'queued' || v.status === 'processing'

export default function VideoMaker() {
  const [presets, setPresets] = useState<Presets | null>(null)
  const [options, setOptions] = useState<MakerOptions | null>(null)
  const [source, setSource] = useState<DraftRequest>({
    kind: 'prompt', text: '', url: null, minutes: 3, language: 'English', tone: '', service: null,
  })
  const [script, setScript] = useState<Script | null>(null)
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS_REQUEST)
  const [videos, setVideos] = useState<Video[]>([])
  const [drafting, setDrafting] = useState(false)
  const [draftLimit, setDraftLimit] = useState<LimitState | null>(null)
  const [making, setMaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const scriptRef = useRef<HTMLDivElement>(null)

  const loadVideos = useCallback(() => makerApi.videos().then(setVideos, (e: Error) => setError(e.message)), [])
  useEffect(() => {
    api.presets().then(setPresets, (e: Error) => setError(e.message))
    makerApi.options().then((o) => {
      setOptions(o)
      if (!o.footage.pexels) setSettings((s) => ({ ...s, footage: o.footage.pixabay ? 'pixabay' : 'none' }))
    }, (e: Error) => setError(e.message))
    loadVideos()
  }, [loadVideos])
  usePolling(loadVideos, 2000, videos.some(isWorking))

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
      setScript({ title: d.title, description: d.description, hashtags: d.hashtags, scenes: d.scenes })
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
      const scenes = script.scenes.map((s) => ({ ...s, text: s.text.trim() })).filter((s) => s.text)
      const v = await makerApi.create({ ...settings, ...script, scenes, language: languageCode })
      setVideos((all) => [v, ...all])
      document.getElementById('maker-videos')?.scrollIntoView({ behavior: 'smooth' })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setMaking(false)
    }
  }

  function reuse(v: Video) {
    const { title, description, hashtags, scenes, language, ...rest } = v.request
    setScript({ title, description, hashtags, scenes })
    setSettings({ ...DEFAULT_SETTINGS_REQUEST, ...rest })
    const label = SCRIPT_LANGUAGES.find((l) => l.code === language)?.label
    if (label) setSrc('language', label)
    setTimeout(() => scriptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  const look: LookOptions = {
    aspect: settings.aspect, layout: 'fill', caption_style: settings.caption_style,
    caption_position: settings.caption_position, highlight_color: settings.highlight_color,
    words_per_line: settings.words_per_line, title_overlay: settings.title_overlay, auto_zoom: false,
    resolution: settings.resolution,
  }
  const words = script ? wordCount(script.scenes) : 0
  const seconds = Math.round(words / 150 * 60 / settings.voice.settings.speed)
  const footageMissing = options && settings.footage !== 'none'
    && (settings.footage === 'both' ? !options.footage.pexels && !options.footage.pixabay : !options.footage[settings.footage])

  return (
    <div className="maker">
      <div className="page-title">
        <h1>Video Maker</h1>
        <p className="muted">Turn a prompt, description, script or news story into a narrated video with stock footage,
          captions and music — using free services.</p>
      </div>
      {error && <div className="notice error-notice"><p>{error}</p><button onClick={() => setError(null)}>Dismiss</button></div>}

      <div className="workspace">
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
              <label className="field">
                <span>Written by</span>
                <select value={source.service ?? ''} onChange={(e) => setSrc('service', e.target.value || null)}>
                  <option value="">Default{options?.default_service ? ` (${options.default_service.split(':')[0]})` : ''}</option>
                  {options?.services.map((s) => (
                    <option key={s.id} value={s.id} disabled={!s.available}>{s.label}{s.available ? '' : ' — needs key'}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="row">
              <button className="primary" disabled={drafting || (!source.text.trim() && !(source.kind === 'news' && source.url))}
                onClick={() => writeScript()}>
                {drafting ? 'Writing…' : source.kind === 'script' ? 'Prepare scenes' : script ? 'Write a new script' : 'Write script'}
              </button>
              {source.kind !== 'script' && !script && (
                <button type="button" className="link" onClick={() => {
                  setScript({ title: 'Untitled video', description: '', hashtags: [], scenes: [{ text: '', search_terms: [] }] })
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
                  <span>Stock footage</span>
                  <div className="segmented">
                    {FOOTAGE.map((f) => (
                      <button type="button" key={f.id} className={settings.footage === f.id ? 'on' : ''}
                        onClick={() => set('footage', f.id)}>{f.label}</button>
                    ))}
                  </div>
                  {footageMissing && (
                    <span className="small limit-reason">
                      Add {settings.footage === 'both' ? `${KEY_HELP.pexels} or ${KEY_HELP.pixabay}` : KEY_HELP[settings.footage as 'pexels' | 'pixabay']} to
                      backend/.env and restart the backend, or use a plain background.
                    </span>
                  )}
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
              </section>

              <section className="panel section">
                <h3><span className="step">5</span> Captions</h3>
                {presets && (
                  <LookControls value={look} presets={presets} sections={['captions', 'title']}
                    onChange={(l) => setSettings((s) => ({
                      ...s, caption_style: l.caption_style, caption_position: l.caption_position,
                      highlight_color: l.highlight_color, words_per_line: l.words_per_line, title_overlay: l.title_overlay,
                    }))} />
                )}
              </section>

              <section className="panel section">
                <h3><span className="step">6</span> Background music</h3>
                <MusicPicker options={options} onOptions={setOptions} value={settings.music} volume={settings.music_volume}
                  onChange={(music, volume) => setSettings((s) => ({ ...s, music, music_volume: volume }))} onError={setError} />
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
              <dt>Footage</dt><dd>{FOOTAGE.find((f) => f.id === settings.footage)?.label} · {settings.aspect} · {settings.resolution}</dd>
              <dt>Music</dt><dd>{settings.music ?? 'None'}</dd>
            </dl>
            <button className="primary block" disabled={!script || making || !!footageMissing || !script.scenes.some((s) => s.text.trim())}
              onClick={makeVideo}>
              {making ? 'Starting…' : 'Make video'}
            </button>
            {!script && <p className="muted small">Write or paste a script first.</p>}
          </div>
        </aside>
      </div>

      <section id="maker-videos" className="maker-videos">
        <h2>Your videos</h2>
        {videos.length === 0 && <p className="muted">Videos you make appear here.</p>}
        <div className="maker-grid">
          {videos.map((v) => (
            <VideoCard key={v.id} video={v} onReuse={() => reuse(v)} onError={setError}
              onUpdate={(nv) => setVideos((all) => all.map((x) => (x.id === nv.id ? nv : x)))}
              onRemoved={() => setVideos((all) => all.filter((x) => x.id !== v.id))} />
          ))}
        </div>
      </section>

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
  const insert = (i: number) => onChange({ ...script, scenes: [...script.scenes.slice(0, i), { text: '', search_terms: [] }, ...script.scenes.slice(i)] })
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
          <li key={i} className="scene">
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
          </li>
        ))}
      </ol>
    </section>
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

function MusicPicker({ options, onOptions, value, volume, onChange, onError }: {
  options: MakerOptions | null
  onOptions: (o: MakerOptions) => void
  value: string | null
  volume: number
  onChange: (music: string | null, volume: number) => void
  onError: (m: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  const tracks = options?.music ?? []
  async function upload(file: File | undefined) {
    if (!file || !options) return
    setUploading(true)
    try {
      const music = await makerApi.uploadMusic(file)
      onOptions({ ...options, music })
      const added = music.find((t) => !tracks.some((x) => x.name === t.name))
      if (added) onChange(added.name, volume)
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }
  async function remove(name: string) {
    if (!options || !confirm(`Delete “${name}” from your music library?`)) return
    try {
      await makerApi.deleteMusic(name)
      onOptions({ ...options, music: tracks.filter((t) => t.name !== name) })
      if (value === name) onChange(null, volume)
    } catch (e) {
      onError((e as Error).message)
    }
  }
  const current = tracks.find((t) => t.name === value)
  return (
    <>
      <div className="row wrap">
        <label className="field grow">
          <span>Track</span>
          <select value={value ?? ''} onChange={(e) => onChange(e.target.value || null, volume)}>
            <option value="">No music</option>
            {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        </label>
        <label className="button align-end">
          {uploading ? 'Uploading…' : 'Upload a track'}
          <input type="file" hidden accept="audio/*,.mp3,.m4a,.wav,.ogg,.flac,.aac" onChange={(e) => upload(e.target.files?.[0])} />
        </label>
        {current && <button type="button" className="link danger align-end" onClick={() => remove(current.name)}>Delete track</button>}
      </div>
      {current && (
        <>
          <audio controls src={current.url} className="maker-audio" />
          <label className="field">
            <span>Music volume: {Math.round(volume * 100)}%</span>
            <input type="range" min={0} max={0.6} step={0.02} value={volume} onChange={(e) => onChange(value, Number(e.target.value))} />
          </label>
        </>
      )}
      <p className="muted small">Use music you have the rights to. Free sources: YouTube Audio Library, Pixabay Music, Free Music Archive.
        It loops under the narration and fades out at the end.</p>
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
    <article className="panel maker-card">
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
          {working && video.limit?.mode === 'wait' && <button className="small-btn" onClick={() => setLimitOpen(true)}>Switch instead…</button>}
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
            <summary>Footage credits ({video.credits.length})</summary>
            <ul className="maker-credits">
              {video.credits.map((c) => (
                <li key={c.page}><a href={c.page} target="_blank" rel="noreferrer">{c.author || 'Unknown'}</a> on {c.provider === 'pexels' ? 'Pexels' : 'Pixabay'}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {working && video.limit && !video.cancelling && (video.limit.mode === 'ask' || (video.limit.mode === 'wait' && limitOpen)) && (
        <LimitDialog state={video.limit} onClose={() => setLimitOpen(false)}
          onChoose={async (action, option) => onUpdate(await makerApi.limitChoice(video.id, action, option))}
          onCancel={async () => onUpdate(await makerApi.cancel(video.id))}
          cancelPrompt={`Stop making “${video.title}”? Narration already recorded is kept for a retry.`} />
      )}
    </article>
  )
}
