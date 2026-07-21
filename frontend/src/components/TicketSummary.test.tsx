import { render, screen } from '@testing-library/react'
import { TicketSummary } from './TicketSummary'

test('renders authoritative ticket and appointment summary', () => {
  render(<TicketSummary ticket={{ ticket_id: 'ticket-1', resident_id: 'resident', resident_username: 'resident_demo', property_id: 'property', property_label: '星河花园', issue_category: 'WATER_LEAK', issue_location: '厨房', severity: 'MEDIUM', ticket_status: 'SCHEDULED', rework_count: 0, version: 2, updated_at: '2026-07-21T00:00:00Z', appointment: { appointment_id: 'appointment', worker_id: 'worker', purpose: 'INITIAL_REPAIR', status: 'BOOKED', scheduled_start: '2026-07-22T06:00:00Z', scheduled_end: '2026-07-22T07:00:00Z', appointment_version: 1 } }} />)
  expect(screen.getByText('WATER_LEAK')).toBeInTheDocument()
  expect(screen.getByText('SCHEDULED')).toBeInTheDocument()
})
