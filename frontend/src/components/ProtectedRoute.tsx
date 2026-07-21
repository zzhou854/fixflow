import { Spin } from 'antd'
import { Navigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'
import type { ActorType } from '../types'

export function ProtectedRoute({ role, children }: { role: ActorType; children: ReactNode }) {
  const { user, restoring } = useAuth()
  if (restoring) return <Spin aria-label="正在恢复登录" />
  if (!user) return <Navigate to="/login" replace />
  if (user.actor_type !== role) return <Navigate to={user.actor_type === 'OPERATOR' ? '/operator' : '/resident'} replace />
  return children
}
