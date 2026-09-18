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
  expect(screen.getByText('使用这张工单')).toBeInTheDocument()
  expect(screen.queryByText('强制新建')).not.toBeInTheDocument()
})

test('shows a compact appointment choice without redundant instructions', () => {
  render(<InterruptPanel interrupt={{ kind: 'APPOINTMENT_SLOT_SELECTION', intent_version: 1, candidates_fingerprint: 'b'.repeat(64), slots: [{ rank: 1, worker_id: '22222222-2222-2222-2222-222222222222', scheduled_start: '2026-07-22T14:00:00+08:00', scheduled_end: '2026-07-22T15:00:00+08:00', booking_guaranteed: false }] }} onResume={vi.fn()} />)
  expect(screen.getByText('推荐')).toBeInTheDocument()
  expect(screen.getByText('7月22日 周三')).toBeInTheDocument()
  expect(screen.getByText('14:00–15:00')).toBeInTheDocument()
  expect(screen.queryByText(/自动收起/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '选这个时间' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '暂不选择' })).toBeInTheDocument()
  expect(screen.queryByText('booking_guaranteed=false')).not.toBeInTheDocument()
})

test('can close the slot panel while preserving the existing appointment', async () => {
  const onResume = vi.fn().mockResolvedValue(undefined)
  render(<InterruptPanel interrupt={{ kind: 'APPOINTMENT_SLOT_SELECTION', intent_version: 3, candidates_fingerprint: 'c'.repeat(64), slots: [] }} onResume={onResume} />)
  await userEvent.click(screen.getByRole('button', { name: '暂不选择' }))
  expect(onResume).toHaveBeenCalledWith({
    kind: 'CANCEL_APPOINTMENT_SLOT_SELECTION',
    intent_version: 3,
    candidates_fingerprint: 'c'.repeat(64),
  })
})
