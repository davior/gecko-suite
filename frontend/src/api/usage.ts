import client from '@/api/client'
import type { DataResponse } from '@/api/envelope'

export interface UsageGroup {
  key: Record<string, string | null>
  events: number
  /** Events with a known price. Fewer than `events` means `cost` is a floor. */
  priced_events: number
  cost: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
}

export interface UsageReport {
  days: number
  scope: 'me' | 'all'
  currency: string
  totals: UsageGroup
  by_model: UsageGroup[]
  by_app: UsageGroup[]
  by_task: UsageGroup[]
  by_day: UsageGroup[]
}

export const usageApi = {
  report(days: number, scope: 'me' | 'all' = 'me'): Promise<UsageReport> {
    return client
      .get<DataResponse<UsageReport>>('/v1/usage', { params: { days, scope } })
      .then((r) => r.data.data)
  },
}

/** "$4.20", or "at least $4.20" when some events have no known price. */
export function describeCost(group: UsageGroup, currency = 'USD'): string {
  const symbol = currency === 'USD' ? '$' : `${currency} `
  const amount = `${symbol}${group.cost.toFixed(group.cost < 1 ? 4 : 2)}`
  if (group.events === 0) return `${symbol}0`
  if (group.priced_events === 0) return 'Unknown'
  return group.priced_events < group.events ? `at least ${amount}` : amount
}
