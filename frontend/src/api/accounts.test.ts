import { describe, expect, it } from 'vitest'
import { safeRedirect } from '@/api/accounts'

const HERE = 'https://suite.example.test'
const ALLOWED = ['https://notes.example.test', 'https://assets.example.test/']

describe('safeRedirect', () => {
  it('honours an allowed app, path and query intact', () => {
    expect(safeRedirect('https://notes.example.test/n/42?x=1', ALLOWED, HERE)).toBe(
      'https://notes.example.test/n/42?x=1'
    )
    // A trailing slash in the setting is not a different origin.
    expect(safeRedirect('https://assets.example.test/library', ALLOWED, HERE)).toBe(
      'https://assets.example.test/library'
    )
  })

  it('honours this suite, absolute or as a path', () => {
    expect(safeRedirect('/usage', ALLOWED, HERE)).toBe(`${HERE}/usage`)
    expect(safeRedirect(`${HERE}/apps`, ALLOWED, HERE)).toBe(`${HERE}/apps`)
  })

  it.each([
    ['another site', 'https://evil.example/'],
    [
      'a lookalike that only starts with an allowed origin',
      'https://notes.example.test.evil.example/',
    ],
    ['the right host on another port', 'https://notes.example.test:8443/'],
    ['the right host over plain http', 'http://notes.example.test/'],
    ['a protocol-relative address', '//evil.example/'],
    ['script', 'javascript:alert(1)'],
    ['data', 'data:text/html,hi'],
  ])('refuses %s', (_why, target) => {
    expect(safeRedirect(target, ALLOWED, HERE)).toBeNull()
  })

  it('is null when nothing was asked', () => {
    expect(safeRedirect(null, ALLOWED, HERE)).toBeNull()
    expect(safeRedirect('', ALLOWED, HERE)).toBeNull()
  })
})
