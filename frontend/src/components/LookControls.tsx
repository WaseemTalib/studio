import { captionPreviewUrl, type Aspect, type CaptionStyleInfo, type Layout, type LookOptions, type Position, type Presets, type Resolution } from '../api'

const SWATCHES = ['#04f827', '#fffd03', '#facc15', '#22d3ee', '#f472b6', '#8f2eed', '#fb923c', '#ffffff']

export const LAYOUTS: { id: Layout; label: string; hint: string }[] = [
  { id: 'auto', label: 'Auto', hint: 'Detects speakers; two people get a shared shot, stacked or side by side' },
  { id: 'track', label: 'Follow speaker', hint: 'Crops around the main face' },
  { id: 'split', label: 'Split screen', hint: 'Two people stacked or side by side, whichever frames them better' },
  { id: 'fit', label: 'Fit + blur', hint: 'Whole frame over a blurred background' },
  { id: 'fill', label: 'Center crop', hint: 'Fills the frame from the center' },
]

export const LAYOUT_LABEL: Record<string, string> = {
  ...Object.fromEntries(LAYOUTS.map((l) => [l.id, l.label])),
  side: 'Side by side',
  group: 'Shared shot',
}

/** Label for a rendered layout; clips with camera cuts report one layout per shot, e.g. "split+track". */
export function layoutLabel(id: string | null | undefined): string {
  if (!id) return '—'
  return id.split('+').map((part) => LAYOUT_LABEL[part] ?? part).join(' + ')
}

type Section = 'captions' | 'layout' | 'title'

const STYLE_GROUPS: { id: CaptionStyleInfo['group']; label: string }[] = [
  { id: 'opus', label: 'Creator presets' },
  { id: 'classic', label: 'Classic' },
  { id: 'more', label: 'More animated' },
]
const NO_CAPTIONS: CaptionStyleInfo = { id: 'none', label: 'No captions', animated: false, group: 'classic', highlight: '#facc15' }

interface Props {
  value: LookOptions
  onChange: (next: LookOptions) => void
  presets: Presets
  sections?: Section[]
}

export default function LookControls({ value, onChange, presets, sections = ['captions', 'layout', 'title'] }: Props) {
  const set = <K extends keyof LookOptions>(key: K, v: LookOptions[K]) => onChange({ ...value, [key]: v })

  return (
    <>
      {sections.includes('layout') && (
        <>
          <div className="field">
            <span>Aspect ratio</span>
            <div className="segmented">
              {(['9:16', '1:1', '16:9'] as Aspect[]).map((a) => (
                <button type="button" key={a} className={value.aspect === a ? 'on' : ''} onClick={() => set('aspect', a)}>
                  {a}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <span>Layout</span>
            <div className="layout-grid">
              {LAYOUTS.map((l) => (
                <button type="button" key={l.id} className={`layout-card ${value.layout === l.id ? 'on' : ''}`}
                  onClick={() => set('layout', l.id)}>
                  <LayoutIcon id={l.id} />
                  <strong>{l.label}</strong>
                  <small>{l.hint}</small>
                </button>
              ))}
            </div>
          </div>
          <div className="row">
            <label className="field check">
              <input type="checkbox" checked={value.auto_zoom} onChange={(e) => set('auto_zoom', e.target.checked)} />
              Auto zoom <em>(punch in on alternate sentences)</em>
            </label>
            <label className="field">
              <span>Export quality</span>
              <select value={value.resolution} onChange={(e) => set('resolution', e.target.value as Resolution)}>
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
                <option value="4k">4K (slower)</option>
              </select>
            </label>
          </div>
        </>
      )}

      {sections.includes('captions') && (
        <>
          <div className="field">
            <span>Caption style</span>
            {STYLE_GROUPS.map((g) => {
              const styles = [...presets.caption_styles, NO_CAPTIONS].filter((s) => s.group === g.id)
              if (!styles.length) return null
              return (
                <div key={g.id} className="style-group">
                  <span className="style-group-label">{g.label}</span>
                  <div className="style-grid">
                    {styles.map((s) => {
                      const selected = value.caption_style === s.id
                      return (
                        <button type="button" key={s.id} className={`style-card ${selected ? 'on' : ''}`}
                          onClick={() => onChange({ ...value, caption_style: s.id, highlight_color: s.highlight })}>
                          {/* Each tile shows its own colours; the selected one shows the colour in use. */}
                          <img src={captionPreviewUrl({
                            ...value, caption_style: s.id, aspect: '9:16', title_overlay: false,
                            highlight_color: selected ? value.highlight_color : s.highlight,
                          })} alt="" loading="lazy" />
                          <span>{s.label}{s.animated && <em className="anim-badge">animated</em>}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
          <div className="field">
            <span>Highlight colour</span>
            <div className="swatches">
              {SWATCHES.map((c) => (
                <button type="button" key={c} aria-label={c} className={`swatch ${value.highlight_color === c ? 'on' : ''}`}
                  style={{ background: c }} onClick={() => set('highlight_color', c)} />
              ))}
              <input type="color" value={value.highlight_color} onChange={(e) => set('highlight_color', e.target.value)} />
            </div>
          </div>
          <div className="row">
            <div className="field">
              <span>Caption position</span>
              <div className="segmented">
                {(['top', 'middle', 'bottom'] as Position[]).map((p) => (
                  <button type="button" key={p} className={value.caption_position === p ? 'on' : ''}
                    onClick={() => set('caption_position', p)}>{p}</button>
                ))}
              </div>
            </div>
            <label className="field">
              <span>Words per caption</span>
              <select value={value.words_per_line} onChange={(e) => set('words_per_line', Number(e.target.value))}>
                {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          </div>
        </>
      )}

      {sections.includes('title') && (
        <label className="field check">
          <input type="checkbox" checked={value.title_overlay} onChange={(e) => set('title_overlay', e.target.checked)} />
          Show title for the first 3s <em>(uses the caption style and colour)</em>
        </label>
      )}
    </>
  )
}

/** Small schematic of each layout inside a 9:16 frame. */
function LayoutIcon({ id }: { id: Layout }) {
  const head = (cx: number, cy: number, r = 5) => (
    <>
      <circle cx={cx} cy={cy} r={r} />
      <path d={`M${cx - r * 1.8} ${cy + r * 3.2} Q${cx} ${cy + r * 0.9} ${cx + r * 1.8} ${cy + r * 3.2}`} />
    </>
  )
  return (
    <svg viewBox="0 0 36 64" className="layout-icon" aria-hidden>
      <rect x="1" y="1" width="34" height="62" rx="4" className="frame" />
      {id === 'split' && (
        <>
          <line x1="1" y1="32" x2="35" y2="32" className="divider" />
          {head(18, 13)}
          {head(18, 45)}
        </>
      )}
      {(id === 'track' || id === 'auto') && head(18, 26, 7)}
      {id === 'fit' && (
        <>
          <rect x="1" y="22" width="34" height="20" className="inner" />
          {head(11, 29, 3)}
          {head(25, 29, 3)}
        </>
      )}
      {id === 'fill' && (
        <>
          {head(4, 26, 7)}
          {head(32, 26, 7)}
        </>
      )}
      {id === 'auto' && <text x="18" y="58" textAnchor="middle">AI</text>}
    </svg>
  )
}
