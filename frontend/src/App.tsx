import { ConfigProvider, Spin } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })))
const OperatorPage = lazy(() => import('./pages/OperatorPage').then((module) => ({ default: module.OperatorPage })))
const ResidentPage = lazy(() => import('./pages/ResidentPage').then((module) => ({ default: module.ResidentPage })))

export function App() {
  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#0f766e', borderRadius: 10 } }}>
    <BrowserRouter><AuthProvider><Suspense fallback={<div className="route-loading"><Spin size="large" /></div>}><Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/resident" element={<ProtectedRoute role="RESIDENT"><ResidentPage /></ProtectedRoute>} />
      <Route path="/operator" element={<ProtectedRoute role="OPERATOR"><OperatorPage /></ProtectedRoute>} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes></Suspense></AuthProvider></BrowserRouter>
  </ConfigProvider>
}
