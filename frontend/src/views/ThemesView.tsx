import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Lock, Pencil, Plus, Trash2, Upload, Users } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { themesApi, type Theme, type ThemeApp, type ThemeValues } from '@/api/themes'
import { ErrorNote, Loading, PageHeader } from '@/components/Bits'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import { background } from '@/theme/apply'

/**
 * The theme library (M7): make, edit and delete themes, and choose where each is worn.
 *
 * Ported from Notes' ThemeManager. What changed: a theme is worn per app, so each row
 * says which apps wear it and changes that in place; and backgrounds upload to the suite,
 * which serves them at an absolute address any app can draw.
 */

const APP_NAMES: Record<ThemeApp, string> = {
  notes: 'Notes',
  gam: 'Assets',
  gvc: 'Video',
  suite: 'Suite',
}
const ORDER: ThemeApp[] = ['notes', 'gam', 'gvc', 'suite']

const BLANK: ThemeValues = {
  name: '',
  mode: 'light',
  bg_type: 'flat',
  bg_color1: '#e0f2fe',
  bg_color2: '#bfdbfe',
  bg_image_url: null,
  bg_image_mode: 'fill',
  bg_blur: 0,
  glass_opacity: 0.3,
  glass_blur: 12,
  shadow_size: 4,
  shadow_blur: 12,
}

function valuesOf(t: Theme): ThemeValues {
  return {
    name: t.name,
    mode: t.mode,
    bg_type: t.bg_type,
    bg_color1: t.bg_color1,
    bg_color2: t.bg_color2,
    bg_image_url: t.bg_image_url,
    bg_image_mode: t.bg_image_mode,
    bg_blur: t.bg_blur,
    glass_opacity: t.glass_opacity,
    glass_blur: t.glass_blur,
    shadow_size: t.shadow_size,
    shadow_blur: t.shadow_blur,
  }
}

export default function ThemesView() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const refreshWorn = useAppThemeStore((s) => s.refresh)
  const [themes, setThemes] = useState<Theme[] | null>(null)
  const [worn, setWorn] = useState<Record<ThemeApp, string | null>>({
    notes: null,
    gam: null,
    gvc: null,
    suite: null,
  })
  const [editing, setEditing] = useState<{
    id: string | null
    values: ThemeValues
    shared: boolean
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await themesApi.list()
      const choices = await Promise.all(ORDER.map((app) => themesApi.selected(app)))
      setThemes(list)
      setWorn(
        Object.fromEntries(
          ORDER.map((app, i) => [app, choices[i]?.id ?? null])
        ) as Record<ThemeApp, string | null>
      )
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    setError(null)
    try {
      await fn()
      await load()
      // The suite itself may be wearing what just changed.
      await refreshWorn()
    } catch (e) {
      setError(apiErrorMessage(e))
    }
  }

  async function save() {
    if (!editing) return
    const { id, values, shared } = editing
    const body: ThemeValues = {
      ...values,
      name: values.name.trim(),
      bg_color2: values.bg_type === 'gradient' ? values.bg_color2 : null,
      bg_image_url: values.bg_type === 'image' ? values.bg_image_url : null,
    }
    await act(async () => {
      if (id) await themesApi.update(id, body)
      else await themesApi.create({ ...body, scope: shared ? 'instance' : 'user' })
      setEditing(null)
    })
  }

  function toggleWorn(app: ThemeApp, themeId: string) {
    void act(() => themesApi.choose(app, worn[app] === themeId ? null : themeId))
  }

  const shared = (themes ?? []).filter((t) => t.scope === 'instance')
  const mine = (themes ?? []).filter((t) => t.scope === 'user')

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Themes">
          One library for every Gecko app. Each app wears its own choice: pick it here, or
          from the palette in that app&apos;s header.
        </PageHeader>
        {!editing && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setEditing({ id: null, values: { ...BLANK }, shared: false })}
          >
            <Plus className="h-4 w-4" /> New theme
          </button>
        )}
      </div>
      <ErrorNote message={error} />

      {editing && (
        <Editor
          values={editing.values}
          isNew={editing.id === null}
          shared={editing.shared}
          canShare={isAdmin && editing.id === null}
          onChange={(values) => setEditing({ ...editing, values })}
          onShared={(sharedNow) => setEditing({ ...editing, shared: sharedNow })}
          onSave={() => void save()}
          onCancel={() => setEditing(null)}
          onError={setError}
        />
      )}

      {!themes ? (
        !error && <Loading />
      ) : (
        <>
          <Section title="Shared themes" icon={<Users className="h-3.5 w-3.5" />}>
            {shared.length === 0 && (
              <p className="text-sm text-gray-500">
                {isAdmin
                  ? 'None yet. New themes can be shared with everyone.'
                  : 'None yet.'}
              </p>
            )}
            {shared.map((t) => (
              <Row
                key={t.id}
                theme={t}
                worn={worn}
                onWear={toggleWorn}
                onEdit={() => setEditing({ id: t.id, values: valuesOf(t), shared: true })}
                onDelete={() => void act(() => themesApi.remove(t.id))}
              />
            ))}
          </Section>
          <Section title="My themes" icon={<Lock className="h-3.5 w-3.5" />}>
            {mine.length === 0 && <p className="text-sm text-gray-500">None yet.</p>}
            {mine.map((t) => (
              <Row
                key={t.id}
                theme={t}
                worn={worn}
                onWear={toggleWorn}
                onEdit={() =>
                  setEditing({ id: t.id, values: valuesOf(t), shared: false })
                }
                onDelete={() => void act(() => themesApi.remove(t.id))}
              />
            ))}
          </Section>
        </>
      )}
    </>
  )
}

function Section({
  title,
  icon,
  children,
}: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {icon} {title}
      </h3>
      {children}
    </section>
  )
}

function Row({
  theme,
  worn,
  onWear,
  onEdit,
  onDelete,
}: {
  theme: Theme
  worn: Record<ThemeApp, string | null>
  onWear: (app: ThemeApp, themeId: string) => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <div className="card flex flex-wrap items-center gap-3 p-3 text-sm">
      <div
        className="h-10 w-10 shrink-0 rounded-lg border border-white/30"
        style={{ background: background(theme), backgroundSize: 'cover' }}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-gray-900 dark:text-gray-100">
          {theme.name}
        </div>
        <div className="text-xs capitalize text-gray-500">
          {theme.mode} · {theme.bg_type} · {Math.round(theme.glass_opacity * 100)}% glass
        </div>
      </div>
      <div className="flex flex-wrap gap-1" aria-label={`Where ${theme.name} is worn`}>
        {ORDER.map((app) => {
          const on = worn[app] === theme.id
          return (
            <button
              key={app}
              type="button"
              aria-pressed={on}
              title={
                on
                  ? `Stop wearing it in ${APP_NAMES[app]}`
                  : `Wear it in ${APP_NAMES[app]}`
              }
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                on
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300'
              }`}
              onClick={() => onWear(app, theme.id)}
            >
              {APP_NAMES[app]}
            </button>
          )
        })}
      </div>
      {theme.editable && (
        <div className="flex gap-1">
          <button
            type="button"
            className="btn btn-ghost p-2"
            title="Edit"
            onClick={onEdit}
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="btn btn-ghost p-2 text-red-600"
            title="Delete"
            onClick={() => {
              if (
                window.confirm(
                  `Delete "${theme.name}"? Apps wearing it go back to plain.`
                )
              )
                onDelete()
            }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  )
}

function Editor({
  values,
  isNew,
  shared,
  canShare,
  onChange,
  onShared,
  onSave,
  onCancel,
  onError,
}: {
  values: ThemeValues
  isNew: boolean
  shared: boolean
  canShare: boolean
  onChange: (v: ThemeValues) => void
  onShared: (s: boolean) => void
  onSave: () => void
  onCancel: () => void
  onError: (m: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const set = <K extends keyof ThemeValues>(key: K, value: ThemeValues[K]) =>
    onChange({ ...values, [key]: value })

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0]
    if (!picked) return
    setUploading(true)
    try {
      const url = await themesApi.uploadImage(picked)
      onChange({ ...values, bg_type: 'image', bg_image_url: url })
    } catch (err) {
      onError(apiErrorMessage(err, 'The image could not be uploaded'))
    } finally {
      setUploading(false)
      if (file.current) file.current.value = ''
    }
  }

  return (
    <div className="card space-y-4 p-5">
      <h3 className="font-medium text-gray-900 dark:text-gray-100">
        {isNew ? 'New theme' : 'Edit theme'}
      </h3>
      <Preview values={values} />

      <label className="block space-y-1">
        <span className="label">Name</span>
        <input
          className="input w-full"
          value={values.name}
          onChange={(e) => set('name', e.target.value)}
          placeholder="My theme"
        />
      </label>

      <Choice
        label="Mode"
        value={values.mode}
        options={['light', 'dark']}
        onChange={(v) => set('mode', v as ThemeValues['mode'])}
      />
      <Choice
        label="Background"
        value={values.bg_type}
        options={['flat', 'gradient', 'image']}
        onChange={(v) => set('bg_type', v as ThemeValues['bg_type'])}
      />

      {values.bg_type !== 'image' && (
        <div className="flex flex-wrap gap-3">
          <Color
            label={values.bg_type === 'gradient' ? 'Colour 1' : 'Colour'}
            value={values.bg_color1}
            onChange={(v) => set('bg_color1', v)}
          />
          {values.bg_type === 'gradient' && (
            <Color
              label="Colour 2"
              value={values.bg_color2 ?? '#bfdbfe'}
              onChange={(v) => set('bg_color2', v)}
            />
          )}
        </div>
      )}

      {values.bg_type === 'image' && (
        <div className="space-y-2">
          <span className="label">Background image</span>
          <div className="flex gap-2">
            <input
              className="input flex-1 text-xs"
              value={values.bg_image_url ?? ''}
              onChange={(e) => set('bg_image_url', e.target.value || null)}
              placeholder="https://… or upload one"
              aria-label="Background image address"
            />
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => file.current?.click()}
              disabled={uploading}
            >
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              Upload
            </button>
            <input
              ref={file}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              className="hidden"
              onChange={upload}
            />
          </div>
          <select
            className="input"
            value={values.bg_image_mode}
            onChange={(e) =>
              set('bg_image_mode', e.target.value as ThemeValues['bg_image_mode'])
            }
            aria-label="Image size"
          >
            <option value="fill">Fill (cover)</option>
            <option value="stretch">Stretch</option>
            <option value="repeat">Repeat (tile)</option>
          </select>
        </div>
      )}

      <Slider
        label="Background blur"
        value={values.bg_blur}
        max={20}
        step={0.5}
        unit="px"
        onChange={(v) => set('bg_blur', v)}
      />
      <Slider
        label="Glass opacity"
        value={Math.round(values.glass_opacity * 100)}
        max={80}
        step={1}
        unit="%"
        onChange={(v) => set('glass_opacity', v / 100)}
      />
      <Slider
        label="Glass blur"
        value={values.glass_blur}
        max={30}
        step={0.5}
        unit="px"
        onChange={(v) => set('glass_blur', v)}
      />
      <Slider
        label="Shadow size"
        value={values.shadow_size}
        max={20}
        step={0.5}
        unit="px"
        onChange={(v) => set('shadow_size', v)}
      />
      <Slider
        label="Shadow blur"
        value={values.shadow_blur}
        max={40}
        step={1}
        unit="px"
        onChange={(v) => set('shadow_blur', v)}
      />

      {canShare && (
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input
            type="checkbox"
            checked={shared}
            onChange={(e) => onShared(e.target.checked)}
          />
          Share with everyone
        </label>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onSave}
          disabled={!values.name.trim()}
        >
          Save
        </button>
        <button type="button" className="btn btn-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function Preview({ values }: { values: ThemeValues }) {
  const dark = values.mode === 'dark'
  const rgb = dark ? '0,0,0' : '255,255,255'
  const shadow = dark ? 'rgba(0,0,0,0.55)' : 'rgba(0,0,0,0.25)'
  return (
    <div
      className="relative flex h-32 items-center justify-center overflow-hidden rounded-xl"
      style={{
        background: background(values),
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      {values.bg_blur > 0 && (
        <div
          className="absolute inset-0"
          style={{ backdropFilter: `blur(${values.bg_blur}px)` }}
        />
      )}
      <div
        className="relative w-44 rounded-lg p-3"
        style={{
          background: `rgba(${rgb}, ${values.glass_opacity})`,
          backdropFilter: `blur(${values.glass_blur}px)`,
          border: `1px solid rgba(${rgb}, ${values.glass_opacity * 1.5})`,
          boxShadow: `0 ${values.shadow_size}px ${values.shadow_blur}px ${shadow}`,
          color: dark ? '#f1f5f9' : '#1a1a2e',
        }}
      >
        <p className="text-xs font-semibold">{values.name || 'My theme'}</p>
        <p className="text-[10px] opacity-70">A glass panel</p>
      </div>
    </div>
  )
}

function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="label">{label}</legend>
      <div className="flex flex-wrap gap-3">
        {options.map((o) => (
          <label
            key={o}
            className="flex items-center gap-2 text-sm capitalize text-gray-700 dark:text-gray-300"
          >
            <input type="radio" checked={value === o} onChange={() => onChange(o)} />
            {o}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function Color({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="flex-1 space-y-1">
      <span className="label">{label}</span>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-10 rounded border p-0.5"
          aria-label={`${label} picker`}
        />
        <input
          className="input flex-1"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
        />
      </div>
    </label>
  )
}

function Slider({
  label,
  value,
  max,
  step,
  unit,
  onChange,
}: {
  label: string
  value: number
  max: number
  step: number
  unit: string
  onChange: (v: number) => void
}) {
  return (
    <label className="block space-y-1">
      <span className="flex justify-between">
        <span className="label">{label}</span>
        <span className="text-xs text-gray-500">
          {value}
          {unit}
        </span>
      </span>
      <input
        type="range"
        min={0}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-blue-600"
      />
    </label>
  )
}
