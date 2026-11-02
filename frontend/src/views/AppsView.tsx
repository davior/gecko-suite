import { useCallback, useEffect, useState } from 'react'
import { apiErrorMessage } from '@/api/client'
import { appsApi, type AppCredential } from '@/api/apps'
import { APP_LABELS, type AppName } from '@/api/envelope'
import { ErrorNote, PageHeader } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

/**
 * The credentials Notes, GAM and GVC use to call the suite for work that outlives a
 * request, such as a background job with no user token. Admins only.
 *
 * A secret is shown once, when issued, and cannot be read back: copy it into that app's
 * .env as SUITE_APP_SECRET. Issuing again replaces it at once, which is also how a
 * leaked one is revoked.
 */
export default function AppsView() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const [rows, setRows] = useState<AppCredential[]>([])
  const [issued, setIssued] = useState<{ app: AppName; secret: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await appsApi.list())
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    if (isAdmin) void load()
  }, [isAdmin, load])

  if (!isAdmin) {
    return (
      <PageHeader title="Apps">
        Only an administrator can manage app credentials.
      </PageHeader>
    )
  }

  return (
    <>
      <PageHeader title="Apps">
        Each app gets one credential for calling the suite on someone&apos;s behalf
        without their session.
      </PageHeader>
      <ErrorNote message={error} />
      {issued && (
        <div className="card space-y-2 border-amber-300 p-4">
          <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
            New secret for {APP_LABELS[issued.app]}. Copy it now; it will not be shown
            again.
          </p>
          <code className="block break-all rounded bg-gray-100 p-2 text-xs dark:bg-gray-900">
            {issued.secret}
          </code>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setIssued(null)}
          >
            I have copied it
          </button>
        </div>
      )}
      {rows.map((row) => (
        <div
          key={row.app}
          className="card flex flex-wrap items-center gap-3 px-4 py-3 text-sm"
        >
          <span className="font-medium text-gray-800 dark:text-gray-200">
            {APP_LABELS[row.app]}
          </span>
          <span className="text-gray-500">
            {row.issued
              ? `Issued ${row.created_at?.slice(0, 10)}${row.last_used_at ? `, last used ${row.last_used_at.slice(0, 16).replace('T', ' ')}` : ', not used yet'}`
              : 'No credential'}
          </span>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              className="btn btn-secondary px-3 py-1"
              onClick={async () => {
                if (
                  row.issued &&
                  !window.confirm(
                    `Replace ${APP_LABELS[row.app]}'s secret? The old one stops working at once.`
                  )
                )
                  return
                try {
                  setIssued(await appsApi.issue(row.app))
                  await load()
                } catch (e) {
                  setError(apiErrorMessage(e))
                }
              }}
            >
              {row.issued ? 'Issue a new secret' : 'Issue a secret'}
            </button>
            {row.issued && (
              <button
                type="button"
                className="btn btn-ghost px-3 py-1 text-red-600"
                onClick={async () => {
                  await appsApi.revoke(row.app)
                  await load()
                }}
              >
                Revoke
              </button>
            )}
          </div>
        </div>
      ))}
    </>
  )
}
