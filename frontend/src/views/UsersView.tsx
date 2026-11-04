import { useCallback, useEffect, useState } from 'react'
import { accountAdminApi, type SignUpSettings } from '@/api/accountAdmin'
import type { Account } from '@/api/accounts'
import { apiErrorMessage } from '@/api/client'
import { ErrorNote, Loading, PageHeader } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

/**
 * Accounts and sign-up, for administrators (M6). Account actions only: what each person
 * keeps in Notes, GAM or GVC (notes, storage, renders) is managed in that app, and
 * deleting an account here does not reach into them.
 *
 * The server refuses to demote, disable or delete the last administrator, and to delete
 * your own account; those refusals arrive as sentences and are shown as they come.
 */
export default function UsersView() {
  const me = useAuthStore((s) => s.user)
  const isAdmin = me?.is_admin ?? false
  const [rows, setRows] = useState<Account[] | null>(null)
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [settings, setSettings] = useState<SignUpSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async (q: string) => {
    try {
      const page = await accountAdminApi.list({ q: q || undefined, limit: 200 })
      setRows(page.data)
      setTotal(page.total)
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    if (!isAdmin) return
    void load('')
    accountAdminApi
      .settings()
      .then(setSettings)
      .catch((e) => setError(apiErrorMessage(e)))
  }, [isAdmin, load])

  if (!isAdmin) {
    return (
      <PageHeader title="Users">Only an administrator can manage accounts.</PageHeader>
    )
  }

  async function act(fn: () => Promise<void>, text?: string) {
    setError(null)
    setNotice(null)
    try {
      await fn()
      if (text) setNotice(text)
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  const replace = (saved: Account) =>
    setRows((current) => current?.map((r) => (r.id === saved.id ? saved : r)) ?? null)

  function setPassword(row: Account) {
    const next = window.prompt(
      `A new password for ${row.username} (at least 8 characters). Tell them it, and ask them to change it.`
    )
    if (!next) return
    void act(
      () => accountAdminApi.setPassword(row.id, next),
      `Password set for ${row.username}.`
    )
  }

  function remove(row: Account) {
    if (
      !window.confirm(
        `Delete ${row.username}'s account? They can no longer sign in to any Gecko app. What they keep in each app is not deleted.`
      )
    )
      return
    void act(async () => {
      await accountAdminApi.remove(row.id)
      setRows((current) => current?.filter((r) => r.id !== row.id) ?? null)
      setTotal((t) => t - 1)
    }, `${row.username}'s account was deleted.`)
  }

  function toggle(key: keyof SignUpSettings) {
    if (!settings) return
    void act(async () => {
      setSettings(await accountAdminApi.updateSettings({ [key]: !settings[key] }))
    })
  }

  return (
    <>
      <PageHeader title="Users">
        Accounts for every Gecko app. Each app&apos;s own content is managed in that app.
      </PageHeader>
      <ErrorNote message={error} />
      {notice && (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          {notice}
        </p>
      )}

      <section className="card space-y-2 p-4 text-sm">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">Sign-up</h3>
        {!settings ? (
          <Loading />
        ) : (
          <>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.registration_enabled}
                onChange={() => toggle('registration_enabled')}
              />
              Anyone may create an account
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.email_verification_required}
                onChange={() => toggle('email_verification_required')}
              />
              New accounts must confirm their email before signing in
            </label>
          </>
        )}
      </section>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void load(query.trim())
        }}
        className="flex gap-2"
      >
        <input
          className="input flex-1"
          placeholder="Search by username or email"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search accounts"
        />
        <button type="submit" className="btn btn-secondary">
          Search
        </button>
      </form>

      {!rows ? (
        <Loading />
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            {total} account{total === 1 ? '' : 's'}
          </p>
          {rows.map((row) => (
            <div
              key={row.id}
              className="card flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm"
            >
              <div className="min-w-0">
                <div className="font-medium text-gray-900 dark:text-gray-100">
                  {row.username}
                  {row.id === me?.id && ' (you)'}
                </div>
                <div className="truncate text-xs text-gray-500">
                  {row.email ?? 'no email'}
                  {row.email && !row.email_verified && ' · not confirmed'}
                  {row.two_factor_method && ' · two-step on'}
                  {row.last_login
                    ? ` · last signed in ${row.last_login.slice(0, 10)}`
                    : ' · never signed in here'}
                </div>
              </div>
              {row.is_admin && (
                <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs font-medium text-purple-700 dark:bg-purple-900/40 dark:text-purple-300">
                  Admin
                </span>
              )}
              {!row.is_active && (
                <span className="rounded bg-gray-200 px-1.5 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300">
                  Disabled
                </span>
              )}
              <div className="ml-auto flex flex-wrap gap-1">
                <button
                  type="button"
                  className="btn btn-ghost px-2 py-1"
                  onClick={() =>
                    void act(async () =>
                      replace(
                        await accountAdminApi.update(row.id, { is_admin: !row.is_admin })
                      )
                    )
                  }
                >
                  {row.is_admin ? 'Remove admin' : 'Make admin'}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost px-2 py-1"
                  onClick={() =>
                    void act(async () =>
                      replace(
                        await accountAdminApi.update(row.id, {
                          is_active: !row.is_active,
                        })
                      )
                    )
                  }
                >
                  {row.is_active ? 'Disable' : 'Enable'}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost px-2 py-1"
                  onClick={() => setPassword(row)}
                >
                  Set password
                </button>
                {row.id !== me?.id && (
                  <button
                    type="button"
                    className="btn btn-ghost px-2 py-1 text-red-600"
                    onClick={() => remove(row)}
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
