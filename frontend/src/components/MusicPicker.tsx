import { useRef, useState } from 'react'
import { formatTime } from '../api'
import { makerApi, type FoundTrack, type MakerOptions, type MusicMode, type Track } from '../makerApi'

export interface MusicChoice {
  music: string | null
  music_mode: MusicMode
  music_mood: string
  music_volume: number
}

const MODES: { id: MusicMode | 'none'; label: string }[] = [
  { id: 'none', label: 'No music' },
  { id: 'track', label: 'Choose a track' },
  { id: 'random', label: 'Random from my library' },
  { id: 'auto', label: 'Match the script' },
]

/** Background music: none, a track from the library (uploaded or found), a random one, or one found
 *  automatically to match the script's mood. Found tracks are free-licensed and allow commercial use. */
export default function MusicPicker({ options, onOptions, value, onChange, onError }: {
  options: MakerOptions | null
  onOptions: (o: MakerOptions) => void
  value: MusicChoice
  onChange: (next: Partial<MusicChoice>) => void
  onError: (m: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  const tracks = options?.music ?? []
  const mode: MusicMode | 'none' = value.music_mode === 'track' && !value.music ? 'none' : value.music_mode
  const current = tracks.find((t) => t.name === value.music)

  async function upload(file: File | undefined) {
    if (!file || !options) return
    setUploading(true)
    try {
      const music = await makerApi.uploadMusic(file)
      onOptions({ ...options, music })
      const added = music.find((t) => !tracks.some((x) => x.name === t.name))
      if (added) onChange({ music: added.name, music_mode: 'track' })
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
      if (value.music === name) onChange({ music: null })
    } catch (e) {
      onError((e as Error).message)
    }
  }

  function setMode(m: MusicMode | 'none') {
    if (m === 'none') onChange({ music_mode: 'track', music: null })
    else if (m === 'track') onChange({ music_mode: 'track', music: value.music ?? tracks[0]?.name ?? null })
    else onChange({ music_mode: m })
  }

  return (
    <>
      <div className="segmented wrap">
        {MODES.map((m) => (
          <button type="button" key={m.id} className={mode === m.id ? 'on' : ''} onClick={() => setMode(m.id)}
            disabled={m.id === 'random' && !tracks.length} title={m.id === 'random' && !tracks.length ? 'Your library is empty' : ''}>
            {m.label}
          </button>
        ))}
      </div>

      {mode === 'track' && (
        <div className="row wrap">
          <label className="field grow">
            <span>Track</span>
            <select value={value.music ?? ''} onChange={(e) => onChange({ music: e.target.value || null })}>
              <option value="">—</option>
              {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
          </label>
          <label className="button align-end">
            {uploading ? 'Uploading…' : 'Upload a track'}
            <input type="file" hidden accept="audio/*,.mp3,.m4a,.wav,.ogg,.flac,.aac" onChange={(e) => upload(e.target.files?.[0])} />
          </label>
          {current && <button type="button" className="link danger align-end" onClick={() => remove(current.name)}>Delete track</button>}
        </div>
      )}
      {mode === 'track' && current && (
        <>
          <audio controls src={current.url} className="maker-audio" />
          {current.credit && <Credit track={current} />}
        </>
      )}
      {mode === 'random' && <p className="muted small">Each video gets a random track from your library of {tracks.length}.</p>}
      {mode === 'auto' && (
        <label className="field">
          <span>Music mood <em className="muted">(suggested by the AI with the script)</em></span>
          <input value={value.music_mood} maxLength={100} placeholder="e.g. calm ambient piano, dark suspense, upbeat corporate"
            onChange={(e) => onChange({ music_mood: e.target.value })} />
          <span className="muted small">A free track matching this mood is found and added to your library when the video is made.</span>
        </label>
      )}

      {mode !== 'none' && (
        <label className="field">
          <span>Music volume: {Math.round(value.music_volume * 100)}%</span>
          <input type="range" min={0} max={0.6} step={0.02} value={value.music_volume}
            onChange={(e) => onChange({ music_volume: Number(e.target.value) })} />
        </label>
      )}

      {(mode === 'track' || mode === 'none') && (
        <MusicSearch options={options} mood={value.music_mood} onError={onError}
          onAdded={(name, library) => {
            if (options) onOptions({ ...options, music: library })
            onChange({ music: name, music_mode: 'track' })
          }} />
      )}
    </>
  )
}

function Credit({ track }: { track: Track }) {
  const c = track.credit!
  return (
    <p className="muted small">
      {c.title ? `“${c.title}”` : track.name} by {c.author || 'unknown'} · {c.license}
      {c.page && <> · <a href={c.page} target="_blank" rel="noreferrer">source</a></>} · credited in the video’s credits
    </p>
  )
}

/** Search free music (Jamendo or Wikimedia Commons), preview it, and add it to the library. */
function MusicSearch({ options, mood, onAdded, onError }: {
  options: MakerOptions | null
  mood: string
  onAdded: (name: string, library: Track[]) => void
  onError: (m: string) => void
}) {
  const sources = options?.music_sources ?? { jamendo: false, commons: true }
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(mood)
  const [source, setSource] = useState<'jamendo' | 'commons'>(sources.jamendo ? 'jamendo' : 'commons')
  const [instrumental, setInstrumental] = useState(true)
  const [results, setResults] = useState<FoundTrack[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const player = useRef<HTMLAudioElement | null>(null)

  if (!open) {
    return (
      <button type="button" className="link small align-start" onClick={() => { setQuery(mood); setOpen(true) }}>
        Find free music (Jamendo, Wikimedia Commons)…
      </button>
    )
  }

  async function search(e?: React.FormEvent) {
    e?.preventDefault()
    if (!query.trim()) return
    setBusy(true)
    try {
      setResults(await makerApi.searchMusic(query.trim(), source, instrumental))
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function play(t: FoundTrack) {
    player.current?.pause()
    if (playing === t.id) {
      setPlaying(null)
      return
    }
    player.current = new Audio(t.preview)
    player.current.onended = () => setPlaying(null)
    player.current.play().catch(() => onError('Could not play the preview'))
    setPlaying(t.id)
  }

  async function add(t: FoundTrack) {
    setAdding(t.id)
    try {
      const r = await makerApi.addMusic(t)
      player.current?.pause()
      setPlaying(null)
      onAdded(r.name, r.library)
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setAdding(null)
    }
  }

  return (
    <div className="music-search">
      <form className="row wrap" onSubmit={search}>
        <input className="grow-input" value={query} placeholder="Mood or genre, e.g. calm piano, epic cinematic, lo-fi"
          onChange={(e) => setQuery(e.target.value)} />
        <select value={source} onChange={(e) => setSource(e.target.value as 'jamendo' | 'commons')}>
          <option value="jamendo" disabled={!sources.jamendo}>Jamendo{sources.jamendo ? '' : ' — needs JAMENDO_CLIENT_ID'}</option>
          <option value="commons">Wikimedia Commons</option>
        </select>
        {source === 'jamendo' && (
          <label className="field check">
            <input type="checkbox" checked={instrumental} onChange={(e) => setInstrumental(e.target.checked)} /> Instrumental
          </label>
        )}
        <button type="submit" disabled={busy || !query.trim()}>{busy ? 'Searching…' : 'Search'}</button>
      </form>
      <p className="muted small">Only tracks whose licence allows commercial use are shown. Their artist and licence go into the video’s credits.</p>
      {results && (
        <ul className="music-results">
          {results.map((t) => (
            <li key={t.id}>
              <button type="button" className="ghost" onClick={() => play(t)} aria-label={playing === t.id ? 'Stop' : 'Play'}>
                {playing === t.id ? '■' : '▶'}
              </button>
              <span className="music-result-text">
                <strong>{t.title}</strong>
                <span className="muted small">{t.author || 'Unknown'} · {formatTime(t.duration)} · {t.license}{t.tags ? ` · ${t.tags}` : ''}</span>
              </span>
              <button type="button" className="small-btn" disabled={adding !== null} onClick={() => add(t)}>
                {adding === t.id ? 'Adding…' : 'Add & use'}
              </button>
            </li>
          ))}
          {!results.length && <li className="muted">Nothing found; try other words.</li>}
        </ul>
      )}
    </div>
  )
}
