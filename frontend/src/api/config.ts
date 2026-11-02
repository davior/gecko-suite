import client from '@/api/client'

/**
 * Settings the browser is told at runtime rather than compiled with.
 *
 * A Vite `import.meta.env` value is fixed when the bundle is *built*, so a setting read
 * that way needs one image per environment and cannot be changed by a deployment. That
 * is why `NOTES_BASE_URL` could be set in `.env`, plumbed through docker-compose, and
 * still have no effect on where the sign-in button pointed.
 *
 * Fetched once and held here, not in a Zustand store: it is the same for everybody, it
 * never changes while the tab is open, and `redirectToLogin` has to be able to reach it
 * from outside React.
 */

export interface ClientConfig {
  notes_base_url: string
}

interface DataResponse<T> {
  data: T
}

let cached: ClientConfig | null = null
let inFlight: Promise<ClientConfig> | null = null

export function loadConfig(): Promise<ClientConfig> {
  if (cached) return Promise.resolve(cached)
  if (!inFlight) {
    inFlight = client
      .get<DataResponse<ClientConfig>>('/config')
      .then((response) => {
        cached = response.data.data
        return cached
      })
      .catch((error) => {
        // Clear the in-flight promise so a later attempt refetches. Holding a rejected
        // promise would mean one failed request at boot — a backend still starting —
        // left the sign-in button permanently broken for the life of the tab.
        inFlight = null
        throw error
      })
  }
  return inFlight
}

/** The value if it has already arrived, for render paths that cannot await. */
export function currentConfig(): ClientConfig | null {
  return cached
}

/** Test seam: a module-level cache outlives a component, and so would leak between tests. */
export function resetConfig(): void {
  cached = null
  inFlight = null
}
