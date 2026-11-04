import client from '@/api/client'
import { loadConfig } from '@/api/config'

/**
 * Who the session says we are, and where to send someone who has none.
 *
 * Sign-in lives wherever `NOTES_BASE_URL` points. Before the M6 cutover that is Gecko
 * Notes, and the suite's own sign-in pages exist but nothing sends anyone to them; the
 * cutover points it at the suite itself, and the same code then stays on this origin.
 * One setting, so switching back is the same edit undone.
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

/** Whether people sign in on this suite's own pages rather than at Notes. */
export function signsInHere(
  base: string,
  here: string = window.location.origin
): boolean {
  try {
    return new URL(base, here).origin === here
  } catch {
    return false
  }
}

/**
 * Send the browser to sign in, asking to be sent back here.
 *
 * If the config never arrives the redirect does not happen, which is the right
 * outcome: the backend being unreachable is what `RequireAuth` already reports, and
 * bouncing to another domain would hide it.
 *
 * The return URL is passed as a parameter rather than remembered by the sign-in side,
 * so this works for any app without the sign-in side knowing about them. Notes checks
 * it against a *.geckopico.com allowlist (GN-2), and the suite's own page against
 * `LOGIN_REDIRECT_ORIGINS` (`safeRedirect`), which is what keeps either from being an
 * open redirect.
 */
export async function redirectToLogin(): Promise<void> {
  const base = await notesBaseUrl()
  const target = encodeURIComponent(window.location.href)
  // A path, not `${base}/login`, when the pages are ours: the same place either way, but
  // it cannot be sent off-site by a base URL written with a different scheme or port.
  window.location.href = signsInHere(base)
    ? `/login?redirect=${target}`
    : `${base}/login?redirect=${target}`
}
