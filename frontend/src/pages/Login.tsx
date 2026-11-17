import { useState, type FormEvent } from 'react'
import { authApi, type AuthStatus } from '../auth'
import PasswordInput from '../components/PasswordInput'

/** Log in, or create the first admin account when the app has no users yet. */
export default function Login({ setup, notice, onDone }: {
  setup: boolean
  notice: string | null
  onDone: (s: AuthStatus) => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (setup && password !== confirm) return setError('The passwords do not match')
    setBusy(true)
    setError(null)
    try {
      onDone(await (setup ? authApi.setup(username.trim(), password) : authApi.login(username.trim(), password)))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-screen">
      <form className="panel auth-card" onSubmit={submit}>
        <div className="brand"><span className="brand-mark">▶</span> Shortcut Studio</div>
        <div>
          <h1>{setup ? 'Create the admin account' : 'Log in'}</h1>
          <p className="muted small">
            {setup ? 'No users exist yet. This account can add more users and choose what each may use.'
              : 'Sign in to continue.'}
          </p>
        </div>
        {notice && !setup && <div className="notice">{notice}</div>}
        <label className="field">Username
          <input className="text" autoComplete="username" autoFocus required value={username}
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">Password
          <PasswordInput autoComplete={setup ? 'new-password' : 'current-password'} required
            minLength={setup ? 8 : undefined} value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {setup && (
          <label className="field">Confirm password
            <PasswordInput autoComplete="new-password" required minLength={8} value={confirm}
              onChange={(e) => setConfirm(e.target.value)} />
          </label>
        )}
        {error && <p className="error small">{error}</p>}
        <button className="primary big" disabled={busy}>{busy ? 'Please wait…' : setup ? 'Create account' : 'Log in'}</button>
      </form>
    </div>
  )
}
