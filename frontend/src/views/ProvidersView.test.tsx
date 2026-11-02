import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { assignmentsApi } from '@/api/assignments'
import { modelsApi } from '@/api/models'
import { providersApi } from '@/api/providers'
import { useRegistryStore } from '@/stores/registry'
import ProvidersView from '@/views/ProvidersView'
import { model, provider } from '@/test-fixtures'

beforeEach(() => {
  vi.spyOn(providersApi, 'list').mockResolvedValue([
    provider(),
    provider({ id: 'p2', scope: 'instance', name: 'House Claude', writable: false }),
  ])
  vi.spyOn(modelsApi, 'list').mockResolvedValue([model()])
  vi.spyOn(assignmentsApi, 'list').mockResolvedValue([])
  vi.spyOn(modelsApi, 'catalog').mockResolvedValue([])
})

afterEach(() => {
  // Unmount first: a store reset re-renders whatever is still mounted, and with the
  // mocks restored its effects would reach for the real network.
  cleanup()
  useRegistryStore.getState().reset()
  vi.restoreAllMocks()
})

describe('ProvidersView', () => {
  it('separates shared providers from yours, and lets you change only yours', async () => {
    render(<ProvidersView />)
    const shared = (await screen.findByText('House Claude')).closest(
      '.card'
    ) as HTMLElement
    const mine = screen
      .getByText('Claude', { selector: 'h4' })
      .closest('.card') as HTMLElement

    expect(within(shared).getByText('Shared')).toBeInTheDocument()
    expect(within(shared).queryByRole('button', { name: /delete/i })).toBeNull()
    expect(
      within(mine).getByRole('button', { name: /delete claude/i })
    ).toBeInTheDocument()
    // Anyone may test a shared key: it is used, never shown.
    expect(within(shared).getByRole('button', { name: 'Test' })).toBeInTheDocument()
  })

  it('shows whether a key is set, never the key', async () => {
    render(<ProvidersView />)
    expect(await screen.findAllByText('Key set')).toHaveLength(2)
  })

  it('shows the price a model is costed at', async () => {
    render(<ProvidersView />)
    expect(await screen.findByText('$4 / $20 per M tokens')).toBeInTheDocument()
  })

  it('adds a model by its id', async () => {
    const create = vi.spyOn(modelsApi, 'create').mockResolvedValue(model())
    render(<ProvidersView />)
    const mine = (await screen.findByText('Claude', { selector: 'h4' })).closest(
      '.card'
    ) as HTMLElement
    await userEvent.type(within(mine).getByPlaceholderText(/add a model/i), 'my-model')
    await userEvent.click(within(mine).getByRole('button', { name: /add model/i }))
    // Not in the catalogue, so it starts as a chat model whose capabilities can be ticked.
    expect(create).toHaveBeenCalledWith('p1', {
      model_id: 'my-model',
      capabilities: ['chat'],
    })
  })
})
