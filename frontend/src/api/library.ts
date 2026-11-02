import client from '@/api/client'
import type { AppName, DataResponse, ListResponse, Scope } from '@/api/envelope'

interface Scoped {
  id: string
  scope: Scope
  app: AppName | null
  sort_order: number
  writable: boolean
}

export interface Prompt extends Scoped {
  name: string
  content: string
  is_default: boolean
}

export interface Recipe extends Scoped {
  name: string
  prompt: string
  tags: string[]
}

export interface Persona extends Scoped {
  name: string
  description: string
  avatar: string | null
  system_prompt: string
  model_overrides: Record<string, string>
}

export type LibraryKind = 'prompts' | 'recipes' | 'personas'

type Row = { prompts: Prompt; recipes: Recipe; personas: Persona }

function crud<K extends LibraryKind>(kind: K) {
  return {
    list(): Promise<Row[K][]> {
      return client.get<ListResponse<Row[K]>>(`/v1/${kind}`).then((r) => r.data.data)
    },
    create(body: Partial<Row[K]>): Promise<Row[K]> {
      return client
        .post<DataResponse<Row[K]>>(`/v1/${kind}`, body)
        .then((r) => r.data.data)
    },
    update(id: string, body: Partial<Row[K]>): Promise<Row[K]> {
      return client
        .patch<DataResponse<Row[K]>>(`/v1/${kind}/${id}`, body)
        .then((r) => r.data.data)
    },
    remove(id: string): Promise<void> {
      return client.delete(`/v1/${kind}/${id}`).then(() => undefined)
    },
  }
}

/** One backend router (`routers/library.py`) serves all three. */
export const libraryApi = {
  prompts: crud('prompts'),
  recipes: crud('recipes'),
  personas: crud('personas'),
}
