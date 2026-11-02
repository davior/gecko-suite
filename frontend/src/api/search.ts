import client from '@/api/client'
import type { DataResponse, ListResponse, Scope } from '@/api/envelope'

export type SearchKind = 'duckduckgo' | 'brave' | 'tavily' | 'searxng'

export const SEARCH_KINDS: {
  id: SearchKind
  label: string
  needsKey: boolean
  needsUrl: boolean
}[] = [
  { id: 'duckduckgo', label: 'DuckDuckGo (no key)', needsKey: false, needsUrl: false },
  { id: 'brave', label: 'Brave Search API', needsKey: true, needsUrl: false },
  { id: 'tavily', label: 'Tavily', needsKey: true, needsUrl: false },
  { id: 'searxng', label: 'SearXNG (your own)', needsKey: false, needsUrl: true },
]

export interface SearchProvider {
  id: string
  scope: Scope
  name: string
  kind: SearchKind
  base_url: string | null
  enabled: boolean
  api_key_configured: boolean
  writable: boolean
}

export interface SearchHit {
  title: string
  url: string
  snippet: string
  published: string | null
}

export const searchApi = {
  providers(): Promise<SearchProvider[]> {
    return client
      .get<ListResponse<SearchProvider>>('/v1/search/providers')
      .then((r) => r.data.data)
  },
  create(body: {
    scope: Scope
    name: string
    kind: SearchKind
    api_key?: string
    base_url?: string
  }): Promise<SearchProvider> {
    return client
      .post<DataResponse<SearchProvider>>('/v1/search/providers', body)
      .then((r) => r.data.data)
  },
  update(
    id: string,
    body: Partial<{ name: string; api_key: string; base_url: string; enabled: boolean }>
  ): Promise<SearchProvider> {
    return client
      .patch<DataResponse<SearchProvider>>(`/v1/search/providers/${id}`, body)
      .then((r) => r.data.data)
  },
  remove(id: string): Promise<void> {
    return client.delete(`/v1/search/providers/${id}`).then(() => undefined)
  },
  search(
    query: string
  ): Promise<{ provider: string; kind: SearchKind; results: SearchHit[] }> {
    return client
      .post<DataResponse<{ provider: string; kind: SearchKind; results: SearchHit[] }>>(
        '/v1/search/web',
        { query }
      )
      .then((r) => r.data.data)
  },
}
