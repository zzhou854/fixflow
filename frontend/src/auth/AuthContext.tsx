/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { User } from '../types'

interface AuthValue {
  token: string | null; user: User | null; restoring: boolean; login: (username: string, password: string) => Promise<User>; logout: () => void
}

const STORAGE_KEY = 'fixflow.demo.session'
const AuthContext = createContext<AuthValue | null>(null)

function loadToken(): string | null {
  const raw = sessionStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { access_token?: unknown }
    return typeof parsed.access_token === 'string' ? parsed.access_token : null
  } catch { sessionStorage.removeItem(STORAGE_KEY); return null }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => loadToken())
  const [user, setUser] = useState<User | null>(null)
  const [restoring, setRestoring] = useState(() => token !== null)
  useEffect(() => {
    if (!token) { setRestoring(false); return }
    let active = true
    void api.me(token).then((verified) => {
      if (active) setUser(verified)
    }).catch(() => {
      if (active) { sessionStorage.removeItem(STORAGE_KEY); setToken(null); setUser(null) }
    }).finally(() => { if (active) setRestoring(false) })
    return () => { active = false }
  }, [token])
  const value = useMemo<AuthValue>(() => ({
    token,
    user,
    restoring,
    login: async (username, password) => {
      const next = await api.login(username, password)
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ access_token: next.access_token }))
      setToken(next.access_token); setUser(next.user); setRestoring(false); return next.user
    },
    logout: () => { sessionStorage.removeItem(STORAGE_KEY); setToken(null); setUser(null) },
  }), [restoring, token, user])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('AuthProvider is required')
  return value
}
