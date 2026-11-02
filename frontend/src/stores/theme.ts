import { create } from 'zustand'

/**
 * Light/dark, persisted per browser.
 *
 * The initial class is set by an inline script in index.html so there is no white
 * flash before React mounts; this store owns it from then on.
 */

type Theme = 'light' | 'dark'

const STORAGE_KEY = 'theme'

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* the class is applied either way; only the preference is lost */
  }
}

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggle: () => void
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readStoredTheme(),

  setTheme(theme) {
    applyTheme(theme)
    set({ theme })
  },

  toggle() {
    get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
  },
}))
