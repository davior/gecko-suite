import { useEffect, useState } from 'react'
import { apiErrorMessage } from '@/api/client'
import { APP_LABELS, type AppName } from '@/api/envelope'
import { TASKS } from '@/api/assignments'
import { describeCost, usageApi, type UsageGroup, type UsageReport } from '@/api/usage'
import { ErrorNote, Loading, PageHeader } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

const PERIODS = [7, 30, 90, 365]

/**
 * What the AI has cost, across every app. Every figure is an estimate from list prices
 * unless a vendor billed it exactly, and a total with unpriced calls in it is a floor
 * ("at least"), never presented as a sum.
 */
export default function UsageView() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const [days, setDays] = useState(30)
  const [scope, setScope] = useState<'me' | 'all'>('me')
  const [report, setReport] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    setReport(null)
    usageApi
      .report(days, scope)
      .then((r) => !stale && setReport(r))
      .catch((e) => !stale && setError(apiErrorMessage(e)))
    return () => {
      stale = true
    }
  }, [days, scope])

  return (
    <>
      <PageHeader title="Usage">
        Estimated from list prices. Providers discount and change prices; your bills are
        the record.
      </PageHeader>
      <div className="flex flex-wrap items-center gap-2">
        {PERIODS.map((d) => (
          <button
            key={d}
            type="button"
            className={
              days === d ? 'btn btn-primary px-3 py-1' : 'btn btn-secondary px-3 py-1'
            }
            onClick={() => setDays(d)}
          >
            {d} days
          </button>
        ))}
        {isAdmin && (
          <label className="ml-auto flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={scope === 'all'}
              onChange={(e) => setScope(e.target.checked ? 'all' : 'me')}
            />
            Everyone
          </label>
        )}
      </div>
      <ErrorNote message={error} />
      {!report ? (
        <Loading />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Spent" value={describeCost(report.totals, report.currency)} />
            <Stat label="Calls" value={report.totals.events.toLocaleString()} />
            <Stat
              label="Tokens"
              value={(
                report.totals.input_tokens +
                report.totals.output_tokens +
                report.totals.cache_read_tokens
              ).toLocaleString()}
            />
          </div>
          {report.totals.priced_events < report.totals.events && (
            <p className="text-xs text-gray-500">
              {report.totals.events - report.totals.priced_events} call(s) have no known
              price (custom endpoints, plan-billed services, models the catalogue does not
              list), so the total is a minimum.
            </p>
          )}
          <Days groups={report.by_day} />
          <Breakdown
            title="By app"
            groups={report.by_app}
            label={(g) => (g.key.app ? APP_LABELS[g.key.app as AppName] : 'Direct')}
            currency={report.currency}
          />
          <Breakdown
            title="By model"
            groups={report.by_model}
            label={(g) =>
              `${g.key.model || '—'} (${g.key.provider_name || g.key.vendor})`
            }
            currency={report.currency}
          />
          <Breakdown
            title="By job"
            groups={report.by_task}
            label={(g) =>
              TASKS.find((t) => t.id === g.key.task)?.label ?? g.key.task ?? 'Unspecified'
            }
            currency={report.currency}
          />
        </>
      )}
    </>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs uppercase text-gray-500">{label}</p>
      <p className="text-2xl font-semibold text-gray-900 dark:text-gray-100">{value}</p>
    </div>
  )
}

function Days({ groups }: { groups: UsageGroup[] }) {
  if (groups.length === 0) return null
  const max = Math.max(...groups.map((g) => g.cost), 0.000001)
  return (
    <div className="card p-4">
      <p className="mb-2 text-xs uppercase text-gray-500">Spend per day</p>
      <div className="flex h-24 items-end gap-1" role="img" aria-label="Spend per day">
        {groups.map((g) => (
          <div
            key={g.key.day}
            className="flex-1 rounded-t bg-blue-500/70"
            style={{ height: `${Math.max(2, (g.cost / max) * 100)}%` }}
            title={`${g.key.day}: $${g.cost.toFixed(4)} over ${g.events} call(s)`}
          />
        ))}
      </div>
    </div>
  )
}

function Breakdown({
  title,
  groups,
  label,
  currency,
}: {
  title: string
  groups: UsageGroup[]
  label: (g: UsageGroup) => string
  currency: string
}) {
  if (groups.length === 0) return null
  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-gray-500">
          <tr>
            <th className="px-4 py-2">{title}</th>
            <th className="px-4 py-2 text-right">Calls</th>
            <th className="px-4 py-2 text-right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {[...groups]
            .sort((a, b) => b.cost - a.cost)
            .map((g) => (
              <tr
                key={JSON.stringify(g.key)}
                className="border-t border-gray-100 dark:border-gray-700"
              >
                <td className="px-4 py-2 text-gray-800 dark:text-gray-200">{label(g)}</td>
                <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-400">
                  {g.events}
                </td>
                <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-400">
                  {describeCost(g, currency)}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  )
}
