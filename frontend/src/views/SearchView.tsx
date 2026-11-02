import { useCallback, useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import type { Scope } from '@/api/envelope'
import {
  SEARCH_KINDS,
  searchApi,
  type SearchHit,
  type SearchKind,
  type SearchProvider,
} from '@/api/search'
import { ErrorNote, PageHeader, ScopeBadge } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

/**
 * Web search for assistants whose model cannot search by itself. Your own backend wins
 * over the shared one; with neither, the suite uses DuckDuckGo, which needs no key but
 * throttles servers.
 */
export default function SearchView() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const [rows, setRows] = useState<SearchProvider[]>([])
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState<SearchKind>('brave')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [scope, setScope] = useState<Scope>('user')
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<{ provider: string; results: SearchHit[] } | null>(
    null
  )
  const spec = SEARCH_KINDS.find((k) => k.id === kind)!

  const load = useCallback(async () => {
    try {
      setRows(await searchApi.providers())
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function act(action: () => Promise<unknown>) {
    try {
      await action()
      setError(null)
      await load()
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  return (
    <>
      <PageHeader title="Web search">
        Used by assistants whose model has no search of its own. Claude and DeepSeek (on
        its Anthropic endpoint) search by themselves.
      </PageHeader>
      <ErrorNote message={error} />
      <div className="card space-y-3 p-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <select
            className="input"
            value={kind}
            onChange={(e) => setKind(e.target.value as SearchKind)}
          >
            {SEARCH_KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
          {spec.needsKey && (
            <input
              className="input"
              type="password"
              placeholder="API key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
          {spec.needsUrl && (
            <input
              className="input"
              placeholder="https://search.example.com"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          )}
        </div>
        {isAdmin && (
          <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={scope === 'instance'}
              onChange={(e) => setScope(e.target.checked ? 'instance' : 'user')}
            />
            Share with everyone
          </label>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            void act(async () => {
              await searchApi.create({
                scope,
                name: spec.label,
                kind,
                api_key: apiKey,
                base_url: baseUrl || undefined,
              })
              setApiKey('')
              setBaseUrl('')
            })
          }
        >
          Add
        </button>
      </div>
      {rows.map((row) => (
        <div key={row.id} className="card flex items-center gap-2 px-4 py-3 text-sm">
          <span className="font-medium text-gray-800 dark:text-gray-200">{row.name}</span>
          <ScopeBadge scope={row.scope} />
          {row.base_url && <span className="text-xs text-gray-500">{row.base_url}</span>}
          {!row.enabled && (
            <span className="text-xs font-medium text-amber-600">Off</span>
          )}
          {row.writable && (
            <div className="ml-auto flex gap-1">
              <button
                type="button"
                className="btn btn-ghost px-2 py-1"
                onClick={() =>
                  void act(() => searchApi.update(row.id, { enabled: !row.enabled }))
                }
              >
                {row.enabled ? 'Switch off' : 'Switch on'}
              </button>
              <button
                type="button"
                className="btn btn-ghost p-1 text-red-600"
                aria-label={`Delete ${row.name}`}
                onClick={() => void act(() => searchApi.remove(row.id))}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      ))}
      <div className="card space-y-3 p-4">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Try it</h3>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () => setHits(await searchApi.search(query)))
          }}
        >
          <input
            className="input"
            placeholder="Search the web"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="btn btn-secondary" disabled={!query.trim()}>
            Search
          </button>
        </form>
        {hits && (
          <div className="space-y-2">
            <p className="text-xs text-gray-500">Searched with {hits.provider}</p>
            {hits.results.map((hit) => (
              <div key={hit.url} className="text-sm">
                <a
                  href={hit.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium text-blue-600 hover:underline"
                >
                  {hit.title}
                </a>
                <p className="text-gray-600 dark:text-gray-400">{hit.snippet}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
