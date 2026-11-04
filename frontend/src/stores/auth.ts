import { create } from 'zustand'
import { accountsApi } from '@/api/accounts'
import { authApi, redirectToLogin, type User } from '@/api/auth'
import { apiErrorCode, apiErrorMessage, clearToken, writeToken } from '@/api/client'
import { useAppThemeStore } from '@/stores/appTheme'
import { useRegistryStore } from '@/stores/registry'

/**
 * Who is signed in. Copied from GVC.
 *
 * `bootstrap()` ends in "the session we were given is valid" or "send them to sign in",
 * wherever that is (see `redirectToLogin`). `signedIn()` is the suite's own sign-in page
 * handing over a token it just got (M6); the page itself does the asking, since its
 * steps (a second factor, a redirect elsewhere) are not state anyone else needs.
 *
 * `rejected` is not a flavour of `anonymous`: Notes says you are signed in and this app
 * could not verify it, the one state where a sign-in button loops forever.
 */
type Status = 'idle' | 'loading' | 'authenticated' | 'anonymous' | 'rejected'

interface AuthState {
  user: User | null
  status: Status
  error: string | null
  bootstrap: () => Promise<void>
  signedIn: (token: string) => Promise<void>
  signOut: () => Promise<void>
  reset: () => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  status: 'idle',
  error: null,

  async bootstrap() {
    // React 18 StrictMode mounts effects twice; a second caller waits for the first.
    if (get().status === 'loading') return
    set({ status: 'loading', error: null })
    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated', error: null })
    } catch (error) {
      const code = apiErrorCode(error)
      if (code === 'session_not_accepted') {
        set({ user: null, status: 'rejected', error: apiErrorMessage(error, '') || null })
        return
      }
      if (code === 'unauthorized') {
        set({ user: null, status: 'anonymous', error: null })
        return
      }
      // A backend that is down is not a signed-out user; a login page would hide it.
      set({
        user: null,
        status: 'anonymous',
        error: 'Could not reach Gecko Suite. Is the API running?',
      })
    }
  },

  async signedIn(token: string) {
    writeToken(token)
    // Not `reset()`, which would flash the signed-out screen between the two states.
    set({ status: 'idle' })
    await get().bootstrap()
  },

  async signOut() {
    clearToken()
    get().reset()
    // Every store holding user data, so one person's keys and spend cannot stay on
    // screen for the next.
    useRegistryStore.getState().reset()
    useAppThemeStore.getState().reset()
    // The cookie is httpOnly, so only the server can clear it, and it is shared by every
    // app on the domain: without this, the sign-in page would find the old session
    // still there and sign the same person straight back in. Best effort, because an
    // unreachable backend must not keep anyone from leaving.
    await accountsApi.logout().catch(() => undefined)
    await redirectToLogin()
  },

  reset() {
    set({ user: null, status: 'idle', error: null })
  },
}))
