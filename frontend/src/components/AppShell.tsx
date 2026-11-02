import type { ReactNode } from 'react'
import {
  Activity,
  Boxes,
  KeyRound,
  LogOut,
  Moon,
  Route,
  Search,
  Sun,
  Users,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

interface Props {
  children: ReactNode
}

const NAV = [
  { to: '/providers', label: 'Providers', icon: KeyRound },
  { to: '/routing', label: 'Routing', icon: Route },
  { to: '/library', label: 'Prompts & personas', icon: Users },
  { to: '/search', label: 'Web search', icon: Search },
  { to: '/usage', label: 'Usage', icon: Activity },
]

/** The frame every view sits in, as in GVC: one component, so views cannot drift apart. */
export default function AppShell({ children }: Props) {
  const user = useAuthStore((s) => s.user)
  const signOut = useAuthStore((s) => s.signOut)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggle)
  const nav = user?.is_admin ? [...NAV, { to: '/apps', label: 'Apps', icon: Boxes }] : NAV

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-900">
      <header className="shrink-0 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
        <div className="flex items-center gap-3">
          <span className="text-xl" aria-hidden="true">
            🦎
          </span>
          <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Gecko Suite
          </h1>
          <nav className="ml-6 flex flex-wrap gap-1" aria-label="Sections">
            {nav.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `btn btn-ghost px-3 py-1.5 ${isActive ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-gray-100' : ''}`
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <button
              type="button"
              onClick={toggleTheme}
              className="btn btn-ghost p-2"
              aria-label={
                theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'
              }
            >
              {theme === 'dark' ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
            </button>
            {user && (
              <>
                <span className="text-sm text-gray-600 dark:text-gray-400">
                  {user.username}
                  {user.is_admin && ' (admin)'}
                </span>
                <button
                  type="button"
                  onClick={signOut}
                  className="btn btn-ghost p-2"
                  aria-label="Sign out"
                  title="Sign out"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl space-y-6 p-6">{children}</div>
      </main>
    </div>
  )
}
