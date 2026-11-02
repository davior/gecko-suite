/** The suite's response envelope, shared by every api module. */
export interface DataResponse<T> {
  data: T
}

export interface ListResponse<T> {
  data: T[]
  total: number
  limit: number
  offset: number
}

export type Scope = 'instance' | 'user'
export type AppName = 'notes' | 'gam' | 'gvc'

export const APPS: AppName[] = ['notes', 'gam', 'gvc']

export const APP_LABELS: Record<AppName, string> = {
  notes: 'Notes',
  gam: 'GAM',
  gvc: 'Video Creator',
}
