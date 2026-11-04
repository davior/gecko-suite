import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  accountsApi,
  type Account,
  type TotpSetup,
  type TwoFactorStatus,
} from '@/api/accounts'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { Field, Notice } from '@/components/AuthCard'
import { ErrorNote, Loading, PageHeader } from '@/components/Bits'

/**
 * The signed-in person's own account: name and email, password, two-step verification.
 *
 * Before the cutover most people have no suite account (Notes signed them in and the
 * importer has not run), and `/v1/auth/me` says so with 404 `no_account`. That is shown
 * as where to manage the account instead, never as an error.
 */
export default function AccountView() {
  const [account, setAccount] = useState<Account | null>(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setAccount(await accountsApi.me())
    } catch (e) {
      if (apiErrorCode(e) === 'no_account') setMissing(true)
      else setError(apiErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (missing) {
    return (
      <PageHeader title="Account">
        Your account is still kept by Gecko Notes. Change your password and two-step
        verification there; they move here when the suite takes over signing in.
      </PageHeader>
    )
  }

  return (
    <>
      <PageHeader title="Account">
        One account for every Gecko app. Changes here apply to all of them.
      </PageHeader>
      <ErrorNote message={error} />
      {!account ? (
        !error && <Loading />
      ) : (
        <>
          <Profile account={account} onSaved={setAccount} />
          <Password />
          <TwoStep />
        </>
      )}
    </>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-3 p-4">
      <h3 className="font-medium text-gray-900 dark:text-gray-100">{title}</h3>
      {children}
    </section>
  )
}

function Profile({
  account,
  onSaved,
}: {
  account: Account
  onSaved: (a: Account) => void
}) {
  const [username, setUsername] = useState(account.username)
  const [email, setEmail] = useState(account.email ?? '')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const saved = await accountsApi.updateMe({
        username: username.trim(),
        email: email.trim() || undefined,
      })
      onSaved(saved)
      setNotice(
        saved.email_verified
          ? 'Saved.'
          : 'Saved. We have emailed a link to confirm the new address.'
      )
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  return (
    <Section title="Profile">
      <ErrorNote message={error} />
      {notice && <Notice>{notice}</Notice>}
      <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <Field
          label={account.email_verified ? 'Email' : 'Email (not confirmed yet)'}
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <div className="sm:col-span-2">
          <button type="submit" className="btn btn-primary">
            Save
          </button>
        </div>
      </form>
    </Section>
  )
}

function Password() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    try {
      await accountsApi.changePassword(current, next)
      setCurrent('')
      setNext('')
      setNotice('Password changed.')
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  return (
    <Section title="Password">
      <ErrorNote message={error} />
      {notice && <Notice>{notice}</Notice>}
      <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Current password"
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
          required
        />
        <Field
          label="New password (at least 8 characters)"
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password"
          minLength={8}
          required
        />
        <div className="sm:col-span-2">
          <button type="submit" className="btn btn-primary">
            Change password
          </button>
        </div>
      </form>
    </Section>
  )
}

/** What the two-step section is showing: its status, or one of the setup flows. */
type Step =
  | { kind: 'status' }
  | { kind: 'totp'; setup: TotpSetup }
  | { kind: 'email' }
  | { kind: 'disable' }

function TwoStep() {
  const [status, setStatus] = useState<TwoFactorStatus | null>(null)
  const [step, setStep] = useState<Step>({ kind: 'status' })
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    accountsApi
      .twoFactorStatus()
      .then(setStatus)
      .catch((e) => setError(apiErrorMessage(e)))
  }, [])

  function done(next: TwoFactorStatus, text: string) {
    setStatus(next)
    setStep({ kind: 'status' })
    setCode('')
    setPassword('')
    setNotice(text)
  }

  async function act(fn: () => Promise<void>) {
    setError(null)
    setNotice(null)
    try {
      await fn()
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  const clean = () => code.replace(/\s+/g, '')

  return (
    <Section title="Two-step verification">
      <ErrorNote message={error} />
      {notice && <Notice>{notice}</Notice>}
      {!status ? (
        !error && <Loading />
      ) : step.kind === 'totp' ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () =>
              done(
                await accountsApi.enableTotp(step.setup.secret, clean()),
                'Authenticator app turned on.'
              )
            )
          }}
          className="space-y-3"
        >
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Scan this with your authenticator app, then enter the code it shows.
          </p>
          <img
            src={step.setup.qr_data_uri}
            alt="QR code for your authenticator app"
            className="h-48 w-48 rounded bg-white p-2"
          />
          <p className="text-xs text-gray-500">
            Or enter this key by hand:{' '}
            <code className="break-all">{step.setup.secret}</code>
          </p>
          <Field
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            required
          />
          <div className="flex gap-2">
            <button type="submit" className="btn btn-primary">
              Turn on
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setStep({ kind: 'status' })}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : step.kind === 'email' ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () =>
              done(await accountsApi.verifyEmailCode(clean()), 'Email codes turned on.')
            )
          }}
          className="space-y-3"
        >
          <p className="text-sm text-gray-600 dark:text-gray-400">
            We have emailed you a code. Enter it to turn email codes on.
          </p>
          <Field
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            required
          />
          <div className="flex gap-2">
            <button type="submit" className="btn btn-primary">
              Turn on
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setStep({ kind: 'status' })}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : step.kind === 'disable' ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () =>
              done(
                await accountsApi.disableTwoFactor(password),
                'Two-step verification turned off.'
              )
            )
          }}
          className="space-y-3"
        >
          <Field
            label="Your password, to confirm"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          <div className="flex gap-2">
            <button type="submit" className="btn btn-danger">
              Turn off
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setStep({ kind: 'status' })}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : status.enabled ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-gray-700 dark:text-gray-300">
            On, with {status.method === 'totp' ? 'an authenticator app' : 'email codes'}.
          </span>
          <button
            type="button"
            className="btn btn-secondary ml-auto"
            onClick={() => setStep({ kind: 'disable' })}
          >
            Turn off
          </button>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-gray-600 dark:text-gray-400">
            Off. Signing in asks only for your password.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() =>
                void act(async () =>
                  setStep({ kind: 'totp', setup: await accountsApi.setupTotp() })
                )
              }
            >
              Use an authenticator app
            </button>
            {status.email_available && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() =>
                  void act(async () => {
                    await accountsApi.setupEmailCodes()
                    setStep({ kind: 'email' })
                  })
                }
              >
                Use email codes
              </button>
            )}
          </div>
        </div>
      )}
    </Section>
  )
}
