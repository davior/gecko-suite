import { describe, expect, it } from 'vitest'
import { describePrice } from '@/api/models'

describe('describePrice', () => {
  it('keeps whole dollars whole and shows cents properly', () => {
    expect(describePrice({ input: 4, output: 20 })).toBe('$4 / $20 per M tokens')
    expect(describePrice({ input: 0.1, output: 0.5 })).toBe('$0.10 / $0.50 per M tokens')
  })

  it('says a local model is free and an unknown price is unknown, not free', () => {
    expect(describePrice({ input: 0, output: 0 }, 'ollama')).toBe('Free (local)')
    expect(describePrice(null)).toBe('Price unknown')
  })
})
