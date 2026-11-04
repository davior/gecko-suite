import type { Model } from '@/api/models'
import type { Provider } from '@/api/providers'

/** Rows as the backend returns them, for component tests to vary. */
export function provider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: 'p1',
    scope: 'user',
    name: 'Claude',
    vendor: 'anthropic',
    base_url: null,
    use_anthropic_api: false,
    extra_params: null,
    enabled: true,
    api_key_configured: true,
    writable: true,
    model_count: 1,
    ...overrides,
  }
}

export function model(overrides: Partial<Model> = {}): Model {
  return {
    id: 'm1',
    provider_id: 'p1',
    provider_name: 'Claude',
    provider_scope: 'user',
    vendor: 'anthropic',
    model_id: 'claude-opus-5-5',
    label: 'Claude Opus 5.5',
    capabilities: ['chat', 'tools', 'vision'],
    max_tokens: 128000,
    context_window: 1000000,
    embed_dims: null,
    voices: null,
    params: null,
    pricing: null,
    effective_pricing: { currency: 'USD', input: 4, output: 20 },
    is_active: true,
    usable: true,
    writable: true,
    ...overrides,
  }
}
