import client from '@/api/client'
import type { DataResponse, ListResponse, Scope } from '@/api/envelope'

export const CAPABILITIES = [
  'chat',
  'vision',
  'tools',
  'thinking',
  'web_search',
  'embed',
  'image_gen',
  'image_edit',
  'video_gen',
  'tts',
  'stt',
] as const
export type Capability = (typeof CAPABILITIES)[number]

export interface Pricing {
  currency?: string
  input?: number
  output?: number
  cache_read?: number
  cache_write?: number
  per_image?: number
  per_second?: number
}

export interface Model {
  id: string
  provider_id: string
  provider_name: string
  provider_scope: Scope
  vendor: string
  model_id: string
  label: string
  capabilities: Capability[]
  max_tokens: number | null
  context_window: number | null
  embed_dims: number | null
  /** What a text-to-speech model offers: ids, or objects with an `id`. */
  voices: Array<string | { id: string }> | null
  /** The model's own settings; for speech, the request format (see `SpeechSettings`). */
  params: Record<string, unknown> | null
  pricing: Pricing | null
  /** What a call is actually costed at: the model's own price, or the catalogue's. */
  effective_pricing: Pricing | null
  is_active: boolean
  usable: boolean
  writable: boolean
}

export interface ModelCreate {
  model_id: string
  from_catalog?: boolean
  label?: string
  capabilities?: Capability[]
  max_tokens?: number
}

export type ModelUpdate = Partial<{
  label: string
  capabilities: Capability[]
  max_tokens: number
  is_active: boolean
  pricing: Pricing
  /** `[]` clears them. */
  voices: string[]
  /** `{}` clears them. */
  params: Record<string, unknown>
}>

export interface CatalogEntry {
  vendor: string
  model_id: string
  label: string
  capabilities: Capability[]
  max_tokens: number | null
  context_window: number | null
  pricing: Pricing | null
  note: string
}

export const modelsApi = {
  list(): Promise<Model[]> {
    return client.get<ListResponse<Model>>('/v1/models').then((r) => r.data.data)
  },
  create(providerId: string, body: ModelCreate): Promise<Model> {
    return client
      .post<DataResponse<Model>>(`/v1/providers/${providerId}/models`, body)
      .then((r) => r.data.data)
  },
  update(id: string, body: ModelUpdate): Promise<Model> {
    return client
      .patch<DataResponse<Model>>(`/v1/models/${id}`, body)
      .then((r) => r.data.data)
  },
  remove(id: string): Promise<void> {
    return client.delete(`/v1/models/${id}`).then(() => undefined)
  },
  catalog(): Promise<CatalogEntry[]> {
    return client.get<ListResponse<CatalogEntry>>('/v1/catalog').then((r) => r.data.data)
  },
}

/** "$4 / $20 per M tokens", or a sentence saying the price is unknown. */
export function describePrice(pricing: Pricing | null, vendor?: string): string {
  if (!pricing) return 'Price unknown'
  if (pricing.input === 0 && pricing.output === 0)
    return vendor === 'ollama' ? 'Free (local)' : 'Free'
  if (pricing.input !== undefined && pricing.output !== undefined) {
    return `$${money(pricing.input)} / $${money(pricing.output)} per M tokens`
  }
  if (pricing.input !== undefined) return `$${money(pricing.input)} per M tokens`
  return 'Price unknown'
}

/** "4", "0.10": whole dollars stay whole, cents always show two digits. */
function money(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2)
}
