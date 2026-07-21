import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { api } from '../api/client'
import { ProtectedRoute } from './ProtectedRoute'
import { vi } from 'vitest'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, me: vi.fn() } }
})

test('redirects an anonymous visitor to login', () => {
  sessionStorage.clear()
  render(<MemoryRouter initialEntries={['/resident']}><AuthProvider><Routes><Route path="/login" element={<span>登录页</span>} /><Route path="/resident" element={<ProtectedRoute role="RESIDENT"><span>住户页</span></ProtectedRoute>} /></Routes></AuthProvider></MemoryRouter>)
  expect(screen.getByText('登录页')).toBeInTheDocument()
})

test('restores a token through auth me and ignores a cached operator role', async () => {
  sessionStorage.setItem('fixflow.demo.session', JSON.stringify({ access_token: 'token', user: { actor_type: 'OPERATOR' } }))
  vi.mocked(api.me).mockResolvedValue({ user_id: 'id', username: 'resident', actor_type: 'RESIDENT' })
  render(<MemoryRouter initialEntries={['/operator']}><AuthProvider><Routes><Route path="/resident" element={<span>住户页</span>} /><Route path="/operator" element={<ProtectedRoute role="OPERATOR"><span>物业页</span></ProtectedRoute>} /></Routes></AuthProvider></MemoryRouter>)
  expect(await screen.findByText('住户页')).toBeInTheDocument()
})

test('clears an invalid restored token and returns to login', async () => {
  sessionStorage.setItem('fixflow.demo.session', JSON.stringify({ access_token: 'expired' }))
  vi.mocked(api.me).mockRejectedValue(new Error('expired'))
  render(<MemoryRouter initialEntries={['/resident']}><AuthProvider><Routes><Route path="/login" element={<span>登录页</span>} /><Route path="/resident" element={<ProtectedRoute role="RESIDENT"><span>住户页</span></ProtectedRoute>} /></Routes></AuthProvider></MemoryRouter>)
  expect(await screen.findByText('登录页')).toBeInTheDocument()
  expect(sessionStorage.getItem('fixflow.demo.session')).toBeNull()
})
