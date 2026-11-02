import { afterEach, describe, expect, it, vi } from 'vitest'
import { assignmentsApi } from '@/api/assignments'
import { modelsApi } from '@/api/models'
import { providersApi } from '@/api/providers'
import { useRegistryStore } from '@/stores/registry'
import { provider } from '@/test-fixtures'

afterEach(() => {
  vi.restoreAllMocks()
  useRegistryStore.getState().reset()
})

function stub(providers = [provider()]) {
  vi.spyOn(providersApi, 'list').mockResolvedValue(providers)
  vi.spyOn(modelsApi, 'list').mockResolvedValue([])
  vi.spyOn(assignmentsApi, 'list').mockResolvedValue([])
  vi.spyOn(modelsApi, 'catalog').mockResolvedValue([])
}

describe('registry store', () => {
  it('loads everything together', async () => {
    stub()
    await useRegistryStore.getState().load()
    expect(useRegistryStore.getState().status).toBe('ready')
    expect(useRegistryStore.getState().providers).toHaveLength(1)
  })

  it('drops a response that arrives after sign-out', async () => {
    let finish: (rows: ReturnType<typeof provider>[]) => void = () => {}
    vi.spyOn(providersApi, 'list').mockReturnValue(
      new Promise((resolve) => (finish = resolve))
    )
    vi.spyOn(modelsApi, 'list').mockResolvedValue([])
    vi.spyOn(assignmentsApi, 'list').mockResolvedValue([])
    vi.spyOn(modelsApi, 'catalog').mockResolvedValue([])

    const loading = useRegistryStore.getState().load()
    useRegistryStore.getState().reset()
    finish([provider({ name: 'Somebody else' })])
    await loading
    // The previous user's keys must not reappear on the next user's screen.
    expect(useRegistryStore.getState().providers).toEqual([])
    expect(useRegistryStore.getState().status).toBe('idle')
  })

  it('reports a failed write and does not reload', async () => {
    stub()
    const reload = vi.spyOn(useRegistryStore.getState(), 'load')
    const message = await useRegistryStore
      .getState()
      .run(() => Promise.reject(new Error('Nope')))
    expect(message).toBe('Nope')
    expect(reload).not.toHaveBeenCalled()
  })
})
