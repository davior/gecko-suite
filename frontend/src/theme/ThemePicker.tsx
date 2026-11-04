import { useEffect, useRef, useState } from 'react'
import { Check, Palette } from 'lucide-react'
import { background } from '@/theme/apply'
import { useAppThemeStore } from '@/stores/appTheme'

/**
 * The header's theme menu: this app's choice, from every theme the person can see.
 * Making and editing themes happens on the suite's Themes page (`editUrl`).
 */
export default function ThemePicker({ editUrl }: { editUrl: string | null }) {
  const themes = useAppThemeStore((s) => s.themes)
  const selected = useAppThemeStore((s) => s.selected)
  const status = useAppThemeStore((s) => s.status)
  const choose = useAppThemeStore((s) => s.choose)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  // Nothing to offer while the suite cannot be reached; the cached theme stays drawn.
  if (status === 'unavailable') return null

  async function pick(id: string | null) {
    setError(null)
    try {
      await choose(id)
      setOpen(false)
    } catch {
      setError('Could not save that choice. Try again.')
    }
  }

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        className="btn btn-ghost p-2"
        aria-label="Theme"
        aria-expanded={open}
        title="Theme"
        onClick={() => setOpen((o) => !o)}
      >
        <Palette className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="card absolute right-0 z-50 mt-2 w-64 space-y-1 p-2 text-sm shadow-lg"
        >
          <Option
            label="None (plain light or dark)"
            active={!selected}
            onClick={() => pick(null)}
          />
          {themes.map((t) => (
            <Option
              key={t.id}
              label={t.name}
              swatch={background(t)}
              hint={t.scope === 'instance' ? 'Shared' : undefined}
              active={selected?.id === t.id}
              onClick={() => pick(t.id)}
            />
          ))}
          {error && <p className="px-2 py-1 text-xs text-red-600">{error}</p>}
          {editUrl && (
            <a
              href={editUrl}
              className="block border-t border-gray-200 px-2 pt-2 text-xs text-blue-600 hover:underline dark:border-gray-700 dark:text-blue-400"
            >
              Create and edit themes
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function Option({
  label,
  swatch,
  hint,
  active,
  onClick,
}: {
  label: string
  swatch?: string
  hint?: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={active}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-gray-100 dark:hover:bg-gray-700"
      onClick={onClick}
    >
      <span
        className="h-5 w-5 shrink-0 rounded border border-gray-300 dark:border-gray-600"
        style={swatch ? { background: swatch, backgroundSize: 'cover' } : undefined}
      />
      <span className="flex-1 truncate text-gray-800 dark:text-gray-200">{label}</span>
      {hint && <span className="text-xs text-gray-500">{hint}</span>}
      {active && <Check className="h-4 w-4 text-blue-600" />}
    </button>
  )
}
