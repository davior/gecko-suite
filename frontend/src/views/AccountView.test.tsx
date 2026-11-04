import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { accountsApi } from '@/api/accounts'
import AccountView from '@/views/AccountView'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const axiosError = (status: number, code: string, message: string) =>
  Object.assign(new Error(String(status)), {
    isAxiosError: true,
    response: { status, data: { detail: { code, message } } },
  })

describe('AccountView', () => {
  it('says where the account lives when the suite has none yet', async () => {
    // Before the cutover nearly everyone is in this state; it is not an error.
    vi.spyOn(accountsApi, 'me').mockRejectedValue(
      axiosError(
        404,
        'no_account',
        'There is no Gecko Suite account for this session yet.'
      )
    )
    render(<AccountView />)

    expect(await screen.findByText(/still kept by Gecko Notes/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('offers email codes only when the server can send mail', async () => {
    vi.spyOn(accountsApi, 'me').mockResolvedValue({
      id: 'u1',
      username: 'davior',
      email: 'd@example.test',
      is_active: true,
      is_admin: false,
      email_verified: true,
      two_factor_method: null,
      created_at: null,
      last_login: null,
    })
    vi.spyOn(accountsApi, 'twoFactorStatus').mockResolvedValue({
      enabled: false,
      method: null,
      email_available: false,
    })
    render(<AccountView />)

    expect(
      await screen.findByRole('button', { name: 'Use an authenticator app' })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Use email codes' })
    ).not.toBeInTheDocument()
  })
})
