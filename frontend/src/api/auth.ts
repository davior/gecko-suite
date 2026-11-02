import client from '@/api/client'
import { loadConfig } from '@/api/config'

/**
 * Identity comes from Gecko Notes, not from here.
 *
 * The suite has no login form, no registration and no password reset until M6. Notes signs the token;
 * this module verifies who that makes us, and sends the browser to Notes when nobody
 * is signed in.
 */

export interface User {
  id: string
  username: string
  /** From the suite's `ADMIN_USERS`. Admins see and edit the shared (instance) settings. */
  is_admin: boolean
}

interface DataResponse<T> {
  data: T
}

export const authApi = {
  /** Who the backend thinks we are. 401 means not signed in. */
  me(): Promise<User> {
    return client.get<DataResponse<User>>('/me').then((r) => r.data.data)
  },
}

/**
 * Where the suite's sign-in page lives, as the backend reports it.
 *
 * Asynchronous because the answer arrives over the wire. There is deliberately no
 * hardcoded production fallback any more: one used to sit here, and it meant a
 * misconfigured development instance silently sent people to the live Notes login
 * instead of failing in a way anyone would notice.
 */
export async function notesBaseUrl(): Promise<string> {
  const config = await loadConfig()
  return config.notes_base_url
}

/**
 * Hand the browser to Gecko Notes to sign in, asking to be sent back here.
 *
 * If the config never arrives the redirect does not happen, which is the right
 * outcome: the backend being unreachable is what `RequireAuth` already reports, and
 * bouncing to another domain would hide it.
 *
 * The return URL is passed as a parameter rather than remembered on Notes' side so
 * this works for any app in the suite without Notes knowing about them. Notes
 * validates it against a *.geckopico.com allowlist — see GN-2 — which is what keeps
 * this from being an open redirect.
 */
export async function redirectToLogin(): Promise<void> {
  const base = await notesBaseUrl()
  const target = encodeURIComponent(window.location.href)
  window.location.href = `${base}/login?redirect=${target}`
}
