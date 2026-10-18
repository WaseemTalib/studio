import type { AIServices } from '../api'
import { remember } from '../aiServices'

const SHORT: Record<string, string> = { groq: 'Groq', gemini: 'Gemini', openrouter: 'OpenRouter', sambanova: 'SambaNova' }

/** Picks the AI model for writing scripts or finding highlights. The choice is shared across pages. */
export default function AIModelSelect({ value, onChange, services, label = 'AI model' }: {
  value: string | null
  onChange: (service: string | null) => void
  services: AIServices | null
  label?: string
}) {
  const fallback = services?.default ? SHORT[services.default.split(':')[0]] ?? services.default : null
  const chosen = services?.services.find((s) => s.id === value)
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value ?? ''} onChange={(e) => {
        const v = e.target.value || null
        remember(v)
        onChange(v)
      }}>
        <option value="">Default{fallback ? ` (${fallback})` : ''}</option>
        {services?.services.map((s) => (
          <option key={s.id} value={s.id} disabled={!s.available}>
            {s.label}{s.available ? '' : ' — needs key'}
          </option>
        ))}
        {value && !chosen && services && <option value={value}>{value}</option>}
      </select>
    </label>
  )
}
