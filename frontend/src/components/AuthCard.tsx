import type { ReactNode } from 'react'

/**
 * The frame of every page a signed-out visitor sees: sign in, register, recovery. No
 * navigation, because nothing behind it is reachable yet, and the suite's name, because
 * someone sent here from Notes or GAM should see where they have landed.
 */
export default function AuthCard({
  title,
  children,
  footer,
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-10 dark:bg-gray-900">
      <div className="w-full max-w-sm space-y-4">
        <div className="flex items-center justify-center gap-2">
          <span className="text-2xl" aria-hidden="true">
            🦎
          </span>
          <span className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Gecko Suite
          </span>
        </div>
        <div className="card space-y-4 p-6">
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {title}
          </h1>
          {children}
        </div>
        {footer && (
          <div className="space-y-1 text-center text-sm text-gray-600 dark:text-gray-400">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p
      role="status"
      className="rounded-lg bg-green-50 px-3 py-2 text-sm text-green-800 dark:bg-green-900/30 dark:text-green-300"
    >
      {children}
    </p>
  )
}

/** A labelled input. The label wraps it, so the two cannot drift apart. */
export function Field({
  label,
  ...input
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block space-y-1">
      <span className="label">{label}</span>
      <input className="input w-full" {...input} />
    </label>
  )
}
