import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import AccountView from '@/views/AccountView'
import AppsView from '@/views/AppsView'
import LibraryView from '@/views/LibraryView'
import LoginView from '@/views/LoginView'
import ProvidersView from '@/views/ProvidersView'
import RoutingView from '@/views/RoutingView'
import SearchView from '@/views/SearchView'
import {
  ForgotPasswordView,
  RegisterView,
  ResetPasswordView,
  VerifyEmailView,
} from '@/views/RecoveryViews'
import UsageView from '@/views/UsageView'
import UsersView from '@/views/UsersView'
import { loadConfig } from '@/api/config'
import { setUnauthorizedHandler } from '@/api/client'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import ThemeBackdrop from '@/theme/ThemeBackdrop'
import { applyThemeToDom, cachedTheme } from '@/theme/apply'
import ThemesView from '@/views/ThemesView'

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const status = useAuthStore((s) => s.status)
  const loadTheme = useAppThemeStore((s) => s.load)

  // The last theme this browser wore, before anything else draws: no flash of plain
  // light while the suite is asked. Signing out clears it.
  useEffect(() => {
    applyThemeToDom(cachedTheme())
  }, [])

  useEffect(() => {
    if (status === 'authenticated') void loadTheme()
  }, [status, loadTheme])

  useEffect(() => {
    void bootstrap()
    // Early, so the sign-in address is in hand before anybody clicks.
    void loadConfig().catch(() => {
      // Not fatal: RequireAuth already reports an unreachable backend.
    })
  }, [bootstrap])

  // Here rather than in api/client.ts, which cannot import the auth store without a cycle.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      const auth = useAuthStore.getState()
      if (auth.status !== 'authenticated') return false
      void auth.signOut()
      return true
    })
  }, [])

  const page = (view: JSX.Element) => (
    <RequireAuth>
      <AppShell>{view}</AppShell>
    </RequireAuth>
  )

  return (
    <>
      <ThemeBackdrop />
      <Routes>
        {/* Signed-out pages. The paths are Notes' own, so the links in mail (built from
          APP_BASE_URL) and every app's `{suite}/login?redirect=` land on the right page. */}
        <Route path="/login" element={<LoginView />} />
        <Route path="/register" element={<RegisterView />} />
        <Route path="/forgot-password" element={<ForgotPasswordView />} />
        <Route path="/reset-password" element={<ResetPasswordView />} />
        <Route path="/verify-email" element={<VerifyEmailView />} />
        <Route path="/" element={<Navigate to="/providers" replace />} />
        <Route path="/providers" element={page(<ProvidersView />)} />
        <Route path="/routing" element={page(<RoutingView />)} />
        <Route path="/library" element={page(<LibraryView />)} />
        <Route path="/search" element={page(<SearchView />)} />
        <Route path="/usage" element={page(<UsageView />)} />
        <Route path="/apps" element={page(<AppsView />)} />
        <Route path="/account" element={page(<AccountView />)} />
        <Route path="/users" element={page(<UsersView />)} />
        <Route path="/themes" element={page(<ThemesView />)} />
        <Route path="*" element={<Navigate to="/providers" replace />} />
      </Routes>
    </>
  )
}
