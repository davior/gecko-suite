import type { ReactNode } from 'react'
import {
  Activity,
  Boxes,
  KeyRound,
  LogOut,
  Moon,
  Palette,
  Route,
  Search,
  Sun,
  UserCog,
  Users,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import ThemePicker from '@/theme/ThemePicker'
import { useThemeStore } from '@/stores/theme'
import favicon from '@/assets/favicon.svg'

interface Props {
  children: ReactNode
}

const NAV = [
  { to: '/providers', label: 'Providers', icon: KeyRound },
  { to: '/routing', label: 'Routing', icon: Route },
  { to: '/library', label: 'Prompts & personas', icon: Users },
  { to: '/search', label: 'Web search', icon: Search },
  { to: '/usage', label: 'Usage', icon: Activity },
  { to: '/themes', label: 'Themes', icon: Palette },
]

/** The frame every view sits in, as in GVC: one component, so views cannot drift apart. */
export default function AppShell({ children }: Props) {
  const user = useAuthStore((s) => s.user)
  const signOut = useAuthStore((s) => s.signOut)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggle)
  // A worn theme brings its own light or dark, so the toggle would only fight it.
  const wearing = useAppThemeStore((s) => s.selected !== null)
  const nav = user?.is_admin
    ? [
        ...NAV,
        { to: '/apps', label: 'Apps', icon: Boxes },
        { to: '/users', label: 'Users', icon: UserCog },
      ]
    : NAV

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-900">
      {/* relative z-30: a glass theme blurs the header (assets/glass.css), and a blurred
          element is its own stacking layer, so the theme popup's z-50 only orders it
          inside the header. Without a z-index of its own the whole header paints in
          document order, under the glass cards and pills of the page below it. Nothing else here sets a z-index, so 30 only has to beat the page. */}
      <header className="relative z-30 shrink-0 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
        <div className="flex items-center gap-3">
          <img src={favicon} alt="" aria-hidden="true" className="h-7 w-7" />
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
            {user && <ThemePicker editUrl="/themes" />}
            {!wearing && (
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
            )}
            {user && (
              <>
                <NavLink
                  to="/account"
                  className="text-sm text-gray-600 hover:underline dark:text-gray-400"
                  title="Your account"
                >
                  {user.username}
                  {user.is_admin && ' (admin)'}
                </NavLink>
                <button
                  type="button"
                  onClick={() => void signOut()}
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
