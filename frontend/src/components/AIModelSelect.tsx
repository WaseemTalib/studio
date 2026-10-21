import type { AIServices, ChainStatus } from '../api'
import { remember } from '../aiServices'
import { formatWait } from '../useCountdown'

const SHORT: Record<string, string> = {
  auto: 'Automatic', groq: 'Groq', claude: 'Claude (AgentRouter)', deepseek: 'DeepSeek (AgentRouter)',
  'deepseek-api': 'DeepSeek (personal)', gemini: 'Gemini', openrouter: 'OpenRouter',
  sambanova: 'SambaNova',
}

/** "Claude unavailable, rechecked in 8m 10s · DeepSeek in use · Groq ready" */
function ChainLine({ chain }: { chain: ChainStatus[] }) {
  const shown = chain.filter((c) => c.state !== 'no key')
  if (!shown.length) return null
  return (
    <span className="muted small ai-chain">
      {shown.map((c, i) => (
        <span key={c.service} className={`ai-chain-item ${c.state.replace(' ', '-')}`} title={c.reason}>
          {i > 0 && ' → '}{c.label}{' '}
          {c.state === 'down' ? `unavailable${c.retry_in != null ? `, rechecked in ${formatWait(c.retry_in)}` : ''}`
            : c.state === 'in use' ? '✓ in use' : 'ready'}
        </span>
      ))}
    </span>
  )
}

/** Picks the AI model for writing scripts or finding highlights. The choice is shared across pages. */
export default function AIModelSelect({ value, onChange, services, label = 'AI model' }: {
  value: string | null
  onChange: (service: string | null) => void
  services: AIServices | null
  label?: string
}) {
  const fallback = services?.default ? SHORT[services.default.split(':')[0]] ?? services.default : null
  const chosen = services?.services.find((s) => s.id === value)
  const automatic = value === 'auto' || (!value && services?.default === 'auto')
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
      {automatic && services && <ChainLine chain={services.chain} />}
    </label>
  )
}
