import client from '@/api/client'
import type { DataResponse, ListResponse, Scope } from '@/api/envelope'

export type Vendor =
  'anthropic' | 'openai' | 'deepseek' | 'ollama' | 'custom' | 'fal' | 'deepgram'

export const VENDORS: {
  id: Vendor
  label: string
  needsKey: boolean
  baseUrl: 'none' | 'optional' | 'required'
  anthropicSwitch: boolean
}[] = [
  {
    id: 'anthropic',
    label: 'Anthropic',
    needsKey: true,
    baseUrl: 'none',
    anthropicSwitch: false,
  },
  {
    id: 'openai',
    label: 'OpenAI',
    needsKey: true,
    baseUrl: 'optional',
    anthropicSwitch: false,
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    needsKey: true,
    baseUrl: 'none',
    anthropicSwitch: true,
  },
  {
    id: 'ollama',
    label: 'Ollama (local)',
    needsKey: false,
    baseUrl: 'optional',
    anthropicSwitch: false,
  },
  {
    id: 'custom',
    label: 'Custom endpoint',
    needsKey: true,
    baseUrl: 'required',
    anthropicSwitch: true,
  },
  {
    id: 'fal',
    label: 'fal.ai (images, video)',
    needsKey: true,
    baseUrl: 'none',
    anthropicSwitch: false,
  },
  {
    id: 'deepgram',
    label: 'Deepgram (speech)',
    needsKey: true,
    baseUrl: 'none',
    anthropicSwitch: false,
  },
]

export function vendorLabel(id: string): string {
  return VENDORS.find((v) => v.id === id)?.label ?? id
}

export interface Provider {
  id: string
  scope: Scope
  name: string
  vendor: Vendor
  base_url: string | null
  use_anthropic_api: boolean
  extra_params: Record<string, unknown> | null
  enabled: boolean
  /** The key itself never leaves the suite. */
  api_key_configured: boolean
  writable: boolean
  model_count: number
}

export interface ProviderCreate {
  scope: Scope
  name: string
  vendor: Vendor
  api_key: string
  base_url?: string | null
  use_anthropic_api?: boolean
}

/** `api_key`: omit to keep it, '' to remove it, a value to replace it. */
export type ProviderUpdate = Partial<{
  name: string
  api_key: string
  base_url: string | null
  use_anthropic_api: boolean
  enabled: boolean
}>

export interface TestResult {
  success: boolean
  message: string
}

export const providersApi = {
  list(): Promise<Provider[]> {
    return client.get<ListResponse<Provider>>('/v1/providers').then((r) => r.data.data)
  },
  create(body: ProviderCreate): Promise<Provider> {
    return client
      .post<DataResponse<Provider>>('/v1/providers', body)
      .then((r) => r.data.data)
  },
  update(id: string, body: ProviderUpdate): Promise<Provider> {
    return client
      .patch<DataResponse<Provider>>(`/v1/providers/${id}`, body)
      .then((r) => r.data.data)
  },
  remove(id: string): Promise<void> {
    return client.delete(`/v1/providers/${id}`).then(() => undefined)
  },
  test(providerId: string, modelId?: string): Promise<TestResult> {
    return client
      .post<DataResponse<TestResult>>('/v1/providers/test', {
        provider_id: providerId,
        model_id: modelId,
      })
      .then((r) => r.data.data)
  },
}
