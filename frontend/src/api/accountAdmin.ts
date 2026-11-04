import client from '@/api/client'
import type { Account } from '@/api/accounts'
import type { DataResponse, ListResponse } from '@/api/envelope'

/**
 * Account administration (M6), for administrators only. Deleting removes the account,
 * nothing else: each app keeps its own content and decides what to do with it.
 */

export interface SignUpSettings {
  registration_enabled: boolean
  email_verification_required: boolean
}

export const accountAdminApi = {
  list(
    params: { q?: string; limit?: number; offset?: number } = {}
  ): Promise<ListResponse<Account>> {
    return client
      .get<ListResponse<Account>>('/v1/admin/users', { params })
      .then((r) => r.data)
  },
  update(
    id: string,
    body: { is_active?: boolean; is_admin?: boolean }
  ): Promise<Account> {
    return client
      .patch<DataResponse<Account>>(`/v1/admin/users/${id}`, body)
      .then((r) => r.data.data)
  },
  setPassword(id: string, newPassword: string): Promise<void> {
    return client
      .post(`/v1/admin/users/${id}/reset-password`, { new_password: newPassword })
      .then(() => undefined)
  },
  remove(id: string): Promise<void> {
    return client.delete(`/v1/admin/users/${id}`).then(() => undefined)
  },
  settings(): Promise<SignUpSettings> {
    return client
      .get<DataResponse<SignUpSettings>>('/v1/admin/settings')
      .then((r) => r.data.data)
  },
  updateSettings(body: Partial<SignUpSettings>): Promise<SignUpSettings> {
    return client
      .put<DataResponse<SignUpSettings>>('/v1/admin/settings', body)
      .then((r) => r.data.data)
  },
}
