import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { themesApi, type Theme } from '@/api/themes'
import { APP, useAppThemeStore } from '@/stores/appTheme'
import { applyThemeToDom, background, cachedTheme } from '@/theme/apply'

const OCEAN: Theme = {
  id: 't1',
  scope: 'user',
  user_id: 'u',
  editable: true,
  created_at: '',
  updated_at: '',
  name: 'Ocean',
  mode: 'dark',
  bg_type: 'gradient',
  bg_color1: '#0a1f44',
  bg_color2: '#112233',
  bg_image_url: null,
  bg_image_mode: 'fill',
  bg_blur: 0,
  glass_opacity: 0.4,
  glass_blur: 10,
  shadow_size: 6,
  shadow_blur: 14,
}

const root = () => document.documentElement

beforeEach(() => {
  localStorage.clear()
  useAppThemeStore.getState().reset()
  root().classList.remove('dark')
})

afterEach(() => vi.restoreAllMocks())

describe('wearing a theme', () => {
  it('draws the suite’s choice for this app and remembers it for next time', async () => {
    vi.spyOn(themesApi, 'list').mockResolvedValue([OCEAN])
    const selected = vi.spyOn(themesApi, 'selected').mockResolvedValue(OCEAN)

    await useAppThemeStore.getState().load()

    expect(selected).toHaveBeenCalledWith(APP)
    expect(root().getAttribute('data-glass')).toBe('dark')
    expect(root().classList.contains('dark')).toBe(true)
    expect(root().style.getPropertyValue('--glass-opacity')).toBe('0.4')
    expect(cachedTheme()?.name).toBe('Ocean')
  })

  it('keeps the last theme drawn when the suite cannot be reached', async () => {
    // The apps survive the suite being down: a reload looks as it did, and nothing
    // in the page says anything went wrong.
    localStorage.setItem('gecko_theme', JSON.stringify(OCEAN))
    vi.spyOn(themesApi, 'list').mockRejectedValue(new Error('offline'))
    vi.spyOn(themesApi, 'selected').mockRejectedValue(new Error('offline'))

    await useAppThemeStore.getState().load()

    expect(useAppThemeStore.getState().status).toBe('unavailable')
    expect(root().getAttribute('data-glass')).toBe('dark')
  })

  it('saves a choice under this app’s own name, and none returns to plain', async () => {
    const choose = vi.spyOn(themesApi, 'choose').mockResolvedValue(OCEAN)
    await useAppThemeStore.getState().choose('t1')
    expect(choose).toHaveBeenCalledWith(APP, 't1')
    expect(root().hasAttribute('data-glass')).toBe(true)

    choose.mockResolvedValue(null)
    await useAppThemeStore.getState().choose(null)
    expect(root().hasAttribute('data-glass')).toBe(false)
    expect(cachedTheme()).toBeNull()
  })

  it('takes everything off when the person signs out', () => {
    applyThemeToDom(OCEAN)
    localStorage.setItem('gecko_theme', JSON.stringify(OCEAN))
    useAppThemeStore.getState().reset()
    expect(root().hasAttribute('data-glass')).toBe(false)
    expect(root().style.getPropertyValue('--theme-bg')).toBe('')
    expect(cachedTheme()).toBeNull()
  })
})

describe('background', () => {
  it('quotes an image address and builds a gradient from both colours', () => {
    expect(
      background({ ...OCEAN, bg_type: 'image', bg_image_url: 'https://s.example/a.png' })
    ).toBe('url("https://s.example/a.png")')
    expect(background(OCEAN)).toBe('linear-gradient(135deg, #0a1f44, #112233)')
    expect(background({ ...OCEAN, bg_type: 'flat' })).toBe('#0a1f44')
  })
})
