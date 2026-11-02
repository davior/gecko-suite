import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import RequireAuth from '@/components/RequireAuth'
import { useAuthStore } from '@/stores/auth'

/**
 * The auth gate is the one piece of UI that decides whether anything is shown at all,
 * so it is worth testing the states directly. gecko-notes has no component tests, which
 * is why its equivalent logic is spread across views and has drifted.
 */

afterEach(() => {
  act(() => {
    useAuthStore.getState().reset()
  })
})

function renderGate(state: Partial<ReturnType<typeof useAuthStore.getState>>) {
  act(() => {
    useAuthStore.setState(state)
  })
  return render(
    <RequireAuth>
      <p>protected content</p>
    </RequireAuth>
  )
}

describe('RequireAuth', () => {
  it('does not offer a sign-in button when the session was rejected', () => {
    // The state that cost an afternoon in production: signed in to Notes, refused here
    // because the secrets disagree. The old UI showed "Sign in with Gecko Notes", which
    // sends the user around a loop that cannot terminate.
    renderGate({
      status: 'rejected',
      error: 'Its JWT_SECRET_KEY probably does not match Notes\u2019.',
    })

    expect(screen.getByText(/signed in, but not accepted/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('names the cause so an operator knows where to look', () => {
    renderGate({ status: 'rejected', error: null })

    expect(screen.getByText(/JWT_SECRET_KEY/)).toBeInTheDocument()
  })

  it('shows a loading state while the session is being checked', () => {
    renderGate({ status: 'loading' })
    expect(screen.getByText(/checking your session/i)).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders children once authenticated', () => {
    renderGate({
      status: 'authenticated',
      user: { id: 'u1', username: 'davior', is_admin: false },
    })
    expect(screen.getByText('protected content')).toBeInTheDocument()
  })

  it('offers sign-in when anonymous', () => {
    renderGate({ status: 'anonymous', error: null })
    expect(
      screen.getByRole('button', { name: /sign in with gecko notes/i })
    ).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('reports a backend outage instead of offering a pointless sign-in', () => {
    // The distinction that matters: a user who is signed out and a server that is
    // down look identical to a naive gate, and bouncing to another domain would hide
    // the real problem.
    renderGate({ status: 'anonymous', error: 'Could not reach the asset library.' })
    expect(screen.getByText(/could not reach the asset library/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
  })
})
