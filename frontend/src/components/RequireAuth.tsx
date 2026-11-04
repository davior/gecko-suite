import type { ReactNode } from 'react'
import { AlertTriangle, LogIn } from 'lucide-react'
import { redirectToLogin, signsInHere } from '@/api/auth'
import { currentConfig } from '@/api/config'
import { useAuthStore } from '@/stores/auth'

interface Props {
  children: ReactNode
}

/**
 * Gate for everything that needs a signed-in user.
 *
 * Deliberately does *not* redirect automatically on `anonymous`. An automatic bounce
 * to another domain turns a backend outage into an unexplained round trip, and makes
 * local development painful when the API is simply not running yet. The user is told
 * what happened and clicks through.
 */
export default function RequireAuth({ children }: Props) {
  const status = useAuthStore((s) => s.status)
  const error = useAuthStore((s) => s.error)

  if (status === 'idle' || status === 'loading') {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
        <span className="text-sm text-gray-500 dark:text-gray-400">
          Checking your session…
        </span>
      </div>
    )
  }

  // Signed in to Notes, rejected here. Deliberately offers no sign-in button: in this
  // state the user has already proved they can sign in, and doing it again lands them
  // right back on this screen. The only thing that helps is an operator reading the
  // message — so the message is the whole panel.
  if (status === 'rejected') {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 px-4 dark:bg-gray-900">
        <div className="card max-w-md space-y-4 p-6 text-center">
          <div className="flex justify-center">
            <AlertTriangle className="h-8 w-8 text-amber-500" />
          </div>
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Signed in, but not accepted
          </h1>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {error ??
              'You are signed in to Gecko Notes, but this app could not verify that session.'}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-500">
            This is a configuration problem, not something you did. Signing in again will
            not change it — the two apps need the same JWT_SECRET_KEY.
          </p>
        </div>
      </div>
    )
  }

  if (status === 'anonymous') {
    // App.tsx fetches the config at boot, so by the time a check has failed it is
    // normally here; without it, Notes is the safer thing to name (it is the default).
    const config = currentConfig()
    const here = config ? signsInHere(config.notes_base_url) : false
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 px-4 dark:bg-gray-900">
        <div className="card max-w-md space-y-4 p-6 text-center">
          <div className="flex justify-center">
            {error ? (
              <AlertTriangle className="h-8 w-8 text-amber-500" />
            ) : (
              <LogIn className="h-8 w-8 text-blue-500" />
            )}
          </div>
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {error ? 'Gecko Suite unavailable' : 'Sign in to continue'}
          </h1>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {error ??
              (here
                ? 'One account for every Gecko app.'
                : 'Gecko Suite uses your Gecko Notes account.')}
          </p>
          {!error && (
            <button
              type="button"
              className="btn btn-primary w-full"
              onClick={() => void redirectToLogin()}
            >
              {here ? 'Sign in' : 'Sign in with Gecko Notes'}
            </button>
          )}
        </div>
      </div>
    )
  }

  return <>{children}</>
}
