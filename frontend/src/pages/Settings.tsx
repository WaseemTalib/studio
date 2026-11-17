import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { authApi, useAuth, type ActivityItem, type Permission, type Role, type User } from '../auth'

function ChangePassword() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await authApi.changePassword(current, next)
      setCurrent('')
      setNext('')
      setMsg({ ok: true, text: 'Password changed. Other devices were logged out.' })
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message })
    }
  }

  return (
    <form className="panel settings-section" onSubmit={submit}>
      <h2>Change your password</h2>
      <div className="row">
        <label className="field">Current password
          <input className="text" type="password" autoComplete="current-password" required value={current}
            onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">New password
          <input className="text" type="password" autoComplete="new-password" required minLength={8} value={next}
            onChange={(e) => setNext(e.target.value)} />
        </label>
        <button className="primary">Change password</button>
      </div>
      {msg && <p className={`small ${msg.ok ? 'muted' : 'error'}`}>{msg.text}</p>}
    </form>
  )
}

function PermissionPicker({ value, onChange, disabled }: {
  value: Permission[]
  onChange: (v: Permission[]) => void
  disabled?: boolean
}) {
  const { labels } = useAuth()
  return (
    <div className="perm-list">
      {(Object.keys(labels) as Permission[]).map((p) => (
        <label key={p} className="field check">
          <input type="checkbox" disabled={disabled} checked={disabled || value.includes(p)}
            onChange={(e) => onChange(e.target.checked ? [...value, p] : value.filter((x) => x !== p))} />
          {labels[p]}
        </label>
      ))}
    </div>
  )
}

function AddUser({ onAdded }: { onAdded: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('user')
  const [permissions, setPermissions] = useState<Permission[]>([])
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await authApi.addUser({ username: username.trim(), password, role, permissions })
      setUsername('')
      setPassword('')
      setRole('user')
      setPermissions([])
      onAdded()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <form className="user-add" onSubmit={submit}>
      <h3>Add a user</h3>
      <div className="row">
        <label className="field">Username
          <input className="text" required minLength={2} maxLength={40} pattern="[A-Za-z0-9_.@\-]+" value={username}
            title="Letters, numbers and _ . @ -" onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">Password
          <input className="text" type="password" autoComplete="new-password" required minLength={8} value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="field">Role
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="user">User</option>
            <option value="admin">Admin (everything, manages users)</option>
          </select>
        </label>
      </div>
      <PermissionPicker value={permissions} onChange={setPermissions} disabled={role === 'admin'} />
      {error && <p className="error small">{error}</p>}
      <div><button className="primary">Add user</button></div>
    </form>
  )
}

function UserRow({ user, self, onChange }: { user: User; self: boolean; onChange: () => void }) {
  const [permissions, setPermissions] = useState(user.permissions)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setPermissions(user.permissions), [user.permissions])

  async function save(fields: Parameters<typeof authApi.updateUser>[1]) {
    setError(null)
    try {
      await authApi.updateUser(user.id, fields)
      onChange()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function resetPassword() {
    const password = prompt(`New password for ${user.username} (at least 8 characters):`)
    if (password) await save({ password })
  }

  async function remove() {
    if (!confirm(`Delete the user “${user.username}”?`)) return
    try {
      await authApi.deleteUser(user.id)
      onChange()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const changed = permissions.slice().sort().join() !== user.permissions.slice().sort().join()
  return (
    <div className={`user-row ${user.disabled ? 'is-disabled' : ''}`}>
      <div className="user-row-head">
        <strong>{user.username}</strong>
        {self && <span className="pill">you</span>}
        {user.disabled && <span className="pill">disabled</span>}
        <select value={user.role} onChange={(e) => save({ role: e.target.value as Role })} aria-label="Role">
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
        <span className="spacer" />
        <button type="button" className="ghost" onClick={resetPassword}>Reset password</button>
        {!self && (
          <button type="button" className="ghost" onClick={() => save({ disabled: !user.disabled })}>
            {user.disabled ? 'Enable' : 'Disable'}
          </button>
        )}
        {!self && <button type="button" className="ghost danger" onClick={remove}>Delete</button>}
      </div>
      <PermissionPicker value={permissions} onChange={setPermissions} disabled={user.role === 'admin'} />
      {changed && (
        <div className="row">
          <button type="button" className="primary" onClick={() => save({ permissions })}>Save access</button>
          <button type="button" className="ghost" onClick={() => setPermissions(user.permissions)}>Cancel</button>
        </div>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  )
}

function Users() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => { authApi.users().then(setUsers, (e: Error) => setError(e.message)) }, [])
  useEffect(load, [load])

  return (
    <section className="panel settings-section">
      <div>
        <h2>Users</h2>
        <p className="muted small">Admins can use every tool and manage users. Other users only see the tools ticked
          for them.</p>
      </div>
      {error && <p className="error small">{error}</p>}
      <div className="user-list">
        {users?.map((u) => <UserRow key={u.id} user={u} self={u.id === me.id} onChange={load} />)}
      </div>
      <AddUser onAdded={load} />
    </section>
  )
}

const ITEM_LINK: Record<Permission, (id: string) => string> = {
  shorts: (id) => `/projects/${id}`,
  voice: () => '/voice-studio',
  video_maker: (id) => `/video-maker#video-${id}`,
  video_director: (id) => `/director?plan=${id}`,
  stickman: (id) => `/stickman?plan=${id}`,
}
const UNKNOWN = '(before logins)'

/** Admins: which user created which project, in which tool. */
function Activity() {
  const { labels } = useAuth()
  const [items, setItems] = useState<ActivityItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [who, setWho] = useState('')
  const [tool, setTool] = useState<Permission | ''>('')
  useEffect(() => { authApi.activity().then(setItems, (e: Error) => setError(e.message)) }, [])

  const nameOf = (i: ActivityItem) => i.created_by_name ?? UNKNOWN
  const people = useMemo(() => [...new Set((items ?? []).map(nameOf))].sort(), [items])
  const tools = Object.keys(labels) as Permission[]
  const counts = useMemo(() => {
    const c: Record<string, Partial<Record<Permission, number>>> = {}
    for (const i of items ?? []) (c[nameOf(i)] ??= {})[i.tool] = (c[nameOf(i)][i.tool] ?? 0) + 1
    return c
  }, [items])
  const shown = (items ?? []).filter((i) => (!who || nameOf(i) === who) && (!tool || i.tool === tool))

  return (
    <section className="panel settings-section">
      <div>
        <h2>Projects by user</h2>
        <p className="muted small">Who created what, and in which tool.</p>
      </div>
      {error && <p className="error small">{error}</p>}
      {items === null && !error && <p className="muted small">Loading…</p>}
      {items && people.length > 0 && (
        <div className="table-wrap">
          <table className="activity-table">
            <thead>
              <tr><th>User</th>{tools.map((t) => <th key={t}>{labels[t]}</th>)}<th>Total</th></tr>
            </thead>
            <tbody>
              {people.map((name) => (
                <tr key={name} className={who === name ? 'on' : ''} onClick={() => setWho(who === name ? '' : name)}>
                  <td><strong>{name}</strong></td>
                  {tools.map((t) => <td key={t}>{counts[name][t] ?? '–'}</td>)}
                  <td>{Object.values(counts[name]).reduce((a, b) => a + (b ?? 0), 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {items && items.length > 0 && (
        <>
          <div className="row">
            <label className="field">User
              <select value={who} onChange={(e) => setWho(e.target.value)}>
                <option value="">Everyone</option>
                {people.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label className="field">Tool
              <select value={tool} onChange={(e) => setTool(e.target.value as Permission | '')}>
                <option value="">All tools</option>
                {tools.map((t) => <option key={t} value={t}>{labels[t]}</option>)}
              </select>
            </label>
            <span className="muted small">{shown.length} of {items.length}</span>
          </div>
          <div className="table-wrap">
            <table className="activity-table">
              <thead>
                <tr><th>Project</th><th>Tool</th><th>Created by</th><th>Status</th><th>Created</th></tr>
              </thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={`${i.tool}:${i.id}`}>
                    <td className="activity-title"><Link to={ITEM_LINK[i.tool](i.id)} title={i.title}>{i.title}</Link></td>
                    <td>{labels[i.tool]}</td>
                    <td>{nameOf(i)}</td>
                    <td>{i.status}</td>
                    <td>{new Date(i.created_at * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {items?.length === 0 && <p className="muted small">Nothing has been created yet.</p>}
    </section>
  )
}

export default function Settings() {
  const { user } = useAuth()
  return (
    <div className="tool-page">
      <div className="tool-page-copy">
        <h1>Settings</h1>
        <p className="muted">Signed in as <strong>{user.username}</strong> ({user.role}).</p>
      </div>
      <ChangePassword />
      {user.role === 'admin' && <Users />}
      {user.role === 'admin' && <Activity />}
    </div>
  )
}
