import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * The CSP hash for index.html's inline theme script, kept honest.
 *
 * `script-src 'self'` blocks inline scripts, so the script that sets the dark class
 * before first paint needs its sha256 listed in nginx.conf. That was missing, and the
 * only symptom was a console warning nobody reads plus a white flash on every
 * dark-mode load in production — the exact thing the script exists to prevent.
 *
 * A pinned hash trades one silent failure for another: edit the script by a byte and
 * it goes back to being blocked, again silently. This is the guard against that. It
 * lives at the frontend root rather than in `src/` because neither file it reads is
 * source.
 */

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

/** The exact bytes a browser hashes: the element's text content, nothing else. */
function inlineScriptBody(html: string): string {
  const match = html.match(/<script>([\s\S]*?)<\/script>/)
  if (!match) throw new Error('no inline <script> found in index.html')
  return match[1]
}

const sha256 = (body: string) =>
  `sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}`

/**
 * The policy string itself, not the file.
 *
 * Scoped deliberately: the surrounding comments discuss `script-src` and `unsafe-inline`
 * by name, so a naive search of the whole file matches the prose explaining the rule
 * and reports the opposite of the truth. The first draft of this file did exactly that.
 */
function policy(): string {
  const match = read('./nginx.conf').match(
    /add_header\s+Content-Security-Policy\s+"([^"]*)"/
  )
  if (!match) throw new Error('no Content-Security-Policy header found in nginx.conf')
  return match[1]
}

const directive = (name: string) =>
  policy()
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name} `)) ?? ''

describe('Content-Security-Policy', () => {
  it('lists the current hash of the inline theme script', () => {
    const expected = sha256(inlineScriptBody(read('./index.html')))

    expect(directive('script-src')).toContain(`'${expected}'`)
  })

  it('does not reach for unsafe-inline instead', () => {
    // The lazy fix for the above, and it would permit every injected script on the page.
    expect(directive('script-src')).not.toContain('unsafe-inline')
    expect(policy()).not.toContain('unsafe-eval')
  })

  it('keeps the Notes origin templated rather than hardcoded', () => {
    // It has to follow NOTES_BASE_URL, or a deployment pointing somewhere else gets a
    // policy nobody edited silently blocking its auth fetch.
    expect(directive('connect-src')).toContain('${NOTES_BASE_URL}')
    expect(directive('connect-src')).not.toContain('geckopico.com')
  })
})
