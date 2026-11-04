import type { AxiosInstance } from 'axios'
import client from '@/api/client'
import type { DataResponse, ListResponse } from '@/api/envelope'

/**
 * Themes and which one each app wears (M7).
 *
 * A factory over an axios instance because this file is copied into GAM and GVC, which
 * call the suite cross-origin with their own instance (`withCredentials`, the suite's
 * address); here it is the suite's own client.
 */

export type ThemeApp = 'notes' | 'gam' | 'gvc' | 'suite'

export interface ThemeValues {
  name: string
  mode: 'light' | 'dark'
  bg_type: 'flat' | 'gradient' | 'image'
  bg_color1: string
  bg_color2: string | null
  bg_image_url: string | null
  bg_image_mode: 'fill' | 'stretch' | 'repeat'
  bg_blur: number
  glass_opacity: number
  glass_blur: number
  shadow_size: number
  shadow_blur: number
}

export interface Theme extends ThemeValues {
  id: string
  scope: 'instance' | 'user'
  user_id: string | null
  /** Whether the caller may change it: their own, or a shared one for an admin. */
  editable: boolean
  created_at: string
  updated_at: string
}

export interface Selection {
  app: ThemeApp
  theme: Theme | null
}

export function makeThemesApi(http: AxiosInstance) {
  return {
    list(): Promise<Theme[]> {
      return http.get<ListResponse<Theme>>('/v1/themes').then((r) => r.data.data)
    },
    create(body: ThemeValues & { scope: 'instance' | 'user' }): Promise<Theme> {
      return http.post<DataResponse<Theme>>('/v1/themes', body).then((r) => r.data.data)
    },
    update(id: string, body: Partial<ThemeValues>): Promise<Theme> {
      return http
        .patch<DataResponse<Theme>>(`/v1/themes/${id}`, body)
        .then((r) => r.data.data)
    },
    remove(id: string): Promise<void> {
      return http.delete(`/v1/themes/${id}`).then(() => undefined)
    },
    /** The image itself as the body; answers with the absolute address to use. */
    uploadImage(file: File): Promise<string> {
      return http
        .post<DataResponse<{ url: string }>>('/v1/themes/images', file, {
          headers: { 'Content-Type': file.type },
        })
        .then((r) => r.data.data.url)
    },
    selected(app: ThemeApp): Promise<Theme | null> {
      return http
        .get<DataResponse<Selection>>(`/v1/themes/selected/${app}`)
        .then((r) => r.data.data.theme)
    },
    choose(app: ThemeApp, themeId: string | null): Promise<Theme | null> {
      return http
        .put<DataResponse<Selection>>(`/v1/themes/selected/${app}`, { theme_id: themeId })
        .then((r) => r.data.data.theme)
    },
  }
}

export const themesApi = makeThemesApi(client)
