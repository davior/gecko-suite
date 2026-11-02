import { create } from 'zustand'
import { apiErrorMessage } from '@/api/client'
import { assignmentsApi, type Assignment } from '@/api/assignments'
import { modelsApi, type CatalogEntry, type Model } from '@/api/models'
import { providersApi, type Provider } from '@/api/providers'

/**
 * Providers, their models, the routing that points at them, and the catalogue.
 *
 * One store because they are one picture: deleting a provider removes its models and
 * the routing that used them (the backend cascades), so the screens showing each must
 * reload together or they show a routing rule pointing at nothing.
 *
 * Every write reloads everything rather than patching local state. The backend decides
 * things the client cannot know in advance (a catalogue fill-in, a cascade, `writable`),
 * and the lists are small.
 */

type Status = 'idle' | 'loading' | 'ready' | 'error'

interface RegistryState {
  providers: Provider[]
  models: Model[]
  assignments: Assignment[]
  catalog: CatalogEntry[]
  status: Status
  error: string | null
  load: () => Promise<void>
  /** Run a write, then reload. Returns the error message, or null on success. */
  run: (write: () => Promise<unknown>) => Promise<string | null>
  reset: () => void
}

// Bumped on every load and reset. A response that comes back after a newer request
// started, or after sign-out, belongs to a state that no longer exists and is dropped.
let generation = 0

export const useRegistryStore = create<RegistryState>((set, get) => ({
  providers: [],
  models: [],
  assignments: [],
  catalog: [],
  status: 'idle',
  error: null,

  async load() {
    const mine = ++generation
    set({ status: get().status === 'ready' ? 'ready' : 'loading', error: null })
    try {
      const [providers, models, assignments, catalog] = await Promise.all([
        providersApi.list(),
        modelsApi.list(),
        assignmentsApi.list(),
        modelsApi.catalog(),
      ])
      if (mine !== generation) return
      set({ providers, models, assignments, catalog, status: 'ready', error: null })
    } catch (error) {
      if (mine !== generation) return
      set({
        status: 'error',
        error: apiErrorMessage(error, 'Could not load the AI settings'),
      })
    }
  },

  async run(write) {
    try {
      await write()
    } catch (error) {
      return apiErrorMessage(error)
    }
    await get().load()
    return null
  },

  reset() {
    generation++
    set({
      providers: [],
      models: [],
      assignments: [],
      catalog: [],
      status: 'idle',
      error: null,
    })
  },
}))
