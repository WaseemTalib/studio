import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatTime } from '../api'
import { usePolling } from '../usePolling'
import {
  DEFAULT_SETTINGS, orbStyle, voiceApi, voiceTags,
  type Control, type Generation, type ProviderInfo, type TTSModel, type TTSProvider, type Usage, type Voice,
  type VoiceFilters, type VoiceSettings, type VoiceSource,
} from '../voiceApi'

const QUICK_STARTS: { label: string; text: string }[] = [
  { label: 'Narrate a story', text: 'The lighthouse keeper had not seen a ship in eleven years. Then, one foggy night, a light blinked back at him from the sea.' },
  { label: 'Tell a joke', text: "I told my computer I needed a break. Now it won't stop sending me vacation ads." },
  { label: 'Record an ad', text: 'Tired of editing for hours? Drop in your video and get ready-to-post shorts in minutes. Try it free today.' },
  { label: 'Speak another language', text: 'Bonjour à tous ! Aujourd’hui, je vais vous montrer comment créer des vidéos courtes en quelques minutes.' },
  { label: 'Direct a dramatic scene', text: "You said you'd be here. You promised. And now, after everything, you want me to just forget?" },
  { label: 'Voice a game character', text: 'Halt, traveler! None may pass the iron gate without answering the riddle of the mountain.' },
  { label: 'Introduce your podcast', text: "Welcome back to the show. Today we're talking to a founder who turned a weekend side project into a million users." },
  { label: 'Guide a meditation', text: 'Close your eyes. Breathe in slowly through your nose, and let your shoulders soften as you breathe out.' },
]

const SLIDERS: { key: Exclude<Control, 'speaker_boost'>; label: string; min: number; max: number; step: number; ends: [string, string]; hint: string }[] = [
  { key: 'speed', label: 'Speed', min: 0.7, max: 1.2, step: 0.01, ends: ['Slower', 'Faster'], hint: 'How fast the voice speaks.' },
  { key: 'stability', label: 'Stability', min: 0, max: 1, step: 0.01, ends: ['More variable', 'More stable'], hint: 'Lower is more expressive; higher is more consistent but can sound flat.' },
  { key: 'similarity', label: 'Similarity', min: 0, max: 1, step: 0.01, ends: ['Low', 'High'], hint: 'How closely the output follows the original voice.' },
  { key: 'style', label: 'Style exaggeration', min: 0, max: 1, step: 0.01, ends: ['None', 'Exaggerated'], hint: 'Amplifies the speaker’s style. Can slow generation.' },
]

interface Saved {
  provider: TTSProvider
  voice: Record<TTSProvider, Voice | null>
  model: Record<TTSProvider, string | null>
  settings: VoiceSettings
  text: string
}

const STORAGE_KEY = 'voice-studio'

function load(): Partial<Saved> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as Partial<Saved>
  } catch {
    return {}
  }
}

export default function VoiceStudio() {
  const saved = useMemo(load, [])
  const [creating, setCreating] = useState(false)
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null)
  const [provider, setProvider] = useState<TTSProvider>(saved.provider ?? 'elevenlabs')
  const [models, setModels] = useState<TTSModel[]>([])
  const [modelIds, setModelIds] = useState<Record<TTSProvider, string | null>>(saved.model ?? { elevenlabs: null, local: null })
  const [voices, setVoices] = useState<Record<TTSProvider, Voice | null>>(saved.voice ?? { elevenlabs: null, local: null })
  const [settings, setSettings] = useState<VoiceSettings>({ ...DEFAULT_SETTINGS, ...saved.settings })
  const [text, setText] = useState(saved.text ?? '')
  const [tab, setTab] = useState<'settings' | 'history'>('settings')
  const [picking, setPicking] = useState(false)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [history, setHistory] = useState<Generation[]>([])
  const [current, setCurrent] = useState<Generation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const info = providers?.find((p) => p.id === provider)
  const localModel = providers?.find((p) => p.id === 'local')?.model
  const model = models.find((m) => m.id === modelIds[provider]) ?? models[0]
  const voice = voices[provider]

  const refreshProviders = useCallback(() => {
    voiceApi.providers().then((list) => {
      setProviders(list)
      // Fall back to the local engine when ElevenLabs has no key.
      if (!list.find((p) => p.id === 'elevenlabs')?.available) setProvider('local')
    }, (e: Error) => setError(e.message))
  }, [])
  useEffect(refreshProviders, [refreshProviders])
  usePolling(refreshProviders, 1500, localModel?.state === 'downloading')

  useEffect(() => {
    voiceApi.history().then(setHistory, () => undefined)
  }, [])

  useEffect(() => {
    setModels([])
    if (!info?.available) return
    voiceApi.models(provider).then((list) => {
      setModels(list)
      setModelIds((ids) => (ids[provider] && list.some((m) => m.id === ids[provider]) ? ids
        : { ...ids, [provider]: (list.find((m) => m.id === 'eleven_multilingual_v2') ?? list[0])?.id ?? null }))
    }, (e: Error) => setError(e.message))
    if (provider === 'elevenlabs') voiceApi.usage().then(setUsage, () => setUsage(null))
  }, [provider, info?.available])

  // Pick a starting voice: the first default voice of the provider.
  useEffect(() => {
    if (!info?.available || voices[provider]) return
    voiceApi.voices(provider, 'default', {}).then((r) => {
      if (r.voices[0]) setVoices((v) => ({ ...v, [provider]: r.voices[0] }))
    }, () => undefined)
  }, [provider, info?.available, voices])

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ provider, voice: voices, model: modelIds, settings, text }))
    } catch {
      // Private mode or storage blocked: settings just aren't remembered.
    }
  }, [provider, voices, modelIds, settings, text])

  const maxChars = model?.max_chars ?? 5000
  const remaining = usage?.limit != null && usage.used != null ? usage.limit - usage.used : null
  const localReady = provider !== 'local' || localModel?.state === 'ready'
  const canGenerate = !!info?.available && localReady && !!voice && !!model && text.trim().length > 0
    && text.length <= maxChars && !busy

  async function generate() {
    if (!voice || !model) return
    setBusy(true)
    setError(null)
    try {
      const g = await voiceApi.generate({ provider, voice_id: voice.id, voice_name: voice.name, model_id: model.id, text, settings })
      setHistory((h) => [g, ...h])
      setCurrent(g)
      if (provider === 'elevenlabs') voiceApi.usage().then(setUsage, () => undefined)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(g: Generation) {
    try {
      await voiceApi.deleteGeneration(g.id)
      setHistory((h) => h.filter((x) => x.id !== g.id))
      if (current?.id === g.id) setCurrent(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const controls = new Set<Control>(model?.controls ?? ['speed'])

  return (
    <div className="tool-page">
      <div className="tool-page-head">
        <div className="tool-page-copy">
          <h1>Voice Studio</h1>
          <p className="muted">Turn text into natural speech and reuse any voice project.</p>
        </div>
        <button type="button" className={creating ? '' : 'primary'} onClick={() => {
          if (!creating) {
            setText('')
            setTab('settings')
          }
          setCreating(!creating)
          setPicking(false)
        }}>
          {creating ? '← Projects' : '+ Create project'}
        </button>
      </div>
      {!creating && error && <div className="notice error-notice"><p>{error}</p><button onClick={() => setError(null)}>Dismiss</button></div>}
      {!creating ? (
        <section className="panel tool-projects first-load-projects">
          <div className="section-head">
            <h2>Projects</h2>
            {history.length > 0 && <span className="muted small">{history.length} total</span>}
          </div>
          <HistoryList items={history} current={current} onPlay={setCurrent} onDelete={remove}
            onReuse={(g) => { setText(g.text); setTab('settings'); setCreating(true) }} />
        </section>
      ) : <div className={`vs ${current ? 'with-player' : ''}`}>
      <section className="vs-editor">
        <div className="vs-head">
          <h2>Text to Speech</h2>
          <div className="segmented">
            {(providers ?? []).map((p) => (
              <button type="button" key={p.id} className={provider === p.id ? 'on' : ''} onClick={() => setProvider(p.id)}
                title={p.note ?? undefined}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {info && !info.available && (
          <div className="notice"><p>{info.note}</p></div>
        )}
        {provider === 'local' && localModel && localModel.state !== 'ready' && (
          <div className="notice">
            {localModel.state === 'downloading' ? (
              <p>Downloading the local voice model… {Math.round(localModel.done / 1e6)} / {Math.round(localModel.total / 1e6) || '…'} MB</p>
            ) : (
              <>
                <p>{localModel.error ?? 'The local voice model (about 350 MB) is downloaded once, then runs offline.'}</p>
                <button onClick={() => voiceApi.downloadLocalModel().then(refreshProviders, (e: Error) => setError(e.message))}>
                  Download model
                </button>
              </>
            )}
          </div>
        )}

        <div className="vs-text">
          <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck
            placeholder="Start typing here or paste any text you want to turn into lifelike speech…" />
          {!text && (
            <div className="vs-quick">
              <span className="muted small">Get started with</span>
              <div className="vs-chips">
                {QUICK_STARTS.map((q) => (
                  <button type="button" key={q.label} className="chip" onClick={() => setText(q.text)}>{q.label}</button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="vs-foot">
          <span className="muted small">
            {provider === 'elevenlabs' && remaining != null ? `${remaining.toLocaleString()} credits remaining` : provider === 'local' ? 'Free · runs on this computer' : ''}
          </span>
          <span className={`small ${text.length > maxChars ? 'error' : 'muted'}`}>
            {text.length.toLocaleString()} / {maxChars.toLocaleString()}
          </span>
          <button className="primary" disabled={!canGenerate} onClick={generate}>
            {busy ? 'Generating…' : 'Generate speech'}
          </button>
        </div>
        {error && <p className="error small">{error}</p>}
      </section>

      <aside className="vs-side">
        <div className="vs-tabs">
          <button type="button" className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>Settings</button>
          <button type="button" className={tab === 'history' ? 'on' : ''} onClick={() => setTab('history')}>History</button>
        </div>

        {tab === 'settings' ? (
          <div className="vs-settings">
            <label className="vs-label">Voice</label>
            <button type="button" className="vs-select" onClick={() => setPicking(true)} disabled={!info?.available}>
              {voice ? <span className="orb" style={orbStyle(voice.id)} /> : <span className="orb empty" />}
              <span className="vs-select-text">
                <strong>{voice?.name ?? 'Choose a voice'}</strong>
                {voice && <span className="muted small">{voiceTags(voice).slice(0, 3).join(' · ')}</span>}
              </span>
              <span className="muted">›</span>
            </button>

            <label className="vs-label">Model</label>
            <select value={model?.id ?? ''} onChange={(e) => setModelIds((ids) => ({ ...ids, [provider]: e.target.value }))}
              disabled={!models.length}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
            {model?.description && <p className="muted small vs-model-desc">{model.description}</p>}

            {SLIDERS.filter((s) => controls.has(s.key)).map((s) => (
              <div key={s.key} className="vs-slider" title={s.hint}>
                <div className="vs-slider-head">
                  <span>{s.label}</span>
                  <span className="muted small">{settings[s.key].toFixed(2)}</span>
                </div>
                <input type="range" min={s.min} max={s.max} step={s.step} value={settings[s.key]}
                  onChange={(e) => setSettings({ ...settings, [s.key]: Number(e.target.value) })} />
                <div className="vs-slider-ends muted small"><span>{s.ends[0]}</span><span>{s.ends[1]}</span></div>
              </div>
            ))}
            {controls.has('speaker_boost') && (
              <label className="field check vs-toggle">
                <input type="checkbox" checked={settings.speaker_boost}
                  onChange={(e) => setSettings({ ...settings, speaker_boost: e.target.checked })} />
                Speaker boost
              </label>
            )}
            <button type="button" className="ghost vs-reset" onClick={() => setSettings(DEFAULT_SETTINGS)}>↺ Reset values</button>
          </div>
        ) : (
          <HistoryList items={history} current={current} onPlay={setCurrent} onDelete={remove}
            onReuse={(g) => { setText(g.text); setTab('settings') }} />
        )}
      </aside>

      {picking && info?.available && (
        <VoicePicker provider={provider} selected={voice} onClose={() => setPicking(false)}
          onPick={(v) => { setVoices((all) => ({ ...all, [provider]: v })); setPicking(false) }} />
      )}
      </div>}
      {current && <Player key={current.id} generation={current} onClose={() => setCurrent(null)} />}
    </div>
  )
}

// --- Voice picker -----------------------------------------------------------------

const LIBRARY_LANGUAGES: [string, string][] = [
  ['', 'Any language'], ['en', 'English'], ['es', 'Spanish'], ['fr', 'French'], ['de', 'German'], ['it', 'Italian'],
  ['pt', 'Portuguese'], ['pl', 'Polish'], ['nl', 'Dutch'], ['hi', 'Hindi'], ['ar', 'Arabic'], ['ja', 'Japanese'],
  ['zh', 'Chinese'], ['ko', 'Korean'], ['tr', 'Turkish'], ['ru', 'Russian'], ['sv', 'Swedish'], ['id', 'Indonesian'],
  ['fil', 'Filipino'], ['uk', 'Ukrainian'], ['ta', 'Tamil'], ['ur', 'Urdu'], ['vi', 'Vietnamese'],
]
const USE_CASES: [string, string][] = [
  ['', 'Any use case'], ['narrative_story', 'Narrative & story'], ['conversational', 'Conversational'],
  ['characters_animation', 'Characters & animation'], ['social_media', 'Social media'],
  ['entertainment_tv', 'Entertainment & TV'], ['advertisement', 'Advertisement'],
  ['informative_educational', 'Informative & educational'],
]
const SORTS: [string, string][] = [
  ['cloned_by_count', 'Most users'], ['trending', 'Trending'], ['usage_character_count_1y', 'Most used'],
  ['created_date', 'Latest'],
]
const LOCAL_LANGUAGES = ['', 'English', 'Spanish', 'French', 'Hindi', 'Italian', 'Portuguese', 'Chinese']

function VoicePicker({ provider, selected, onPick, onClose }: {
  provider: TTSProvider
  selected: Voice | null
  onPick: (v: Voice) => void
  onClose: () => void
}) {
  const [source, setSource] = useState<VoiceSource>('default')
  const [filters, setFilters] = useState<VoiceFilters>({ sort: 'cloned_by_count' })
  const [list, setList] = useState<Voice[] | null>(null)
  const [next, setNext] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    let stale = false
    setList(null)
    setError(null)
    const t = setTimeout(() => {
      voiceApi.voices(provider, source, filters).then((r) => {
        if (stale) return
        setList(r.voices)
        setNext(r.next)
      }, (e: Error) => !stale && setError(e.message))
    }, 250)  // debounce typing in the search box
    return () => { stale = true; clearTimeout(t) }
  }, [provider, source, filters])

  useEffect(() => () => audio.current?.pause(), [])

  function more() {
    voiceApi.voices(provider, source, filters, next).then((r) => {
      setList((l) => [...(l ?? []), ...r.voices])
      setNext(r.next)
    }, (e: Error) => setError(e.message))
  }

  function preview(v: Voice) {
    audio.current?.pause()
    if (playing === v.id || !v.preview_url) {
      setPlaying(null)
      return
    }
    const a = new Audio(v.preview_url)
    a.onended = () => setPlaying(null)
    a.onerror = () => setPlaying(null)
    audio.current = a
    setPlaying(v.id)
    a.play().catch(() => setPlaying(null))
  }

  async function use(v: Voice) {
    if (v.in_library) {
      onPick(v)
      return
    }
    // A community voice is first added to the user's ElevenLabs account; only on this explicit click.
    setAdding(v.id)
    try {
      const { voice_id } = await voiceApi.addLibraryVoice(v)
      onPick({ ...v, id: voice_id, in_library: true, public_owner_id: null })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAdding(null)
    }
  }

  const set = (key: keyof VoiceFilters, value: string) => setFilters((f) => ({ ...f, [key]: value || undefined }))

  return (
    <div className="vs-modal-backdrop" onClick={onClose}>
      <div className="vs-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Choose a voice">
        <div className="vs-modal-head">
          <h2>Voices</h2>
          <button type="button" className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {provider === 'elevenlabs' && (
          <div className="vs-tabs">
            <button type="button" className={source === 'default' ? 'on' : ''} onClick={() => setSource('default')}>Default</button>
            <button type="button" className={source === 'mine' ? 'on' : ''} onClick={() => setSource('mine')}>My voices</button>
            <button type="button" className={source === 'library' ? 'on' : ''} onClick={() => setSource('library')}>Voice library</button>
          </div>
        )}

        <div className="vs-filters">
          <input type="search" placeholder="Search voices…" value={filters.search ?? ''} onChange={(e) => set('search', e.target.value)} />
          {provider === 'local' && (
            <>
              <select value={filters.language ?? ''} onChange={(e) => set('language', e.target.value)}>
                {LOCAL_LANGUAGES.map((l) => <option key={l} value={l}>{l || 'Any language'}</option>)}
              </select>
              <select value={filters.gender ?? ''} onChange={(e) => set('gender', e.target.value)}>
                <option value="">Any gender</option><option value="female">Female</option><option value="male">Male</option>
              </select>
            </>
          )}
          {provider === 'elevenlabs' && source === 'library' && (
            <>
              <select value={filters.category ?? ''} onChange={(e) => set('category', e.target.value)}>
                <option value="">All voices</option>
                <option value="famous">Iconic</option>
                <option value="high_quality">High quality</option>
                <option value="professional">Professional</option>
              </select>
              <select value={filters.language ?? ''} onChange={(e) => set('language', e.target.value)}>
                {LIBRARY_LANGUAGES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
              <select value={filters.gender ?? ''} onChange={(e) => set('gender', e.target.value)}>
                <option value="">Any gender</option><option value="female">Female</option>
                <option value="male">Male</option><option value="neutral">Neutral</option>
              </select>
              <select value={filters.age ?? ''} onChange={(e) => set('age', e.target.value)}>
                <option value="">Any age</option><option value="young">Young</option>
                <option value="middle_aged">Middle aged</option><option value="old">Old</option>
              </select>
              <input placeholder="Accent (e.g. british)" value={filters.accent ?? ''} onChange={(e) => set('accent', e.target.value)} />
              <select value={filters.use_case ?? ''} onChange={(e) => set('use_case', e.target.value)}>
                {USE_CASES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
              <select value={filters.sort ?? 'cloned_by_count'} onChange={(e) => set('sort', e.target.value)}>
                {SORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </>
          )}
        </div>

        {error && <p className="error small">{error}</p>}
        <div className="vs-voice-list">
          {list === null && !error && <p className="muted">Loading voices…</p>}
          {list?.length === 0 && <p className="muted">No voices match.</p>}
          {list?.map((v) => (
            <div key={`${v.public_owner_id ?? ''}${v.id}`} className={`vs-voice ${selected?.id === v.id ? 'on' : ''}`}>
              <button type="button" className="orb-btn" onClick={() => preview(v)} disabled={!v.preview_url}
                aria-label={playing === v.id ? 'Stop preview' : 'Play preview'}>
                <span className="orb" style={orbStyle(v.id)} />
                <span className="orb-icon">{playing === v.id ? '❚❚' : '▶'}</span>
              </button>
              <div className="vs-voice-text">
                <strong>{v.name}</strong>
                <span className="muted small vs-voice-desc">{v.description}</span>
                <span className="vs-tags">
                  {voiceTags(v).slice(0, 4).map((t) => <span key={t} className="tag">{t}</span>)}
                  {v.popularity ? <span className="tag">{v.popularity.toLocaleString()} users</span> : null}
                </span>
              </div>
              <button type="button" className={selected?.id === v.id ? '' : 'primary'} disabled={adding === v.id}
                onClick={() => use(v)}>
                {selected?.id === v.id ? 'Selected' : adding === v.id ? 'Adding…' : v.in_library ? 'Use' : 'Add & use'}
              </button>
            </div>
          ))}
          {next && <button type="button" className="ghost vs-more" onClick={more}>Load more</button>}
        </div>
      </div>
    </div>
  )
}

// --- History and player -------------------------------------------------------------

function HistoryList({ items, current, onPlay, onDelete, onReuse }: {
  items: Generation[]
  current: Generation | null
  onPlay: (g: Generation) => void
  onDelete: (g: Generation) => void
  onReuse: (g: Generation) => void
}) {
  if (!items.length) return <p className="muted small vs-empty">Generated speech will appear here.</p>
  const day = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
  let lastDay = ''
  return (
    <div className="vs-history">
      {items.map((g) => {
        const d = day(g.created_at)
        const header = d !== lastDay ? <div className="vs-day muted small">{d}</div> : null
        lastDay = d
        return (
          <div key={g.id}>
            {header}
            <div className={`vs-gen ${current?.id === g.id ? 'on' : ''}`}>
              <button type="button" className="orb-btn" onClick={() => onPlay(g)} aria-label="Play">
                <span className="orb" style={orbStyle(g.voice_id)} />
                <span className="orb-icon">▶</span>
              </button>
              <div className="vs-gen-text" onClick={() => onReuse(g)} title="Use this text again">
                <span className="vs-gen-snippet">{g.text}</span>
                <span className="muted small">
                  {g.voice_name} · {new Date(g.created_at * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
                  {g.duration ? ` · ${formatTime(g.duration)}` : ''}
                </span>
              </div>
              <a className="button ghost small-btn" href={g.audio} download={`${g.voice_name} - ${g.text.slice(0, 40)}.mp3`} title="Download">⬇</a>
              <button type="button" className="ghost small-btn" onClick={() => onDelete(g)} title="Delete">✕</button>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Player({ generation: g, onClose }: { generation: Generation; onClose: () => void }) {
  const audio = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(g.duration ?? 0)

  useEffect(() => {
    audio.current?.play().catch(() => undefined)  // play a new generation right away
  }, [])

  const toggle = () => {
    const a = audio.current
    if (!a) return
    if (a.paused) a.play().catch(() => undefined)
    else a.pause()
  }

  return (
    <div className="vs-player">
      <audio ref={audio} src={g.audio} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)} />
      <span className="orb" style={orbStyle(g.voice_id)} />
      <div className="vs-player-meta">
        <strong>{g.voice_name}</strong>
        <span className="muted small vs-gen-snippet">{g.text}</span>
      </div>
      <button type="button" className="vs-play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
      <span className="muted small">{formatTime(time)}</span>
      <input className="vs-seek" type="range" min={0} max={duration || 0} step={0.01} value={time}
        onChange={(e) => { if (audio.current) audio.current.currentTime = Number(e.target.value) }} />
      <span className="muted small">{formatTime(duration)}</span>
      <a className="button ghost small-btn" href={g.audio} download={`${g.voice_name} - ${g.text.slice(0, 40)}.mp3`}>⬇ Download</a>
      <button type="button" className="ghost small-btn" onClick={onClose} aria-label="Close player">✕</button>
    </div>
  )
}
