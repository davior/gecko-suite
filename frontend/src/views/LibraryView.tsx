import { useCallback, useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { APP_LABELS, APPS, type AppName, type Scope } from '@/api/envelope'
import {
  libraryApi,
  type LibraryKind,
  type Persona,
  type Prompt,
  type Recipe,
} from '@/api/library'
import { ErrorNote, Loading, PageHeader, ScopeBadge } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'

const TABS: { id: LibraryKind; label: string; blurb: string }[] = [
  {
    id: 'prompts',
    label: 'System prompts',
    blurb:
      'Standing instructions an assistant starts from. One per app can be the default.',
  },
  {
    id: 'recipes',
    label: 'Recipes',
    blurb: 'Saved requests you run by name, like "Summarise for a newsletter".',
  },
  {
    id: 'personas',
    label: 'Personas',
    blurb:
      'An assistant with its own name and instructions, offered in every app that asks for one.',
  },
]

type Row = Prompt | Recipe | Persona

/** Prompts, recipes and personas: the same list-and-edit screen three times. */
export default function LibraryView() {
  const [tab, setTab] = useState<LibraryKind>('prompts')
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await libraryApi[tab].list())
      setError(null)
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }, [tab])

  useEffect(() => {
    // Cleared first so the previous tab's rows never show under the new tab's heading.
    setRows(null)
    void load()
  }, [load])

  const current = TABS.find((t) => t.id === tab)!

  return (
    <>
      <PageHeader title="Prompts & personas">{current.blurb}</PageHeader>
      <div className="flex gap-2" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={
              tab === t.id ? 'btn btn-primary px-3 py-1' : 'btn btn-secondary px-3 py-1'
            }
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <ErrorNote message={error} />
      <Editor key={`new-${tab}`} kind={tab} onSaved={load} />
      {rows === null ? (
        <Loading />
      ) : (
        rows.map((row) => <Editor key={row.id} kind={tab} row={row} onSaved={load} />)
      )}
    </>
  )
}

function textOf(kind: LibraryKind, row?: Row): string {
  if (!row) return ''
  if (kind === 'prompts') return (row as Prompt).content
  if (kind === 'recipes') return (row as Recipe).prompt
  return (row as Persona).system_prompt
}

function Editor({
  kind,
  row,
  onSaved,
}: {
  kind: LibraryKind
  row?: Row
  onSaved: () => Promise<void>
}) {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(row?.name ?? '')
  const [text, setText] = useState(textOf(kind, row))
  const [app, setApp] = useState<AppName | null>(row?.app ?? null)
  const [scope, setScope] = useState<Scope>(row?.scope ?? 'user')
  const [isDefault, setIsDefault] = useState(
    kind === 'prompts' && !!(row as Prompt | undefined)?.is_default
  )
  const [error, setError] = useState<string | null>(null)
  const readOnly = row !== undefined && !row.writable

  async function save() {
    const field =
      kind === 'prompts' ? 'content' : kind === 'recipes' ? 'prompt' : 'system_prompt'
    const body: Record<string, unknown> = { name, app, [field]: text }
    if (kind === 'prompts') body.is_default = isDefault
    try {
      if (row) await libraryApi[kind].update(row.id, body)
      else await libraryApi[kind].create({ ...body, scope })
      setError(null)
      setOpen(false)
      if (!row) {
        setName('')
        setText('')
      }
      await onSaved()
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  if (!open) {
    return row ? (
      <div className="card flex items-center gap-2 px-4 py-3">
        <button
          type="button"
          className="text-left font-medium text-gray-800 dark:text-gray-200"
          onClick={() => setOpen(true)}
        >
          {row.name}
        </button>
        <ScopeBadge scope={row.scope} />
        <span className="text-xs text-gray-500">
          {row.app ? APP_LABELS[row.app] : 'Every app'}
        </span>
        {kind === 'prompts' && (row as Prompt).is_default && (
          <span className="text-xs font-medium text-green-700">Default</span>
        )}
        {row.writable && (
          <button
            type="button"
            className="btn btn-ghost ml-auto p-1 text-red-600"
            aria-label={`Delete ${row.name}`}
            onClick={async () => {
              await libraryApi[kind].remove(row.id)
              await onSaved()
            }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    ) : (
      <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4" /> New{' '}
        {kind === 'prompts' ? 'prompt' : kind.slice(0, -1)}
      </button>
    )
  }

  return (
    <div className="card space-y-3 p-4">
      <input
        className="input"
        placeholder="Name"
        value={name}
        readOnly={readOnly}
        onChange={(e) => setName(e.target.value)}
      />
      <textarea
        className="input min-h-[8rem] font-mono text-xs"
        placeholder={kind === 'recipes' ? 'The request' : 'The instructions'}
        value={text}
        readOnly={readOnly}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-4 text-sm text-gray-700 dark:text-gray-300">
        <label className="flex items-center gap-2">
          For
          <select
            className="input w-auto py-1"
            value={app ?? ''}
            disabled={readOnly}
            onChange={(e) => setApp((e.target.value || null) as AppName | null)}
          >
            <option value="">Every app</option>
            {APPS.map((a) => (
              <option key={a} value={a}>
                {APP_LABELS[a]}
              </option>
            ))}
          </select>
        </label>
        {kind === 'prompts' && !readOnly && (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Default
          </label>
        )}
        {!row && isAdmin && (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={scope === 'instance'}
              onChange={(e) => setScope(e.target.checked ? 'instance' : 'user')}
            />
            Share with everyone
          </label>
        )}
      </div>
      <ErrorNote message={error} />
      <div className="flex gap-2">
        {!readOnly && (
          <button
            type="button"
            className="btn btn-primary"
            disabled={!name || !text}
            onClick={() => void save()}
          >
            Save
          </button>
        )}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setOpen(false)}
        >
          {readOnly ? 'Close' : 'Cancel'}
        </button>
      </div>
    </div>
  )
}
