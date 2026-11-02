import { useEffect, useState } from 'react'
import { APP_LABELS, APPS, type AppName, type Scope } from '@/api/envelope'
import {
  assignmentsApi,
  modelsFor,
  RULE_LABELS,
  TASKS,
  type Assignment,
  type Resolution,
} from '@/api/assignments'
import type { Model } from '@/api/models'
import { ErrorNote, Loading, PageHeader } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'
import { useRegistryStore } from '@/stores/registry'

/**
 * Which model does which job.
 *
 * Two columns: your own choice, and the shared default an admin sets. Yours wins. Each
 * row says what a request would actually use right now, and why, because four layers
 * of override (yours or shared, for this app or every app) are only understandable
 * when the answer is on screen.
 */
export default function RoutingView() {
  const { models, assignments, status, error, load } = useRegistryStore()
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const [app, setApp] = useState<AppName | null>(null)

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <PageHeader title="Routing">
        Choose a model for each job. Jobs without a choice use the one they fall back to
        (shown in grey), so setting the Assistant is often enough.
      </PageHeader>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="App">
        {[null, ...APPS].map((a) => (
          <button
            key={a ?? 'all'}
            type="button"
            role="tab"
            aria-selected={app === a}
            className={
              app === a ? 'btn btn-primary px-3 py-1' : 'btn btn-secondary px-3 py-1'
            }
            onClick={() => setApp(a)}
          >
            {a ? APP_LABELS[a] : 'Every app'}
          </button>
        ))}
      </div>
      <ErrorNote message={error} />
      {status === 'loading' && <Loading />}
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Job</th>
              <th className="px-4 py-2">Your choice</th>
              <th className="px-4 py-2">Shared default</th>
              <th className="px-4 py-2">Uses now</th>
            </tr>
          </thead>
          <tbody>
            {TASKS.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                app={app}
                models={modelsFor(task.id, models)}
                assignments={assignments}
                isAdmin={isAdmin}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function TaskRow({
  task,
  app,
  models,
  assignments,
  isAdmin,
}: {
  task: (typeof TASKS)[number]
  app: AppName | null
  models: Model[]
  assignments: Assignment[]
  isAdmin: boolean
}) {
  const find = (scope: Scope) =>
    assignments.find((a) => a.task === task.id && a.scope === scope && a.app === app)
  const mine = find('user')
  const shared = find('instance')
  const [resolution, setResolution] = useState<Resolution | null>(null)

  useEffect(() => {
    // Re-asked whenever the routing changes, so "uses now" is the server's answer, not
    // a re-implementation of its rules that could drift from them.
    let stale = false
    assignmentsApi
      .resolve(task.id, app)
      .then((r) => !stale && setResolution(r))
      .catch(() => !stale && setResolution(null))
    return () => {
      stale = true
    }
  }, [task.id, app, assignments])

  return (
    <tr className="border-t border-gray-100 dark:border-gray-700">
      <td className="px-4 py-2 font-medium text-gray-800 dark:text-gray-200">
        {task.label}
        {task.fallback && (
          <span className="block text-xs font-normal text-gray-400">
            falls back to {TASKS.find((t) => t.id === task.fallback)?.label}
          </span>
        )}
      </td>
      <td className="px-4 py-2">
        <Choice scope="user" task={task.id} app={app} current={mine} models={models} />
      </td>
      <td className="px-4 py-2">
        {isAdmin ? (
          <Choice
            scope="instance"
            task={task.id}
            app={app}
            current={shared}
            models={models.filter((m) => m.provider_scope === 'instance')}
          />
        ) : (
          <span className="text-gray-600 dark:text-gray-400">
            {shared?.model_label ?? '—'}
          </span>
        )}
      </td>
      <td className="px-4 py-2 text-gray-600 dark:text-gray-400">
        {resolution?.model ? (
          <>
            {resolution.model.label}
            <span className="block text-xs text-gray-400">
              {resolution.rule && RULE_LABELS[resolution.rule]}
              {resolution.matched_task !== task.id && ' (fallback)'}
            </span>
          </>
        ) : (
          <span className="text-gray-400">Nothing — this job is off</span>
        )}
      </td>
    </tr>
  )
}

function Choice({
  scope,
  task,
  app,
  current,
  models,
}: {
  scope: Scope
  task: string
  app: AppName | null
  current: Assignment | undefined
  models: Model[]
}) {
  const run = useRegistryStore((s) => s.run)
  const [error, setError] = useState<string | null>(null)

  async function choose(modelRef: string) {
    setError(
      await run(() =>
        modelRef
          ? assignmentsApi.put({ scope, app, task, model_ref: modelRef })
          : current
            ? assignmentsApi.remove(current.id)
            : Promise.resolve()
      )
    )
  }

  return (
    <>
      <select
        className="input py-1"
        aria-label={`${scope === 'user' ? 'Your' : 'Shared'} model for ${task}`}
        value={current?.model_ref ?? ''}
        onChange={(e) => void choose(e.target.value)}
      >
        <option value="">{scope === 'user' ? 'Use the default' : 'None'}</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} ({m.provider_name})
          </option>
        ))}
      </select>
      <ErrorNote message={error} />
    </>
  )
}
