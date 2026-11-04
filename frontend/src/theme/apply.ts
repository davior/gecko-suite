import type { ThemeValues } from '@/api/themes'

/**
 * Drawing a theme: CSS variables on <html> that `glass.css` reads, `data-glass` to switch
 * those rules on, and `dark` for the mode. Ported from Notes' `applyThemeToDom`, which is
 * where the variable names come from; all four apps share `glass.css`, so a theme looks
 * the same in each.
 *
 * The background is drawn by `<ThemeBackdrop />`, a fixed layer behind the page.
 */

const VARIABLES = [
  '--theme-bg',
  '--theme-bg-size',
  '--theme-bg-filter',
  '--glass-opacity',
  '--glass-blur',
  '--glass-rgb',
  '--shadow-size',
  '--shadow-blur',
  '--shadow-color',
]

export function background(theme: ThemeValues): string {
  if (theme.bg_type === 'gradient' && theme.bg_color2) {
    return `linear-gradient(135deg, ${theme.bg_color1}, ${theme.bg_color2})`
  }
  if (theme.bg_type === 'image' && theme.bg_image_url) {
    // Quoted: the suite only accepts addresses without quotes or brackets, and the
    // quotes keep anything else from being read as more CSS.
    return `url("${theme.bg_image_url}")`
  }
  return theme.bg_color1
}

/** Draw `theme`, or with null take everything off, leaving the light/dark class alone. */
export function applyThemeToDom(theme: ThemeValues | null): void {
  const root = document.documentElement
  if (!theme) {
    root.removeAttribute('data-glass')
    VARIABLES.forEach((v) => root.style.removeProperty(v))
    return
  }
  const dark = theme.mode === 'dark'
  const size =
    theme.bg_image_mode === 'repeat'
      ? 'auto'
      : theme.bg_image_mode === 'stretch'
        ? '100% 100%'
        : 'cover'
  root.style.setProperty('--theme-bg', background(theme))
  root.style.setProperty('--theme-bg-size', size)
  root.style.setProperty(
    '--theme-bg-filter',
    theme.bg_blur > 0 ? `blur(${theme.bg_blur}px)` : 'none'
  )
  root.style.setProperty('--glass-opacity', String(theme.glass_opacity))
  root.style.setProperty('--glass-blur', `${theme.glass_blur}px`)
  root.style.setProperty('--glass-rgb', dark ? '0,0,0' : '255,255,255')
  root.style.setProperty('--shadow-size', `${theme.shadow_size}px`)
  root.style.setProperty('--shadow-blur', `${theme.shadow_blur}px`)
  root.style.setProperty('--shadow-color', dark ? 'rgba(0,0,0,0.55)' : 'rgba(0,0,0,0.25)')
  root.setAttribute('data-glass', theme.mode)
  root.classList.toggle('dark', dark)
}

/**
 * The last theme this browser drew for this app, so a reload looks right at once and
 * still does while the suite cannot be reached. Never the source of truth: the suite's
 * answer replaces it whenever one arrives.
 */
const CACHE_KEY = 'gecko_theme'

export function cachedTheme(): ThemeValues | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as ThemeValues) : null
  } catch {
    return null
  }
}

export function cacheTheme(theme: ThemeValues | null): void {
  try {
    if (theme) localStorage.setItem(CACHE_KEY, JSON.stringify(theme))
    else localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode: the next load asks the suite again */
  }
}
