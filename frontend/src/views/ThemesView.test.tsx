import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { themesApi, type Theme } from '@/api/themes'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import ThemesView from '@/views/ThemesView'

const base = {
  user_id: null,
  created_at: '',
  updated_at: '',
  mode: 'light' as const,
  bg_type: 'flat' as const,
  bg_color1: '#ffffff',
  bg_color2: null,
  bg_image_url: null,
  bg_image_mode: 'fill' as const,
  bg_blur: 0,
  glass_opacity: 0.3,
  glass_blur: 12,
  shadow_size: 4,
  shadow_blur: 12,
}
const HOUSE: Theme = {
  ...base,
  id: 'house',
  name: 'House',
  scope: 'instance',
  editable: false,
}
const MINE: Theme = {
  ...base,
  id: 'mine',
  name: 'Mine',
  scope: 'user',
  user_id: 'u',
  editable: true,
}

beforeEach(() => {
  vi.spyOn(themesApi, 'list').mockResolvedValue([HOUSE, MINE])
  vi.spyOn(themesApi, 'selected').mockImplementation(async (app) =>
    app === 'notes' ? HOUSE : null
  )
  vi.spyOn(useAppThemeStore.getState(), 'refresh').mockResolvedValue()
  useAuthStore.setState({
    status: 'authenticated',
    user: { id: 'u', username: 'u', is_admin: false },
  })
})

afterEach(() => {
  cleanup()
  useAuthStore.getState().reset()
  vi.restoreAllMocks()
})

describe('ThemesView', () => {
  it('shows where each theme is worn and changes it per app', async () => {
    const choose = vi.spyOn(themesApi, 'choose').mockResolvedValue(MINE)
    render(<ThemesView />)

    const house = await screen.findByLabelText('Where House is worn')
    expect(within(house).getByRole('button', { name: 'Notes' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(within(house).getByRole('button', { name: 'Assets' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )

    const mine = screen.getByLabelText('Where Mine is worn')
    await userEvent.click(within(mine).getByRole('button', { name: 'Assets' }))
    expect(choose).toHaveBeenCalledWith('gam', 'mine')

    // Pressing the one already worn takes it off that app.
    await userEvent.click(within(house).getByRole('button', { name: 'Notes' }))
    expect(choose).toHaveBeenCalledWith('notes', null)
  })

  it('offers editing only for themes the person may change', async () => {
    render(<ThemesView />)
    await screen.findByText('House')
    expect(screen.getAllByTitle('Edit')).toHaveLength(1)
  })
})
