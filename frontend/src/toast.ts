export type ToastKind = 'success' | 'error' | 'info'

export interface Toast {
  id: number
  message: string
  kind: ToastKind
  /** How long it stays, in milliseconds. */
  duration: number
}

const TOAST_EVENT = 'studio:toast'
let nextId = 1

/** Show a toast that closes itself after `duration` ms. */
export function toast(message: string, kind: ToastKind = 'success', duration = 4000) {
  window.dispatchEvent(new CustomEvent<Toast>(TOAST_EVENT, { detail: { id: nextId++, message, kind, duration } }))
}

export function onToast(fn: (t: Toast) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<Toast>).detail)
  window.addEventListener(TOAST_EVENT, handler)
  return () => window.removeEventListener(TOAST_EVENT, handler)
}

// Message for a successful API call that changed something. GET requests and background lookups show none.
const MESSAGES: [string, RegExp, string | null][] = [
  ['POST', /^\/api\/source-info$/, null],
  ['POST', /^\/api\/auth\/(status|logout)$/, null],
  ['POST', /^\/api\/auth\/(login|setup)$/, null], // the app greets the user by name instead
  ['POST', /^\/api\/auth\/password$/, 'Password changed. Other devices were logged out.'],
  ['POST', /^\/api\/users$/, 'User added'],
  ['PATCH', /^\/api\/users\/[^/]+$/, 'User updated'],
  ['DELETE', /^\/api\/users\/[^/]+$/, 'User deleted'],
  ['POST', /^\/api\/projects$/, 'Project created. Finding the best moments…'],
  ['PATCH', /^\/api\/projects\/[^/]+$/, 'Project renamed'],
  ['DELETE', /^\/api\/projects\/[^/]+$/, 'Project deleted'],
  ['POST', /\/retry$/, 'Started again'],
  ['POST', /\/cancel$/, 'Cancelled'],
  ['POST', /\/limit-choice$/, 'Choice saved'],
  ['POST', /^\/api\/templates$/, 'Template saved'],
  ['DELETE', /^\/api\/templates\//, 'Template deleted'],
  ['POST', /^\/api\/clips\/[^/]+\/render$/, 'Rendering the clip…'],
  ['PATCH', /^\/api\/clips\//, 'Clip saved'],
  ['POST', /^\/api\/tts\/generate$/, 'Speech generated'],
  ['POST', /^\/api\/tts\/voices\/library$/, 'Voice added to your library'],
  ['POST', /^\/api\/tts\/local\/download$/, 'Downloading the local voice model…'],
  ['DELETE', /^\/api\/tts\/history\//, 'Generation deleted'],
  ['POST', /^\/api\/maker\/draft$/, 'Script ready'],
  ['POST', /^\/api\/maker\/footage$/, 'Footage found'],
  ['POST', /^\/api\/maker\/footage\/uploads$/, 'Uploaded'],
  ['DELETE', /^\/api\/maker\/footage\/uploads\//, 'Upload deleted'],
  ['POST', /^\/api\/maker\/videos$/, 'Video started'],
  ['DELETE', /^\/api\/maker\/videos\//, 'Video deleted'],
  ['POST', /^\/api\/maker\/music(\/add)?$/, 'Music added'],
  ['DELETE', /^\/api\/maker\/music\//, 'Track deleted'],
  ['POST', /^\/api\/(video-)?director\/plans$/, 'Plan started'],
  ['POST', /\/plans\/[^/]+\/revise$/, 'Revising the plan…'],
  ['POST', /\/plans\/[^/]+\/approve$/, 'Approved. Writing the prompts…'],
  ['POST', /\/plans\/[^/]+\/clips\/[^/]+\/rewrite$/, 'Rewriting the clip…'],
  ['DELETE', /\/plans\/[^/]+$/, 'Plan deleted'],
]

/** Toast for a successful request, if it was one the user made to change something. */
export function toastSuccess(method: string | undefined, url: string) {
  const m = (method ?? 'GET').toUpperCase()
  if (m === 'GET' || m === 'HEAD') return
  const path = url.split('?')[0]
  const match = MESSAGES.find(([verb, re]) => verb === m && re.test(path))
  const message = match ? match[2] : m === 'DELETE' ? 'Deleted' : 'Saved'
  if (message) toast(message)
}
