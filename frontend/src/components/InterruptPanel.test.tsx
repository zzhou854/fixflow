import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { InterruptPanel } from './InterruptPanel'

test('submits missing information with the strict resume kind', async () => {
  const onResume = vi.fn().mockResolvedValue(undefined)
  render(<InterruptPanel interrupt={{ kind: 'NEED_INFORMATION', intent_version: 2, missing_fields: ['ISSUE_LOCATION'], message: '请补充位置' }} onResume={onResume} />)
  await userEvent.type(screen.getByLabelText('补充信息'), '厨房')
  fireEvent.click(screen.getByText('提交补充信息'))
  await waitFor(() => expect(onResume).toHaveBeenCalledWith(expect.objectContaining({ kind: 'PROVIDE_INFORMATION', intent_version: 2, user_message: '厨房' })))
})

test('offers selection of an existing duplicate without force-create', () => {
  render(<InterruptPanel interrupt={{ kind: 'DUPLICATE_TICKET_SELECTION', intent_version: 1, candidates_fingerprint: 'a'.repeat(64), tickets: [{ ticket_id: '11111111-1111-1111-1111-111111111111', ticket_version: 1, ticket_status: 'OPEN', issue_location: '厨房' }] }} onResume={vi.fn()} />)
  expect(screen.getByText('选择已有工单')).toBeInTheDocument()
  expect(screen.queryByText('强制新建')).not.toBeInTheDocument()
})

test('shows a resident-friendly slot rank and non-guarantee warning', () => {
  render(<InterruptPanel interrupt={{ kind: 'APPOINTMENT_SLOT_SELECTION', intent_version: 1, candidates_fingerprint: 'b'.repeat(64), slots: [{ rank: 1, worker_id: '22222222-2222-2222-2222-222222222222', scheduled_start: '2026-07-22T14:00:00+08:00', scheduled_end: '2026-07-22T15:00:00+08:00', booking_guaranteed: false }] }} onResume={vi.fn()} />)
  expect(screen.getByText('推荐顺序 1')).toBeInTheDocument()
  expect(screen.getByText('选择后系统会再次确认，成功后才算预约完成')).toBeInTheDocument()
  expect(screen.queryByText('booking_guaranteed=false')).not.toBeInTheDocument()
})
