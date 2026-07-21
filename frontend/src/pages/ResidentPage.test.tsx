import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { ResidentPage } from './ResidentPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'token', user: { user_id: 'r', username: 'resident_demo', actor_type: 'RESIDENT' }, logout: vi.fn() }) }))
vi.mock('../hooks/useSSE', () => ({ useSSE: () => 'idle' }))
vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, properties: vi.fn(), getThread: vi.fn(), createThread: vi.fn(), sendMessage: vi.fn(), resume: vi.fn() } }
})

test('renders a resident message and the agent reply', async () => {
  sessionStorage.clear()
  vi.mocked(api.properties).mockResolvedValue([{ property_id: 'p', community_name: '星河花园', building_no: '3', unit_no: '2', room_no: '1201', address_text: '星河花园 1201' }])
  vi.mocked(api.createThread).mockResolvedValue({ thread_id: 't', trace_id: 'trace', message_id: 'm', workflow_stage: 'NEED_INFO', run_status: 'INTERRUPTED', assistant_message: '请补充具体位置', interrupt: null, active_ticket: null, active_appointment: null, policy_status: { sufficiency: null, conflict: false, evidence_ids: [] }, structured_issue: { issue_category: null, issue_location: null, issue_description: null, severity: null }, safety_review_required: false, error_code: null, development_mode: true })
  render(<ResidentPage />)
  const input = screen.getByPlaceholderText('例如：厨房水龙头漏水，明天下午有空')
  await userEvent.type(input, '家里漏水')
  await userEvent.click(screen.getByRole('button', { name: /发送/ }))
  expect(await screen.findByText('家里漏水')).toBeInTheDocument()
  expect(await screen.findByText('请补充具体位置')).toBeInTheDocument()
})

test('restores only the saved thread id through the state API', async () => {
  sessionStorage.clear()
  sessionStorage.setItem('fixflow.demo.thread_id', 'saved-thread')
  vi.mocked(api.properties).mockResolvedValue([])
  vi.mocked(api.getThread).mockResolvedValue({ thread_id: 'saved-thread', trace_id: 'trace', message_id: null, workflow_stage: 'DONE', run_status: 'COMPLETED', assistant_message: null, interrupt: null, active_ticket: null, active_appointment: null, policy_status: { sufficiency: null, conflict: false, evidence_ids: [] }, structured_issue: { issue_category: null, issue_location: null, issue_description: null, severity: null }, safety_review_required: false, error_code: null, development_mode: true })
  render(<ResidentPage />)
  expect(await screen.findByText('DONE')).toBeInTheDocument()
  expect(api.getThread).toHaveBeenCalledWith('token', 'saved-thread')
})
