import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { api } from '../api/client'
import { LoginPage } from './LoginPage'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, login: vi.fn() } }
})

test('renders preset login form without storing a password itself', () => {
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  expect(screen.getByText('FixFlow')).toBeInTheDocument()
  expect(screen.getByLabelText('账号')).toBeInTheDocument()
  expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password')
})

test('shows a safe login error', async () => {
  vi.mocked(api.login).mockRejectedValue(new Error('offline'))
  render(<MemoryRouter><AuthProvider><LoginPage /></AuthProvider></MemoryRouter>)
  await userEvent.type(screen.getByLabelText('密码'), 'test-password')
  await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
  expect(await screen.findByText('登录失败，请稍后重试。')).toBeInTheDocument()
})
