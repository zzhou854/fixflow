import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import type { ResidentThreadSummary } from '../types'
import { ResidentConversationList } from './ResidentConversationList'

function thread(index: number, archived = false): ResidentThreadSummary {
  return {
    thread_id: `thread-${index}`,
    property_id: 'property',
    workflow_stage: index % 2 ? 'NEED_INFO' : 'DONE',
    run_status: index % 2 ? 'INTERRUPTED' : 'COMPLETED',
    issue_category: 'WATER_LEAK',
    issue_location: `位置${index}`,
    active_ticket_id: index === 1 ? 'ticket' : null,
    lifecycle_status: archived ? 'ARCHIVED' : 'ACTIVE',
    archived_at: archived ? '2026-07-31T00:00:00+08:00' : null,
    version: 1,
    updated_at: `2026-07-${String(Math.min(index, 9)).padStart(2, '0')}T00:00:00+08:00`,
  }
}

const baseProps = {
  currentThreadId: undefined,
  allThreadsLoading: false,
  archiveFilter: 'active' as const,
  drawerOpen: false,
  onDrawerOpen: vi.fn(),
  onDrawerClose: vi.fn(),
  onFilterChange: vi.fn(),
  onSelect: vi.fn().mockResolvedValue(undefined),
  onArchive: vi.fn().mockResolvedValue(undefined),
  onRestore: vi.fn().mockResolvedValue(undefined),
}

test('shows only the five most recent sessions in the fixed recent list', () => {
  const items = [1, 2, 3, 4, 5, 6].map((index) => thread(index))
  render(
    <ResidentConversationList
      {...baseProps}
      recentThreads={items}
      allThreads={items}
    />,
  )
  expect(screen.getAllByRole('button', { name: /漏水报修/ })).toHaveLength(5)
  expect(screen.queryByText('漏水报修 · 位置6')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /查看全部会话/ })).toBeInTheDocument()
})

test('explains archive semantics and restores an archived session', async () => {
  const onRestore = vi.fn().mockResolvedValue(undefined)
  const archived = thread(7, true)
  const { rerender } = render(
    <ResidentConversationList
      {...baseProps}
      recentThreads={[]}
      allThreads={[archived]}
      drawerOpen={true}
      onRestore={onRestore}
    />,
  )
  expect(screen.getByText(/不会取消工单、预约或删除历史记录/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /恢复/ }))
  expect(onRestore).toHaveBeenCalledWith(archived)

  const activeWithTicket = thread(1)
  rerender(
    <ResidentConversationList
      {...baseProps}
      recentThreads={[]}
      allThreads={[activeWithTicket]}
      drawerOpen={true}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: /归档会话/ }))
  expect(screen.getByText('归档这段会话？')).toBeInTheDocument()
  expect(screen.getByText(/不会取消已有工单、预约或删除审计记录/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /^归档会话$/ }))
  expect(baseProps.onArchive).toHaveBeenCalledWith(activeWithTicket)
})
