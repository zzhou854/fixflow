import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { api } from '../api/client'
import { TraceTimeline } from './TraceTimeline'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, operatorRuns: vi.fn(), operatorRunEvents: vi.fn() } }
})

test('renders a sanitized Agent execution timeline separately from business history', async () => {
  vi.mocked(api.operatorRuns).mockResolvedValue({ items: [{ run_id: 'run-1', thread_id: 'thread-1', trace_id: 'trace-1', trigger: 'MESSAGE', status: 'COMPLETED', started_at: '2026-07-22T00:00:00Z', finished_at: '2026-07-22T00:00:01Z', error_code: null }] })
  vi.mocked(api.operatorRunEvents).mockResolvedValue({ items: [{ event_id: 'event-1', sequence_number: 1, source: 'AGENT', event_type: 'run_started', node_name: null, operation_id: null, payload: { run_status: 'RUNNING' }, occurred_at: '2026-07-22T00:00:00Z' }] })
  render(<TraceTimeline token="token" threadId="thread-1" />)
  expect(await screen.findByText('run_started')).toBeInTheDocument()
  expect(screen.getByText(/业务状态历史与 Agent Trace 相互独立/)).toBeInTheDocument()
  await waitFor(() => expect(api.operatorRunEvents).toHaveBeenCalledWith('token', 'run-1', expect.stringContaining('limit=50')))
})

test('shows an empty timeline and retains strict sequence order from paged API data', async () => {
  vi.mocked(api.operatorRuns).mockResolvedValue({ items: [] })
  render(<TraceTimeline token="token" threadId="thread-empty" />)
  expect(await screen.findByText('暂无执行记录')).toBeInTheDocument()

  vi.mocked(api.operatorRuns).mockResolvedValue({ items: [{ run_id: 'run-2', thread_id: 'thread-2', trace_id: 'trace-2', trigger: 'RESUME', status: 'FAILED_SAFE', started_at: '2026-07-22T00:00:00Z', finished_at: '2026-07-22T00:00:01Z', error_code: 'SAFE_STOP' }] })
  vi.mocked(api.operatorRunEvents).mockResolvedValue({ items: [
    { event_id: 'event-2', sequence_number: 2, source: 'DOMAIN', event_type: 'domain_ticket_created', node_name: null, operation_id: null, payload: { status: 'OPEN' }, occurred_at: '2026-07-22T00:00:01Z' },
    { event_id: 'event-1', sequence_number: 1, source: 'AGENT', event_type: 'run_failed_safe', node_name: null, operation_id: null, payload: { error_code: 'SAFE_STOP' }, occurred_at: '2026-07-22T00:00:00Z' },
  ] })
  render(<TraceTimeline token="token" threadId="thread-2" />)
  const failed = await screen.findByText('run_failed_safe')
  const domain = screen.getByText('domain_ticket_created')
  expect(failed.compareDocumentPosition(domain) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})
