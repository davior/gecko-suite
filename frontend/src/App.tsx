import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import AppsView from '@/views/AppsView'
import LibraryView from '@/views/LibraryView'
import ProvidersView from '@/views/ProvidersView'
import RoutingView from '@/views/RoutingView'
import SearchView from '@/views/SearchView'
import UsageView from '@/views/UsageView'
import { loadConfig } from '@/api/config'
import { setUnauthorizedHandler } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)

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
      auth.signOut()
      return true
    })
  }, [])

  const page = (view: JSX.Element) => (
    <RequireAuth>
      <AppShell>{view}</AppShell>
    </RequireAuth>
  )

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/providers" replace />} />
      <Route path="/providers" element={page(<ProvidersView />)} />
      <Route path="/routing" element={page(<RoutingView />)} />
      <Route path="/library" element={page(<LibraryView />)} />
      <Route path="/search" element={page(<SearchView />)} />
      <Route path="/usage" element={page(<UsageView />)} />
      <Route path="/apps" element={page(<AppsView />)} />
      <Route path="*" element={<Navigate to="/providers" replace />} />
    </Routes>
  )
}
