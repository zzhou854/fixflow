import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { OperatorPage } from './OperatorPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'token', user: { user_id: 'o', username: 'operator_demo', actor_type: 'OPERATOR' }, logout: vi.fn() }) }))
vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, operatorTickets: vi.fn(), operatorTicket: vi.fn(), operatorEscalate: vi.fn(), reconciliationCase: vi.fn() } }
})

test('renders ticket data and the explicit known-thread review control', async () => {
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [{ ticket_id: 'ticket-1', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'ELECTRICAL', issue_location: '客厅', severity: 'HIGH', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-07-21T00:00:00Z' }] })
  render(<OperatorPage />)
  expect(await screen.findByText('ticket-1')).toBeInTheDocument()
  expect(screen.getByText('ELECTRICAL')).toBeInTheDocument()
  expect(screen.getByLabelText('Agent Thread ID')).toBeInTheDocument()
})

test('keeps an uncertain escalation disabled until NOT_COMMITTED permits the same-key retry', async () => {
  const ticket = { ticket_id: 'ticket-2', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-07-21T00:00:00Z' }
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [ticket] })
  vi.mocked(api.operatorTicket).mockResolvedValue({ ticket, issue_description: '漏水', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
  vi.mocked(api.operatorEscalate).mockResolvedValue({ ok: false, code: 'RECONCILIATION_PENDING', resource_type: 'repair_ticket', resource_id: 'ticket-2', resource_version: null, replayed: false, reconciliation: { case_id: 'case-2', action: 'ESCALATE_TO_OPERATOR', status: 'PENDING', retry_allowed: false, ticket_id: 'ticket-2' } })
  vi.mocked(api.reconciliationCase).mockResolvedValue({ case_id: 'case-2', operation_type: 'ESCALATE_TO_OPERATOR', status: 'RESOLVED_NOT_COMMITTED', target_entity_id: 'ticket-2', attempt_count: 1, evidence_status: 'NOT_COMMITTED', resolution_code: 'NO_DURABLE_EVIDENCE', retry_allowed: true, created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z' })
  render(<OperatorPage />)
  await userEvent.click(await screen.findByText('ticket-2'))
  const escalate = await screen.findByRole('button', { name: '人工升级' })
  await userEvent.click(escalate)
  expect(await screen.findByText('对账状态：PENDING')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '人工升级' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '刷新对账状态' }))
  const retry = await screen.findByRole('button', { name: '使用原请求重试' })
  expect(retry).toBeEnabled()
  const originalKey = vi.mocked(api.operatorEscalate).mock.calls[0][3]
  await userEvent.click(retry)
  expect(vi.mocked(api.operatorEscalate).mock.calls[1][3]).toBe(originalKey)
})
