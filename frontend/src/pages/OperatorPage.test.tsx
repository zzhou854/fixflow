import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { OperatorPage } from './OperatorPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'token', user: { user_id: 'o', username: 'operator_demo', actor_type: 'OPERATOR' }, logout: vi.fn() }) }))
vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, operatorTickets: vi.fn(), operatorTicket: vi.fn(), operatorAvailableSlots: vi.fn(), operatorBookAppointment: vi.fn(), operatorEscalate: vi.fn(), recordRepairProgress: vi.fn(), reconciliationCase: vi.fn(), humanReviewCases: vi.fn().mockResolvedValue({ items: [], limit: 100, offset: 0 }), transitionHumanReview: vi.fn(), humanReviewEvents: vi.fn() } }
})

test('shows business work first and keeps thread inspection under processing rationale', async () => {
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [{ ticket_id: 'ticket-1', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'ELECTRICAL', issue_location: '客厅', severity: 'HIGH', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-07-21T00:00:00Z' }] })
  render(<OperatorPage />)
  expect(await screen.findByText('ticket-1')).toBeInTheDocument()
  expect(screen.getByText('电气报修')).toBeInTheDocument()
  expect(screen.queryByLabelText('关联会话编号')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '处理依据' }))
  expect(screen.getByLabelText('关联会话编号')).toBeInTheDocument()
  expect(screen.queryByText('Workflow Stage')).not.toBeInTheDocument()
})

test('keeps an uncertain escalation disabled until NOT_COMMITTED permits the same-key retry', async () => {
  const ticket = { ticket_id: 'ticket-2', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-07-21T00:00:00Z' }
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [ticket] })
  vi.mocked(api.operatorTicket).mockResolvedValue({ ticket, issue_description: '漏水', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
  vi.mocked(api.operatorEscalate).mockResolvedValue({ ok: false, code: 'RECONCILIATION_PENDING', resource_type: 'repair_ticket', resource_id: 'ticket-2', resource_version: null, replayed: false, reconciliation: { case_id: 'case-2', action: 'ESCALATE_TO_OPERATOR', status: 'PENDING', retry_allowed: false, ticket_id: 'ticket-2' } })
  vi.mocked(api.reconciliationCase).mockResolvedValue({ case_id: 'case-2', operation_type: 'ESCALATE_TO_OPERATOR', status: 'RESOLVED_NOT_COMMITTED', target_entity_id: 'ticket-2', attempt_count: 1, evidence_status: 'NOT_COMMITTED', resolution_code: 'NO_DURABLE_EVIDENCE', retry_allowed: true, created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z' })
  render(<OperatorPage />)
  await userEvent.click(await screen.findByText('ticket-2'))
  const escalate = await screen.findByRole('button', { name: '转交主管' })
  await userEvent.click(escalate)
  expect(await screen.findByText('对账状态：PENDING')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '转交主管' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '刷新对账状态' }))
  const retry = await screen.findByRole('button', { name: '重新转交主管' })
  expect(retry).toBeEnabled()
  const originalKey = vi.mocked(api.operatorEscalate).mock.calls[0][3]
  await userEvent.click(retry)
  expect(vi.mocked(api.operatorEscalate).mock.calls[1][3]).toBe(originalKey)
})

test('lets an operator arrange an available visit for a waiting ticket', async () => {
  const ticket = { ticket_id: 'ticket-4', resident_id: 'r', resident_username: 'resident_test', property_id: 'p', property_label: '星河花园 1 栋 1 单元 101', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'OPEN', rework_count: 0, version: 1, appointment: null, updated_at: '2026-09-10T00:00:00Z' }
  const slot = { worker_id: 'worker-1', worker_name: '水暖维修员', scheduled_start: '2099-09-12T06:00:00Z', scheduled_end: '2099-09-12T07:00:00Z', rank: 1 }
  const scheduled = { ...ticket, ticket_status: 'SCHEDULED', version: 2, appointment: { appointment_id: 'appointment-1', worker_id: 'worker-1', purpose: 'INITIAL_REPAIR', status: 'BOOKED', scheduled_start: slot.scheduled_start, scheduled_end: slot.scheduled_end, appointment_version: 1 } }
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [ticket] })
  vi.mocked(api.operatorTicket)
    .mockResolvedValueOnce({ ticket, issue_description: '厨房漏水', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
    .mockResolvedValueOnce({ ticket: scheduled, issue_description: '厨房漏水', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
  vi.mocked(api.operatorAvailableSlots).mockResolvedValue({ items: [slot] })
  vi.mocked(api.operatorBookAppointment).mockResolvedValue({ ok: true, code: 'APPOINTMENT_BOOKED', resource_type: 'appointment', resource_id: 'appointment-1', resource_version: 1, replayed: false, reconciliation: null })

  render(<OperatorPage />)
  await userEvent.click(await screen.findByText('ticket-4'))
  await userEvent.click(await screen.findByRole('button', { name: '查看可用时间' }))
  expect(await screen.findByText('维修人员：水暖维修员')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '安排这个时间' }))
  expect(api.operatorBookAppointment).toHaveBeenCalledWith('token', 'ticket-4', slot, 1)
})

test('does not offer supervisor transfer for a completed ticket', async () => {
  const ticket = { ticket_id: 'ticket-5', resident_id: 'r', resident_username: 'resident_test', property_id: 'p', property_label: '星河花园 1 栋 1 单元 101', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'CLOSED', rework_count: 0, version: 5, appointment: null, updated_at: '2026-09-10T00:00:00Z' }
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [ticket] })
  vi.mocked(api.operatorTicket).mockResolvedValue({ ticket, issue_description: '已处理', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
  render(<OperatorPage />)
  await userEvent.click(await screen.findByText('ticket-5'))
  expect(await screen.findByText('已处理')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '转交主管' })).not.toBeInTheDocument()
})

test('starts an on-site repair without requiring travel-status clicks', async () => {
  const appointment = { appointment_id: 'appointment-1', worker_id: 'worker-1', purpose: 'INITIAL_REPAIR', status: 'BOOKED', scheduled_start: '2026-09-09T05:00:00Z', scheduled_end: '2026-09-09T06:00:00Z', appointment_version: 1 }
  const ticket = { ticket_id: 'ticket-3', resident_id: 'r', resident_username: 'resident_demo', property_id: 'p', property_label: '星河花园 1201', issue_category: 'ELECTRICAL', issue_location: '书房', severity: 'MEDIUM', ticket_status: 'SCHEDULED', rework_count: 0, version: 2, appointment, updated_at: '2026-09-09T00:00:00Z' }
  vi.mocked(api.operatorTickets).mockResolvedValue({ items: [ticket] })
  vi.mocked(api.operatorTicket).mockResolvedValue({ ticket, issue_description: '开关失灵', escalated_from_status: null, ticket_history: [], appointment_history: [], latest_worker_event: null })
  vi.mocked(api.recordRepairProgress).mockResolvedValue({ ok: true, code: 'WORKER_EVENT_RECORDED', resource_type: 'worker_event', resource_id: 'event-1', resource_version: 1, replayed: false, reconciliation: null })

  render(<OperatorPage />)
  await userEvent.click(await screen.findByText('ticket-3'))
  await userEvent.click(await screen.findByRole('button', { name: '开始维修' }))

  expect(api.recordRepairProgress).toHaveBeenCalledWith('token', 'ticket-3', expect.objectContaining({
    appointment_id: 'appointment-1',
    event_type: 'STARTED',
    expected_ticket_version: 2,
    expected_appointment_version: 1,
  }))
})
