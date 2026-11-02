import axios, { AxiosError } from 'axios'

/**
 * The shared HTTP client.
 *
 * `baseURL` defaults to a same-origin `/api` — nginx proxies it in production and the
 * Vite dev server proxies it in development — so the built bundle carries no API host
 * and the same image runs anywhere.
 */
const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'

const client = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
  // Load-bearing for the suite. Gecko Notes sets a session cookie on the parent
  // domain; without this, axios would not send it and every request would rely on the
  // bearer token alone.
  withCredentials: true,

  /**
   * Repeat the key for array values — `a=1&a=2`, not axios' default `a[]=1`. FastAPI
   * reads repeated keys and silently drops ones it does not know (GAM found out).
   */
  paramsSerializer: {
    serialize(params: Record<string, unknown>): string {
      const search = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        if (value === undefined || value === null) return
        if (Array.isArray(value)) {
          value.forEach((item) => {
            if (item !== undefined && item !== null) search.append(key, String(item))
          })
        } else {
          search.append(key, String(value))
        }
      })
      return search.toString()
    },
  },
})

export const AUTH_TOKEN_KEY = 'suite_auth_token'

/** localStorage can throw outright in private mode, so never let it break a request. */
export function readToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY)
  } catch {
    return null
  }
}

export function writeToken(token: string): void {
  try {
    localStorage.setItem(AUTH_TOKEN_KEY, token)
  } catch {
    /* the session cookie still carries us; a lost token is not fatal */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY)
  } catch {
    /* nothing to clear */
  }
}

client.interceptors.request.use((config) => {
  const token = readToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/**
 * What to do when the session turns out to be over.
 *
 * A callback rather than an import, because importing the auth store here would close a
 * cycle: the store imports `api/auth.ts`, which imports this module. `App.tsx` already
 * depends on both and sets it on mount.
 */
type UnauthorizedHandler = () => boolean

let onUnauthorized: UnauthorizedHandler | null = null
let handled = false

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler
  handled = false
}

/**
 * Codes that mean "this session is over".
 *
 * The handler returns whether it acted, and only then is this treated as spent. A 401
 * during `bootstrap()` for a visitor who was never signed in is the ordinary anonymous
 * case, not an expiry — burning the one shot on it would leave a real expiry later in
 * the session with nothing to handle it.
 */
const SESSION_ENDED = new Set(['unauthorized', 'session_not_accepted'])

client.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    // Fires once. Several requests in flight when a session expires each come back 401,
    // and `signOut` navigates away, so reacting to each would fight its own redirect.
    //
    // Deliberately not 403 `forbidden_origin`: that is the CSRF guard refusing a
    // cross-origin write, which says nothing about whether the session is still good.
    // Signing someone out over it would turn a rejected request into a lost session.
    if (!handled && onUnauthorized && axios.isAxiosError(error)) {
      const status = error.response?.status
      const code = apiErrorCode(error)
      if (status === 401 && (code === null || SESSION_ENDED.has(code))) {
        handled = onUnauthorized()
      }
    }
    return Promise.reject(error)
  }
)

/**
 * Pull a readable sentence out of a failure.
 *
 * The backend normalises every error to `{detail: {code, message}}`, but a request can
 * also fail before it reaches the backend — a dropped connection, a proxy error — and
 * "undefined" is not something to show a user.
 */
export function apiErrorMessage(
  error: unknown,
  fallback = 'Something went wrong'
): string {
  if (axios.isAxiosError(error)) {
    const detail = (error as AxiosError<{ detail?: { message?: string } }>).response?.data
      ?.detail
    if (detail?.message) return detail.message
    if (error.code === 'ERR_NETWORK') return 'Could not reach the server'
    if (error.message) return error.message
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/** The machine-readable code, for branching on a specific failure. */
export function apiErrorCode(error: unknown): string | null {
  if (axios.isAxiosError(error)) {
    const detail = (error as AxiosError<{ detail?: { code?: string } }>).response?.data
      ?.detail
    return detail?.code ?? null
  }
  return null
}

export default client
