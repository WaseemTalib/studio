import { useState } from 'react'
import { FOOTAGE_SOURCES, makerApi, type FootageSource, type Media, type Scene, type VideoRequest } from '../makerApi'

/** Suggestions per scene (by its uid) from the last footage search. */
export type Suggestions = Record<string, { shots: number; candidates: Media[] }>

const PROVIDER: Record<Media['provider'], string> = {
  pexels: 'Pexels', pixabay: 'Pixabay', coverr: 'Coverr', commons: 'Commons', wikimedia: 'Wikimedia',
  unsplash: 'Unsplash', upload: 'Yours',
}

type PickerSource = 'all' | FootageSource | 'photos' | 'uploads'

/** The footage each scene will use: thumbnails to check, swap, add or remove before rendering. */
export default function FootageReview({ scenes, suggestions, sources, aspect, resolution, busy, onFind, onChange }: {
  scenes: Scene[]
  suggestions: Suggestions
  sources: FootageSource[]
  aspect: VideoRequest['aspect']
  resolution: VideoRequest['resolution']
  busy: number | 'all' | null
  onFind: (only?: number) => void
  onChange: (index: number, clips: Media[]) => void
}) {
  const [picking, setPicking] = useState<{ scene: number; slot: number | null } | null>(null)
  const found = scenes.some((s) => s.uid! in suggestions)
  return (
    <div className="footage-review">
      <div className="row">
        <button type="button" className="primary" disabled={busy !== null} onClick={() => onFind()}>
          {busy === 'all' ? 'Finding footage…' : found ? 'Find footage again' : 'Find footage'}
        </button>
        <span className="muted small">
          {found ? 'Hover to preview, click a shot to swap it. Scenes left empty get footage picked automatically.'
            : 'Optional: see and change the footage for every scene before the video is made. Otherwise it is picked automatically.'}
        </span>
      </div>
      {found && (
        <ol className="footage-scenes">
          {scenes.map((scene, i) => {
            const info = suggestions[scene.uid!]
            const needed = info?.shots ?? 0
            return (
              <li key={i} className="footage-scene">
                <div className="footage-scene-head">
                  <strong>Scene {i + 1}</strong>
                  <span className="muted small footage-scene-text">{scene.text}</span>
                  <button type="button" className="ghost" title="Find footage for this scene again" disabled={busy !== null}
                    onClick={() => onFind(i)}>{busy === i ? '…' : '↻'}</button>
                </div>
                <div className="footage-strip">
                  {scene.card_text && <div className="footage-card" title="Fact card">{scene.card_text}</div>}
                  {scene.quote && <div className="footage-card quote" title="Quote card">“{scene.quote}”</div>}
                  {scene.clips.map((clip, n) => (
                    <Thumb key={`${clip.key}-${n}`} clip={clip} onClick={() => setPicking({ scene: i, slot: n })}
                      onRemove={() => onChange(i, scene.clips.filter((_, x) => x !== n))} />
                  ))}
                  <button type="button" className="footage-add" onClick={() => setPicking({ scene: i, slot: null })}>
                    ＋<span>{scene.clips.length ? 'Add' : 'Choose'}</span>
                  </button>
                </div>
                <span className="small muted">
                  {info && !scene.clips.length && 'No matching footage found — choose or search for some, or it will use a neighbouring scene’s footage.'}
                  {needed > 0 && scene.clips.length > 0 && scene.clips.length < needed
                    && `${scene.clips.length} of about ${needed} shots chosen; the rest are picked automatically.`}
                </span>
              </li>
            )
          })}
        </ol>
      )}
      {picking && (
        <MediaPicker scene={scenes[picking.scene]} index={picking.scene}
          candidates={suggestions[scenes[picking.scene].uid!]?.candidates ?? []}
          sources={sources} aspect={aspect} resolution={resolution} onClose={() => setPicking(null)}
          onPick={(clip) => {
            const clips = [...scenes[picking.scene].clips]
            if (picking.slot === null) clips.push(clip)
            else clips[picking.slot] = clip
            onChange(picking.scene, clips)
            setPicking(null)
          }} />
      )}
    </div>
  )
}

function Thumb({ clip, onClick, onRemove }: { clip: Media; onClick?: () => void; onRemove?: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <div className={`footage-thumb ${clip.score < 0.66 ? 'weak' : ''}`} onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)} title={clip.text}>
      <button type="button" className="footage-thumb-body" onClick={onClick}>
        {hover && clip.kind === 'video' && clip.preview
          ? <video src={clip.preview} muted autoPlay loop playsInline />
          : <img src={clip.thumb || clip.link} alt="" loading="lazy" />}
        <span className="footage-badge">{clip.kind === 'photo' ? 'Photo' : `${Math.round(clip.duration)}s`} · {PROVIDER[clip.provider]}</span>
      </button>
      {onRemove && <button type="button" className="footage-remove" aria-label="Remove" onClick={onRemove}>×</button>}
    </div>
  )
}

function MediaPicker({ scene, index, candidates, sources, aspect, resolution, onPick, onClose }: {
  scene: Scene
  index: number
  candidates: Media[]
  sources: FootageSource[]
  aspect: VideoRequest['aspect']
  resolution: VideoRequest['resolution']
  onPick: (m: Media) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<PickerSource>(sources.length ? 'all' : 'photos')
  const [results, setResults] = useState<Media[] | null>(null)
  const [mine, setMine] = useState<Media[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function search(e?: React.FormEvent) {
    e?.preventDefault()
    if (source === 'uploads' || !query.trim()) return
    setBusy(true)
    setError(null)
    try {
      const wanted = source === 'all' ? sources : [source]
      setResults(await makerApi.searchFootage(query.trim(), wanted, aspect, resolution))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function chooseSource(next: PickerSource) {
    setSource(next)
    setError(null)
    if (next === 'uploads' && mine === null) {
      try {
        setMine(await makerApi.uploads())
      } catch (err) {
        setError((err as Error).message)
      }
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      setMine(await makerApi.upload(file))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function removeUpload(m: Media) {
    if (!confirm(`Delete “${m.id}” from your uploads?`)) return
    try {
      await makerApi.deleteUpload(m.id)
      setMine((cur) => (cur ?? []).filter((x) => x.id !== m.id))
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const uploadsTab = source === 'uploads'
  const shown = uploadsTab ? mine ?? [] : results ?? candidates
  return (
    <div className="vs-modal-backdrop" onClick={onClose}>
      <div className="vs-modal footage-picker" role="dialog" aria-label="Choose footage" onClick={(e) => e.stopPropagation()}>
        <div className="vs-modal-head">
          <h2>Footage for scene {index + 1}</h2>
          <button type="button" className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted small footage-picker-text">{scene.text}</p>
        <form className="row footage-search" onSubmit={search}>
          <select value={source} onChange={(e) => chooseSource(e.target.value as PickerSource)}>
            {sources.length > 1 && <option value="all">All ticked sources</option>}
            {sources.map((s) => <option key={s} value={s}>{FOOTAGE_SOURCES.find((f) => f.id === s)?.label}</option>)}
            <option value="photos">Photos (Wikimedia Commons)</option>
            <option value="uploads">My uploads</option>
          </select>
          {uploadsTab ? (
            <label className="button">
              {busy ? 'Uploading…' : 'Upload a clip or image'}
              <input type="file" hidden accept="video/*,image/*,.mp4,.mov,.m4v,.webm,.mkv,.jpg,.jpeg,.png,.webp"
                onChange={(e) => upload(e.target.files?.[0])} />
            </label>
          ) : (
            <>
              <input placeholder="Search, e.g. judge gavel, prison cell, police car night" value={query}
                onChange={(e) => setQuery(e.target.value)} autoFocus />
              <button type="submit" disabled={busy || !query.trim()}>{busy ? 'Searching…' : 'Search'}</button>
              {results && <button type="button" className="link" onClick={() => setResults(null)}>Back to suggestions</button>}
            </>
          )}
        </form>
        {error && <p className="error small">{error}</p>}
        <p className="small muted">
          {uploadsTab ? 'Your own clips and images, e.g. from Mixkit, Videvo or your phone. Only use footage you have the rights to.'
            : results ? `${results.length} results for “${query}”. Faded ones only partly match.`
              : `Suggestions from this scene’s searches: ${scene.search_terms.join(', ') || '—'}. Faded ones only partly match.`}
        </p>
        <div className="footage-grid">
          {shown.map((m) => (
            <div key={m.key} className="footage-pick">
              <Thumb clip={m} onClick={() => onPick(m)} onRemove={uploadsTab ? () => removeUpload(m) : undefined} />
              <span className="small muted footage-pick-text">{m.text}</span>
              {m.license && <span className="small muted">{m.license}{m.author ? ` · ${m.author}` : ''}</span>}
            </div>
          ))}
          {!shown.length && (
            <p className="muted">{uploadsTab ? 'No uploads yet.' : results ? 'Nothing found; try other words.' : 'No suggestions yet; search above.'}</p>
          )}
        </div>
      </div>
    </div>
  )
}
