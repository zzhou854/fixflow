import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { LoginPage } from './pages/LoginPage'
import { OperatorPage } from './pages/OperatorPage'
import { ResidentPage } from './pages/ResidentPage'

export function App() {
  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#0f766e', borderRadius: 10 } }}>
    <BrowserRouter><AuthProvider><Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/resident" element={<ProtectedRoute role="RESIDENT"><ResidentPage /></ProtectedRoute>} />
      <Route path="/operator" element={<ProtectedRoute role="OPERATOR"><OperatorPage /></ProtectedRoute>} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes></AuthProvider></BrowserRouter>
  </ConfigProvider>
}
