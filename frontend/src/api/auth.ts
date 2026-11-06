import client from '@/api/client'

/**
 * Who the session says we are, and where to send someone who has none: this suite's own
 * sign-in page. The suite signs people in for every app (Notes with
 * IDENTITY_PROVIDER=suite, GAM and GVC), so there is no other place to send them.
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
 * Send the browser to this suite's sign-in page, asking to be sent back here.
 *
 * A path, never an address: it cannot be sent off-site. The return URL is checked by the
 * sign-in page against `LOGIN_REDIRECT_ORIGINS` (`safeRedirect`), which is what keeps it
 * from being an open redirect.
 */
export function redirectToLogin(): void {
  const target = encodeURIComponent(window.location.href)
  window.location.href = `/login?redirect=${target}`
}
