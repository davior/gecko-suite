import type { ReactNode } from 'react'
import type { Scope } from '@/api/envelope'

/** "Shared" or "Yours": the one thing a reader must know about every row. */
export function ScopeBadge({ scope }: { scope: Scope }) {
  return scope === 'instance' ? (
    <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs font-medium text-purple-700 dark:bg-purple-900/40 dark:text-purple-300">
      Shared
    </span>
  ) : (
    <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
      Yours
    </span>
  )
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p
      role="alert"
      className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300"
    >
      {message}
    </p>
  )
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="space-y-1">
      <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
      {children && <p className="text-sm text-gray-600 dark:text-gray-400">{children}</p>}
    </div>
  )
}

export function Loading() {
  return <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
}
