import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { accountsApi, type Account } from '@/api/accounts'
import { authApi } from '@/api/auth'
import client from '@/api/client'
import { resetConfig } from '@/api/config'
import { useAuthStore } from '@/stores/auth'
import LoginView from '@/views/LoginView'

const ACCOUNT: Account = {
  id: 'u1',
  username: 'davior',
  email: 'd@example.test',
  is_active: true,
  is_admin: true,
  email_verified: true,
  two_factor_method: null,
  created_at: null,
  last_login: null,
}

const signedIn = { access_token: 'tok', token_type: 'bearer', user: ACCOUNT }
let assign: ReturnType<typeof vi.fn>
const original = window.location

beforeEach(() => {
  resetConfig()
  vi.spyOn(client, 'get').mockResolvedValue({
    data: {
      data: {
        notes_base_url: window.location.origin,
        login_redirect_origins: ['https://notes.example.test'],
        email_enabled: true,
      },
    },
  } as never)
  vi.spyOn(authApi, 'me').mockResolvedValue({
    id: 'u1',
    username: 'davior',
    is_admin: true,
  })
  assign = vi.fn()
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...original, origin: original.origin, href: original.href, assign },
  })
  useAuthStore.setState({ status: 'anonymous', user: null })
})

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'location', { configurable: true, value: original })
  useAuthStore.getState().reset()
  resetConfig()
  vi.restoreAllMocks()
})

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/login" element={<LoginView />} />
        <Route path="/providers" element={<p>providers page</p>} />
        <Route path="/" element={<p>home page</p>} />
      </Routes>
    </MemoryRouter>
  )
}

async function signIn() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Username'), 'davior')
  await user.type(screen.getByLabelText('Password'), 'notes-password-123')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  return user
}

describe('LoginView', () => {
  it('signs in and goes back to the app that sent the visitor', async () => {
    vi.spyOn(accountsApi, 'login').mockResolvedValue(signedIn)
    renderAt('/login?redirect=' + encodeURIComponent('https://notes.example.test/n/1'))

    await signIn()

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://notes.example.test/n/1')
    )
    expect(useAuthStore.getState().status).toBe('authenticated')
  })

  it('never follows a redirect to an origin it was not told about', async () => {
    vi.spyOn(accountsApi, 'login').mockResolvedValue(signedIn)
    renderAt('/login?redirect=' + encodeURIComponent('https://evil.example/'))

    await signIn()

    expect(await screen.findByText('home page')).toBeInTheDocument()
    expect(assign).not.toHaveBeenCalled()
  })

  it('stays inside the suite for a redirect to one of its own pages', async () => {
    vi.spyOn(accountsApi, 'login').mockResolvedValue(signedIn)
    renderAt('/login?redirect=' + encodeURIComponent(`${original.origin}/providers`))

    await signIn()

    expect(await screen.findByText('providers page')).toBeInTheDocument()
    expect(assign).not.toHaveBeenCalled()
  })

  it('asks for the second step, and signs in only after it', async () => {
    vi.spyOn(accountsApi, 'login').mockResolvedValue({
      two_factor_required: true,
      method: 'totp',
      challenge_token: 'challenge',
    })
    const second = vi.spyOn(accountsApi, 'secondStep').mockResolvedValue(signedIn)
    renderAt('/login')

    const user = await signIn()
    // The challenge is not a session: nothing is signed in yet.
    expect(await screen.findByText(/authenticator app/i)).toBeInTheDocument()
    expect(useAuthStore.getState().status).toBe('anonymous')

    await user.type(screen.getByLabelText('Code'), '123 456')
    await user.click(screen.getByRole('button', { name: 'Verify' }))

    expect(second).toHaveBeenCalledWith({ challenge_token: 'challenge', code: '123456' })
    expect(await screen.findByText('home page')).toBeInTheDocument()
  })

  it('shows the server’s sentence when the password is wrong', async () => {
    vi.spyOn(accountsApi, 'login').mockRejectedValue(
      Object.assign(new Error('401'), {
        isAxiosError: true,
        response: {
          status: 401,
          data: {
            detail: { code: 'invalid_credentials', message: 'Invalid credentials' },
          },
        },
      })
    )
    renderAt('/login')

    await signIn()

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid credentials')
  })

  it('goes straight on when the visitor is already signed in', async () => {
    useAuthStore.setState({
      status: 'authenticated',
      user: { id: 'u1', username: 'davior', is_admin: true },
    })
    renderAt('/login?redirect=' + encodeURIComponent('https://notes.example.test/'))

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://notes.example.test/')
    )
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })
})
