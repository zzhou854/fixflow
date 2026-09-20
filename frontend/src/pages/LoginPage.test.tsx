import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { api } from '../api/client'
import { LoginPage } from './LoginPage'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, login: vi.fn(), registerResident: vi.fn() } }
})

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

test('uses the clean resident test account and offers password saving', () => {
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  expect(screen.getByText('FixFlow')).toBeInTheDocument()
  expect(screen.getByLabelText('账号')).toHaveValue('resident_test')
  expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password')
  expect(screen.getByRole('checkbox', { name: '记住密码' })).toBeChecked()
})

test('uses the dedicated operator test account for property login', async () => {
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  await userEvent.click(screen.getByText('物业登录'))
  expect(screen.getByLabelText('账号')).toHaveValue('operator_test')
  expect(screen.getByLabelText('密码')).toHaveValue('')
  expect(screen.getByRole('checkbox', { name: '记住密码' })).toBeChecked()
})

test('restores credentials saved by a successful login', async () => {
  vi.mocked(api.login).mockResolvedValue({
    access_token: 'token', token_type: 'bearer', expires_at: '2026-09-10T18:00:00+08:00',
    user: { user_id: 'resident', username: 'resident_test', actor_type: 'RESIDENT' },
  })
  const first = render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  await userEvent.type(screen.getByLabelText('密码'), 'ResidentTest!2026')
  await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
  await waitFor(() => expect(localStorage.getItem('fixflow.remembered.credentials.resident')).toContain('ResidentTest!2026'))
  first.unmount()

  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  expect(screen.getByLabelText('账号')).toHaveValue('resident_test')
  expect(screen.getByLabelText('密码')).toHaveValue('ResidentTest!2026')
})

test('shows a safe login error', async () => {
  vi.mocked(api.login).mockRejectedValue(new Error('offline'))
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  await userEvent.type(screen.getByLabelText('密码'), 'test-password')
  await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
  expect(await screen.findByText('登录失败，请稍后重试。')).toBeInTheDocument()
})

test('registers a resident and binds an existing property', async () => {
  vi.mocked(api.registerResident).mockResolvedValue({
    access_token: 'token', token_type: 'bearer', expires_at: '2026-09-09T18:00:00+08:00',
    user: { user_id: 'resident', username: 'new_resident', actor_type: 'RESIDENT' },
  })
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  await userEvent.click(screen.getByText('住户注册'))
  await userEvent.type(screen.getByLabelText('账号'), 'new_resident')
  await userEvent.type(screen.getByLabelText('密码'), 'new-password')
  await userEvent.click(screen.getByRole('button', { name: '注册并登录' }))
  expect(api.registerResident).toHaveBeenCalledWith(expect.objectContaining({
    username: 'new_resident', community_name: '星河花园', building_no: '3', unit_no: '2', room_no: '1201',
  }))
})
