import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from '@/api/client'
import { currentConfig, loadConfig, resetConfig } from '@/api/config'
import { notesBaseUrl, redirectToLogin } from '@/api/auth'

const served = (url: string) =>
  Promise.resolve({ data: { data: { notes_base_url: url } } })

let get: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  resetConfig()
  get = vi
    .spyOn(client, 'get')
    .mockImplementation(() => served('https://notes.example.test')) as never
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

    get.mockImplementation(() => served('https://notes.example.test') as never)
    await expect(loadConfig()).resolves.toEqual({
      notes_base_url: 'https://notes.example.test',
    })
  })

  it('is null until it has arrived', async () => {
    expect(currentConfig()).toBeNull()
    await loadConfig()
    expect(currentConfig()).toEqual({ notes_base_url: 'https://notes.example.test' })
  })
})

describe('the sign-in redirect', () => {
  it('goes where the backend says, not to a compiled-in address', async () => {
    /**
     * The whole point of the endpoint. A hardcoded production fallback used to sit in
     * `auth.ts`, which is why a dev instance with `NOTES_BASE_URL` set in `.env` still
     * sent people to the live Notes login — silently, and looking like it worked.
     */
    expect(await notesBaseUrl()).toBe('https://notes.example.test')
  })

  it('carries the current page back as the return URL', async () => {
    const assign = vi.fn()
    const original = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { href: 'http://localhost:5174/library?tag=NATO' },
    })
    Object.defineProperty(window.location, 'href', {
      configurable: true,
      get: () => 'http://localhost:5174/library?tag=NATO',
      set: assign,
    })

    await redirectToLogin()

    expect(assign).toHaveBeenCalledWith(
      'https://notes.example.test/login?redirect=' +
        encodeURIComponent('http://localhost:5174/library?tag=NATO')
    )
    Object.defineProperty(window, 'location', { configurable: true, value: original })
  })

  it('does not navigate anywhere when the config cannot be fetched', async () => {
    // Better than a guess: the backend being unreachable is what RequireAuth already
    // reports, and bouncing to another domain would hide it.
    resetConfig()
    get.mockRejectedValue(new Error('offline'))

    await expect(redirectToLogin()).rejects.toThrow()
  })
})
