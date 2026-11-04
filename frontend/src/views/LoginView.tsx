import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  accountsApi,
  needsSecondStep,
  safeRedirect,
  type SecondStepNeeded,
} from '@/api/accounts'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { loadConfig, type ClientConfig } from '@/api/config'
import AuthCard, { Field, Notice } from '@/components/AuthCard'
import { ErrorNote } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

/**
 * Sign in, in one or two steps, then go where `?redirect=` asked if it is allowed.
 *
 * The second step holds the challenge token in component state and nowhere else: it is
 * not a session (every app refuses it as one), and a reload should start over rather than
 * resume a half-finished sign-in.
 */
export default function LoginView() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const status = useAuthStore((s) => s.status)
  const signedIn = useAuthStore((s) => s.signedIn)

  const [config, setConfig] = useState<ClientConfig | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [challenge, setChallenge] = useState<SecondStepNeeded | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [unverified, setUnverified] = useState(false)
  const [resendTo, setResendTo] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const redirect = params.get('redirect')
  const keep = redirect ? `?redirect=${encodeURIComponent(redirect)}` : ''

  useEffect(() => {
    loadConfig()
      .then(setConfig)
      .catch(() => setError('Could not reach Gecko Suite. Is the API running?'))
  }, [])

  // Already signed in (the shared cookie, or a token from an earlier visit): no form,
  // straight on to wherever this visit was meant to end.
  useEffect(() => {
    if (status === 'authenticated' && config) leave(config)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, config])

  function leave(cfg: ClientConfig) {
    const target = safeRedirect(redirect, cfg.login_redirect_origins)
    if (!target) {
      navigate('/', { replace: true })
      return
    }
    const url = new URL(target)
    if (url.origin === window.location.origin) {
      navigate(`${url.pathname}${url.search}${url.hash}`, { replace: true })
    } else {
      window.location.assign(target)
    }
  }

  async function finish(token: string) {
    await signedIn(token)
    // The effect above moves on once the store says authenticated.
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setUnverified(false)
    setNotice(null)
    try {
      const result = await accountsApi.login({ username: username.trim(), password })
      if (needsSecondStep(result)) {
        setChallenge(result)
        setCode('')
      } else {
        await finish(result.access_token)
      }
    } catch (e) {
      setUnverified(apiErrorCode(e) === 'email_not_verified')
      setError(apiErrorMessage(e, 'Could not sign in'))
    } finally {
      setBusy(false)
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault()
    if (!challenge) return
    setBusy(true)
    setError(null)
    try {
      const result = await accountsApi.secondStep({
        challenge_token: challenge.challenge_token,
        code: code.replace(/\s+/g, ''),
      })
      await finish(result.access_token)
    } catch (e) {
      if (apiErrorCode(e) === 'challenge_invalid') {
        // Expired or spent: only a fresh first step can help.
        setChallenge(null)
        setPassword('')
      }
      setError(apiErrorMessage(e, 'Could not verify the code'))
    } finally {
      setBusy(false)
    }
  }

  async function resend(event: FormEvent) {
    event.preventDefault()
    try {
      setNotice(await accountsApi.resendVerification(resendTo.trim()))
      setError(null)
      setUnverified(false)
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  // While a session is being checked, or followed somewhere, a form would only invite
  // someone to type a password the page is about to make unnecessary.
  if (status === 'idle' || status === 'loading' || status === 'authenticated') {
    return (
      <AuthCard title="Sign in">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {status === 'authenticated'
            ? 'Signed in. Taking you on…'
            : 'Checking your session…'}
        </p>
        <ErrorNote message={error} />
      </AuthCard>
    )
  }

  if (challenge) {
    return (
      <AuthCard title="Two-step verification">
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {challenge.method === 'totp'
            ? 'Enter the six-digit code from your authenticator app.'
            : 'We have emailed you a six-digit code. Enter it here.'}
        </p>
        <ErrorNote message={error} />
        <form onSubmit={submitCode} className="space-y-3">
          <Field
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            required
          />
          <button type="submit" className="btn btn-primary w-full" disabled={busy}>
            {busy ? 'Checking…' : 'Verify'}
          </button>
        </form>
        <button
          type="button"
          className="btn btn-ghost w-full"
          onClick={() => {
            setChallenge(null)
            setError(null)
          }}
        >
          Back
        </button>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Sign in"
      footer={
        <>
          <p>
            <Link to={`/forgot-password${keep}`} className="underline">
              Forgot your password?
            </Link>
          </p>
          <p>
            No account?{' '}
            <Link to={`/register${keep}`} className="underline">
              Create one
            </Link>
          </p>
        </>
      }
    >
      <p className="text-sm text-gray-600 dark:text-gray-400">
        One account for Gecko Notes, Assets, Video Creator and the Suite.
      </p>
      <ErrorNote message={error} />
      {unverified && (
        <form onSubmit={resend} className="space-y-2">
          <Field
            label="Your account's email address"
            type="email"
            value={resendTo}
            onChange={(e) => setResendTo(e.target.value)}
            required
          />
          <button type="submit" className="btn btn-secondary w-full">
            Send the verification email again
          </button>
        </form>
      )}
      {notice && <Notice>{notice}</Notice>}
      <form onSubmit={submit} className="space-y-3">
        <Field
          label="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
        <Field
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        <button type="submit" className="btn btn-primary w-full" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </AuthCard>
  )
}
