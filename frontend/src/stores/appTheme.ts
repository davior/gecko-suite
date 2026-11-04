import { create } from 'zustand'
import { themesApi, type Theme, type ThemeApp } from '@/api/themes'
import { applyThemeToDom, cacheTheme, cachedTheme } from '@/theme/apply'
import { useThemeStore } from '@/stores/theme'

/**
 * Which theme this app wears (M7), chosen per app and kept by the suite.
 *
 * With none chosen, the app is its plain light or dark (`stores/theme.ts`), exactly as
 * before themes. A chosen theme brings its own mode, so the header's light/dark toggle
 * is hidden while one is worn.
 *
 * Copied into GAM and GVC with `APP` and the api changed; nothing else here knows
 * which app it is in.
 */

export const APP: ThemeApp = 'suite'

type Status = 'idle' | 'loading' | 'ready' | 'unavailable'

interface AppThemeState {
  themes: Theme[]
  selected: Theme | null
  status: Status
  /** Draw the cached theme, then ask the suite for the real choice and the list. */
  load: () => Promise<void>
  choose: (themeId: string | null) => Promise<void>
  /** After an edit on the Themes page, so a worn theme redraws with its new values. */
  refresh: () => Promise<void>
  reset: () => void
}

function wear(theme: Theme | null) {
  if (theme) {
    applyThemeToDom(theme)
  } else {
    applyThemeToDom(null)
    // Back to the plain mode this browser had chosen.
    const plain = useThemeStore.getState()
    plain.setTheme(plain.theme)
  }
  cacheTheme(theme)
}

let generation = 0

export const useAppThemeStore = create<AppThemeState>((set, get) => ({
  themes: [],
  selected: null,
  status: 'idle',

  async load() {
    const mine = ++generation
    const cached = cachedTheme()
    if (cached) applyThemeToDom(cached)
    set({ status: 'loading' })
    try {
      const [themes, selected] = await Promise.all([
        themesApi.list(),
        themesApi.selected(APP),
      ])
      if (mine !== generation) return
      set({ themes, selected, status: 'ready' })
      wear(selected)
    } catch {
      if (mine !== generation) return
      // The suite is down or unreachable: keep whatever is drawn, say nothing.
      set({ status: 'unavailable' })
    }
  },

  async choose(themeId) {
    const mine = ++generation
    const selected = await themesApi.choose(APP, themeId)
    if (mine !== generation) return
    set({ selected })
    wear(selected)
  },

  async refresh() {
    await get().load()
  },

  reset() {
    generation++
    applyThemeToDom(null)
    cacheTheme(null)
    set({ themes: [], selected: null, status: 'idle' })
  },
}))
