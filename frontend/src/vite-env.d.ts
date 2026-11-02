/// <reference types="vite/client" />

/**
 * The environment variables this app reads, declared so a typo is a compile error
 * rather than a silent `undefined` at runtime.
 *
 * Optional: unset, the app talks to a same-origin `/api`, which is what the built image
 * should do.
 *
 * There is deliberately only one. A `VITE_` value is baked in when the bundle is built,
 * so anything that has to differ per *deployment* cannot live here — the sign-in URL
 * used to, and could not be changed without rebuilding the image. It comes from
 * `GET /api/config` now.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
