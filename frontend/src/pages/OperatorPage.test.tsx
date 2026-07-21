import { render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import { api } from '../api/client'
import { OperatorPage } from './OperatorPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'token', user: { user_id: 'o', username: 'operator_demo', actor_type: 'OPERATOR' }, logout: vi.fn() }) }))
vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, operatorTickets: vi.fn() } }
})

test('renders ticket data and the explicit known-thread review control', async () => {
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [{ ticket_id: 'ticket-1', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'ELECTRICAL', issue_location: '客厅', severity: 'HIGH', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-07-21T00:00:00Z' }] })
  render(<OperatorPage />)
  expect(await screen.findByText('ticket-1')).toBeInTheDocument()
  expect(screen.getByText('ELECTRICAL')).toBeInTheDocument()
  expect(screen.getByLabelText('Agent Thread ID')).toBeInTheDocument()
})
