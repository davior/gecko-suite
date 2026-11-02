import { describe, expect, it } from 'vitest'
import { describeCost, type UsageGroup } from '@/api/usage'

const group = (overrides: Partial<UsageGroup>): UsageGroup => ({
  key: {},
  events: 2,
  priced_events: 2,
  cost: 4.2,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  ...overrides,
})

describe('describeCost', () => {
  it('is a plain sum when every call is priced', () => {
    expect(describeCost(group({}))).toBe('$4.20')
  })

  it('is a floor when some calls have no known price', () => {
    expect(describeCost(group({ priced_events: 1 }))).toBe('at least $4.20')
  })

  it('says unknown rather than $0 when nothing is priced', () => {
    expect(describeCost(group({ priced_events: 0, cost: 0 }))).toBe('Unknown')
  })

  it('keeps small amounts readable', () => {
    expect(describeCost(group({ cost: 0.0144 }))).toBe('$0.0144')
  })
})
