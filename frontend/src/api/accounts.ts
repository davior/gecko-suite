import client from '@/api/client'
import type { DataResponse } from '@/api/envelope'

/**
 * Accounts kept by the suite (M6): sign-in, registration, recovery, the signed-in
 * account and its two-factor settings. `/api/v1/auth` is Notes' own auth API, ported, so
 * the shapes and sentences are Notes'.
 *
 * A session from before the suite signed people in may have no suite account behind it:
 * `me()` then answers 404 `no_account`, which the account page reads as "managed by
 * Notes", not as a failure.
 */

export interface Account {
  id: string
  username: string
  email: string | null
  is_active: boolean
  is_admin: boolean
  email_verified: boolean
  two_factor_method: TwoFactorMethod | null
  created_at: string | null
  last_login: string | null
}

export type TwoFactorMethod = 'totp' | 'email'

export interface SignedIn {
  access_token: string
  token_type: string
  user: Account
}

/** The first step's answer when the account has a second factor. */
export interface SecondStepNeeded {
  two_factor_required: true
  method: TwoFactorMethod
  challenge_token: string
}

export type LoginResult = SignedIn | SecondStepNeeded

export function needsSecondStep(result: LoginResult): result is SecondStepNeeded {
  return 'two_factor_required' in result && result.two_factor_required === true
}

export interface TwoFactorStatus {
  enabled: boolean
  method: TwoFactorMethod | null
  /** Whether the suite can send mail, which email codes need. */
  email_available: boolean
}

export interface TotpSetup {
  secret: string
  otpauth_uri: string
  qr_data_uri: string
}

interface Message {
  message: string
}

const data = <T>(r: { data: DataResponse<T> }): T => r.data.data
const message = (r: { data: DataResponse<Message> }): string => r.data.data.message

export const accountsApi = {
  register(body: {
    username: string
    email: string
    password: string
  }): Promise<Account> {
    return client.post<DataResponse<Account>>('/v1/auth/register', body).then(data)
  },
  login(body: { username: string; password: string }): Promise<LoginResult> {
    return client.post<DataResponse<LoginResult>>('/v1/auth/login', body).then(data)
  },
  secondStep(body: { challenge_token: string; code: string }): Promise<SignedIn> {
    return client.post<DataResponse<SignedIn>>('/v1/auth/login/2fa', body).then(data)
  },
  /** Clears the shared cookie, which signs the browser out of every app at once. */
  logout(): Promise<void> {
    return client.post('/v1/auth/logout').then(() => undefined)
  },
  verifyEmail(token: string): Promise<string> {
    return client
      .post<DataResponse<Message>>('/v1/auth/verify-email', { token })
      .then(message)
  },
  resendVerification(email: string): Promise<string> {
    return client
      .post<DataResponse<Message>>('/v1/auth/resend-verification', { email })
      .then(message)
  },
  forgotPassword(email: string): Promise<string> {
    return client
      .post<DataResponse<Message>>('/v1/auth/forgot-password', { email })
      .then(message)
  },
  resetPassword(token: string, newPassword: string): Promise<string> {
    return client
      .post<DataResponse<Message>>('/v1/auth/reset-password', {
        token,
        new_password: newPassword,
      })
      .then(message)
  },

  me(): Promise<Account> {
    return client.get<DataResponse<Account>>('/v1/auth/me').then(data)
  },
  updateMe(body: { username?: string; email?: string }): Promise<Account> {
    return client.patch<DataResponse<Account>>('/v1/auth/me', body).then(data)
  },
  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return client
      .post('/v1/auth/me/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      .then(() => undefined)
  },

  twoFactorStatus(): Promise<TwoFactorStatus> {
    return client.get<DataResponse<TwoFactorStatus>>('/v1/auth/2fa/status').then(data)
  },
  /** A fresh secret and QR. Nothing is saved until `enableTotp` confirms a code from it. */
  setupTotp(): Promise<TotpSetup> {
    return client.post<DataResponse<TotpSetup>>('/v1/auth/2fa/totp/setup').then(data)
  },
  enableTotp(secret: string, code: string): Promise<TwoFactorStatus> {
    return client
      .post<DataResponse<TwoFactorStatus>>('/v1/auth/2fa/totp/enable', { secret, code })
      .then(data)
  },
  /** Mails a code; `verifyEmailCode` with it turns email codes on. */
  setupEmailCodes(): Promise<string> {
    return client.post<DataResponse<Message>>('/v1/auth/2fa/email/setup').then(message)
  },
  verifyEmailCode(code: string): Promise<TwoFactorStatus> {
    return client
      .post<DataResponse<TwoFactorStatus>>('/v1/auth/2fa/email/verify', { code })
      .then(data)
  },
  disableTwoFactor(password: string): Promise<TwoFactorStatus> {
    return client
      .post<DataResponse<TwoFactorStatus>>('/v1/auth/2fa/disable', { password })
      .then(data)
  },
}

/**
 * Where to go after signing in, or null to stay in the suite.
 *
 * `?redirect=` is how Notes, GAM and GVC ask to be sent back. Honoured only for this
 * origin and the configured `LOGIN_REDIRECT_ORIGINS`, compared as whole origins: a
 * prefix test would let `https://notes.geckopico.com.evil.example` through, and an
 * unchecked one makes the sign-in page an open redirect that phishing can borrow.
 */
export function safeRedirect(
  target: string | null,
  allowedOrigins: string[],
  here: string = window.location.origin
): string | null {
  if (!target) return null
  let url: URL
  try {
    url = new URL(target, here)
  } catch {
    return null
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
  const allowed = new Set([here, ...allowedOrigins.map((o) => o.replace(/\/+$/, ''))])
  return allowed.has(url.origin) ? url.href : null
}
