import client from '@/api/client'
import type { AppName, DataResponse, ListResponse, Scope } from '@/api/envelope'
import type { Capability, Model } from '@/api/models'

export const TASKS: {
  id: string
  label: string
  needs: Capability[]
  fallback?: string
}[] = [
  { id: 'chat.assistant', label: 'Assistant', needs: ['chat'] },
  { id: 'chat.draft', label: 'Drafting', needs: ['chat'], fallback: 'chat.assistant' },
  { id: 'chat.edit', label: 'Editing', needs: ['chat'], fallback: 'chat.assistant' },
  {
    id: 'chat.quick',
    label: 'Quick answers',
    needs: ['chat'],
    fallback: 'chat.assistant',
  },
  {
    id: 'enrich.describe',
    label: 'Describing images',
    needs: ['chat', 'vision'],
    fallback: 'chat.assistant',
  },
  {
    id: 'enrich.summarize',
    label: 'Summarising',
    needs: ['chat'],
    fallback: 'chat.assistant',
  },
  { id: 'enrich.autotag', label: 'Tagging', needs: ['chat'], fallback: 'chat.assistant' },
  { id: 'embed.text', label: 'Search embeddings', needs: ['embed'] },
  { id: 'image.generate', label: 'Image generation', needs: ['image_gen'] },
  {
    id: 'image.edit',
    label: 'Image editing',
    needs: ['image_edit'],
    fallback: 'image.generate',
  },
  { id: 'video.generate', label: 'Video generation', needs: ['video_gen'] },
  { id: 'speech.tts', label: 'Text to speech', needs: ['tts'] },
  { id: 'speech.stt', label: 'Transcription', needs: ['stt'] },
]

export interface Assignment {
  id: string
  scope: Scope
  app: AppName | null
  task: string
  model_ref: string
  model_id: string
  model_label: string
  provider_name: string
  writable: boolean
}

export interface Resolution {
  task: string
  app: AppName | null
  model: Model | null
  matched_task: string | null
  rule: 'user+app' | 'user' | 'instance+app' | 'instance' | null
}

export const assignmentsApi = {
  list(): Promise<Assignment[]> {
    return client
      .get<ListResponse<Assignment>>('/v1/assignments')
      .then((r) => r.data.data)
  },
  put(body: {
    scope: Scope
    app: AppName | null
    task: string
    model_ref: string
  }): Promise<Assignment> {
    return client
      .put<DataResponse<Assignment>>('/v1/assignments', body)
      .then((r) => r.data.data)
  },
  remove(id: string): Promise<void> {
    return client.delete(`/v1/assignments/${id}`).then(() => undefined)
  },
  resolve(task: string, app: AppName | null): Promise<Resolution> {
    return client
      .get<DataResponse<Resolution>>('/v1/resolve', {
        params: { task, app: app ?? undefined },
      })
      .then((r) => r.data.data)
  },
}

/** Models able to do a task: everything it needs, and switched on. */
export function modelsFor(task: string, models: Model[]): Model[] {
  const needs = TASKS.find((t) => t.id === task)?.needs ?? []
  return models.filter((m) => m.usable && needs.every((n) => m.capabilities.includes(n)))
}

export const RULE_LABELS: Record<NonNullable<Resolution['rule']>, string> = {
  'user+app': 'your choice for this app',
  user: 'your choice',
  'instance+app': 'the shared choice for this app',
  instance: 'the shared choice',
}
