import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { TicketSummary } from './TicketSummary'

test('renders authoritative ticket and appointment summary', () => {
  const onAccept = vi.fn()
  render(<TicketSummary onAccept={onAccept} ticket={{ ticket_id: 'ticket-1', resident_id: 'resident', resident_username: 'resident_demo', property_id: 'property', property_label: '星河花园', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'SCHEDULED', rework_count: 0, version: 2, updated_at: '2026-07-21T00:00:00Z', appointment: { appointment_id: 'appointment', worker_id: 'worker', purpose: 'INITIAL_REPAIR', status: 'BOOKED', scheduled_start: '2026-07-22T06:00:00Z', scheduled_end: '2026-07-22T07:00:00Z', appointment_version: 1 } }} />)
  expect(screen.getByText('漏水报修')).toBeInTheDocument()
  expect(screen.getByText('已安排上门')).toBeInTheDocument()
  expect(screen.getByText('预约上门')).toBeInTheDocument()
  expect(screen.getByText('2026年7月22日 周三')).toBeInTheDocument()
  expect(screen.getByText('14:00–15:00')).toBeInTheDocument()
  expect(screen.getByText('维修完成后请确认')).toBeInTheDocument()
  expect(screen.queryByText(/师傅现场/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认已修好' })).toBeInTheDocument()
  expect(screen.queryByText('WATER_LEAK')).not.toBeInTheDocument()
})

test('offers only confirmation when a repair awaits resident acceptance', async () => {
  const onAccept = vi.fn()
  render(<TicketSummary onAccept={onAccept} ticket={{ ticket_id: 'ticket-2', resident_id: 'resident', resident_username: 'resident_demo', property_id: 'property', property_label: '星河花园', issue_category: 'ELECTRICAL', issue_location: '书房', severity: 'MEDIUM', ticket_status: 'PENDING_ACCEPTANCE', rework_count: 0, version: 4, updated_at: '2026-09-09T00:00:00Z', appointment: null }} />)

  await userEvent.click(screen.getByRole('button', { name: '确认已修好' }))
  expect(onAccept).toHaveBeenCalledOnce()
  expect(screen.queryByText(/继续现场维修/)).not.toBeInTheDocument()
})

test('does not allow confirming a scheduled repair before the visit starts', () => {
  render(<TicketSummary onAccept={vi.fn()} ticket={{ ticket_id: 'ticket-future', resident_id: 'resident', resident_username: 'resident_demo', property_id: 'property', property_label: '星河花园', issue_category: 'DOOR_LOCK', issue_location: '入户门', severity: 'MEDIUM', ticket_status: 'SCHEDULED', rework_count: 0, version: 2, updated_at: '2099-07-21T00:00:00Z', appointment: { appointment_id: 'appointment-future', worker_id: 'worker', purpose: 'INITIAL_REPAIR', status: 'BOOKED', scheduled_start: '2099-07-22T06:00:00Z', scheduled_end: '2099-07-22T07:00:00Z', appointment_version: 1 } }} />)

  expect(screen.getByText('预约上门')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认已修好' })).not.toBeInTheDocument()
})
