import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { accountsApi } from '@/api/accounts'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { loadConfig } from '@/api/config'
import AuthCard, { Field, Notice } from '@/components/AuthCard'
import { ErrorNote } from '@/components/Bits'

/**
 * The signed-out pages besides sign-in: registration, the forgotten password, and the
 * two links the suite emails (`/verify-email?token=`, `/reset-password?token=`, built from
 * `APP_BASE_URL`). One module because each is a form and a sentence, and they share the
 * `?redirect=` they pass back to sign-in so a visitor from Notes still ends up there.
 */

function useKeep(): string {
  const [params] = useSearchParams()
  const redirect = params.get('redirect')
  return redirect ? `?redirect=${encodeURIComponent(redirect)}` : ''
}

function BackToSignIn() {
  const keep = useKeep()
  return (
    <p>
      <Link to={`/login${keep}`} className="underline">
        Back to sign in
      </Link>
    </p>
  )
}

export function RegisterView() {
  const keep = useKeep()
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<'verify' | 'ready' | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const account = await accountsApi.register({
        username: username.trim(),
        email: email.trim(),
        password,
      })
      setDone(account.email_verified ? 'ready' : 'verify')
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create the account'))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthCard title="Account created" footer={<BackToSignIn />}>
        <Notice>
          {done === 'verify'
            ? 'We have emailed you a link to confirm your address. Follow it, then sign in.'
            : 'You can sign in now.'}
        </Notice>
        {done === 'ready' && (
          <Link to={`/login${keep}`} className="btn btn-primary w-full">
            Sign in
          </Link>
        )}
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Create an account" footer={<BackToSignIn />}>
      <ErrorNote message={error} />
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
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
        <Field
          label="Password (at least 8 characters)"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          minLength={8}
          required
        />
        <button type="submit" className="btn btn-primary w-full" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </AuthCard>
  )
}

export function ForgotPasswordView() {
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [canMail, setCanMail] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    loadConfig()
      .then((c) => setCanMail(c.email_enabled))
      .catch(() => undefined)
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setNotice(await accountsApi.forgotPassword(email.trim()))
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Reset your password" footer={<BackToSignIn />}>
      {!canMail ? (
        // Said up front: the server answers "a link has been sent" either way (it never
        // tells a stranger whether an address has an account), so without this the
        // visitor would wait for an email that cannot come.
        <p className="text-sm text-gray-600 dark:text-gray-400">
          This server cannot send email, so a reset link cannot reach you. Ask an
          administrator to set a new password for you.
        </p>
      ) : notice ? (
        <Notice>{notice}</Notice>
      ) : (
        <>
          <ErrorNote message={error} />
          <form onSubmit={submit} className="space-y-3">
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              autoFocus
              required
            />
            <button type="submit" className="btn btn-primary w-full" disabled={busy}>
              {busy ? 'Sending…' : 'Send a reset link'}
            </button>
          </form>
        </>
      )}
    </AuthCard>
  )
}

export function ResetPasswordView() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirm) {
      setError('The two passwords do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      setNotice(await accountsApi.resetPassword(token, password))
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Choose a new password" footer={<BackToSignIn />}>
      {!token ? (
        <ErrorNote message="This link is incomplete. Open it again from the email, or ask for a new one." />
      ) : notice ? (
        <Notice>{notice}</Notice>
      ) : (
        <>
          <ErrorNote message={error} />
          <form onSubmit={submit} className="space-y-3">
            <Field
              label="New password (at least 8 characters)"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              autoFocus
              required
            />
            <Field
              label="Again"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
            />
            <button type="submit" className="btn btn-primary w-full" disabled={busy}>
              {busy ? 'Saving…' : 'Set password'}
            </button>
          </form>
        </>
      )}
    </AuthCard>
  )
}

export function VerifyEmailView() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null)
  // A link is single-use, and StrictMode runs effects twice in development: the second
  // call would spend nothing and report the link as invalid over the first's success.
  const sent = useRef(false)

  useEffect(() => {
    if (!token || sent.current) return
    sent.current = true
    accountsApi
      .verifyEmail(token)
      .then((text) => setState({ ok: true, text }))
      .catch((e) =>
        setState({
          ok: false,
          text:
            apiErrorCode(e) === 'link_invalid'
              ? 'This link has expired or was already used. Sign in to have a new one sent.'
              : apiErrorMessage(e),
        })
      )
  }, [token])

  return (
    <AuthCard title="Confirm your email" footer={<BackToSignIn />}>
      {!token ? (
        <ErrorNote message="This link is incomplete. Open it again from the email." />
      ) : !state ? (
        <p className="text-sm text-gray-500">Checking the link…</p>
      ) : state.ok ? (
        <Notice>{state.text}</Notice>
      ) : (
        <ErrorNote message={state.text} />
      )}
    </AuthCard>
  )
}
