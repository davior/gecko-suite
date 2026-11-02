import client from '@/api/client'
import type { AppName, DataResponse, ListResponse } from '@/api/envelope'

export interface AppCredential {
  app: AppName
  issued: boolean
  enabled: boolean
  created_at: string | null
  last_used_at: string | null
}

export const appsApi = {
  list(): Promise<AppCredential[]> {
    return client.get<ListResponse<AppCredential>>('/v1/apps').then((r) => r.data.data)
  },
  /** The secret is returned once, here, and can never be read back. */
  issue(app: AppName): Promise<{ app: AppName; secret: string }> {
    return client
      .post<DataResponse<{ app: AppName; secret: string }>>(`/v1/apps/${app}/secret`)
      .then((r) => r.data.data)
  },
  revoke(app: AppName): Promise<void> {
    return client.delete(`/v1/apps/${app}`).then(() => undefined)
  },
}
