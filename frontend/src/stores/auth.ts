import { create } from 'zustand'
import { authApi, redirectToLogin, type User } from '@/api/auth'
import { apiErrorCode, apiErrorMessage, clearToken } from '@/api/client'
import { useRegistryStore } from '@/stores/registry'

/**
 * Who is signed in. Copied from GVC.
 *
 * There is no `login()` here on purpose: until identity moves here (M6) the suite cannot
 * sign anybody in. `bootstrap()` ends in "the session Notes gave us is valid" or "send
 * them to Notes".
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
  signOut: () => void
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

  signOut() {
    clearToken()
    get().reset()
    // Every store holding user data, so one person's keys and spend cannot stay on
    // screen for the next.
    useRegistryStore.getState().reset()
    void redirectToLogin()
  },

  reset() {
    set({ user: null, status: 'idle', error: null })
  },
}))
