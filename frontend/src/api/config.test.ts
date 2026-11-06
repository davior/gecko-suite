import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from '@/api/client'
import { currentConfig, loadConfig, resetConfig } from '@/api/config'
import { redirectToLogin } from '@/api/auth'

const CONFIG = {
  login_redirect_origins: ['https://assets.example.test'],
  email_enabled: true,
}
const served = () => Promise.resolve({ data: { data: CONFIG } })

let get: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  resetConfig()
  get = vi.spyOn(client, 'get').mockImplementation(() => served()) as never
})

afterEach(() => {
  vi.restoreAllMocks()
  resetConfig()
})

describe('loadConfig', () => {
  it('asks the backend once however many callers want it', async () => {
    const [a, b] = await Promise.all([loadConfig(), loadConfig()])
    await loadConfig()

    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/config')
    expect(a).toEqual(b)
  })

  it('retries after a failure instead of staying broken for the life of the tab', async () => {
    /**
     * Holding the rejected promise would mean one failed request at boot — a backend
     * still starting, which is the normal case under docker compose — left the sign-in
     * button dead until the user reloaded.
     */
    resetConfig()
    get.mockRejectedValueOnce(new Error('backend still starting'))

    await expect(loadConfig()).rejects.toThrow()

    get.mockImplementation(() => served() as never)
    await expect(loadConfig()).resolves.toEqual(CONFIG)
  })

  it('is null until it has arrived', async () => {
    expect(currentConfig()).toBeNull()
    await loadConfig()
    expect(currentConfig()).toEqual(CONFIG)
  })
})

describe('the sign-in redirect', () => {
  it("goes to this suite's own sign-in page, carrying the current page back", () => {
    const original = window.location
    const here = { href: 'https://suite.example.test/usage?range=30d' }
    Object.defineProperty(window, 'location', { configurable: true, value: here })

    redirectToLogin()

    expect(here.href).toBe(
      '/login?redirect=' +
        encodeURIComponent('https://suite.example.test/usage?range=30d')
    )
    Object.defineProperty(window, 'location', { configurable: true, value: original })
  })
})
