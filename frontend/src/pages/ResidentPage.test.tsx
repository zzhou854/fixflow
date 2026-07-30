import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { ResidentPage } from './ResidentPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'token', user: { user_id: 'r', username: 'resident_demo', actor_type: 'RESIDENT' }, logout: vi.fn() }) }))
vi.mock('../hooks/useSSE', () => ({ useSSE: () => 'idle' }))
vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, properties: vi.fn(), residentThreads: vi.fn(), getThread: vi.fn(), createThread: vi.fn(), sendMessage: vi.fn(), resume: vi.fn() } }
})

test('renders a resident message and the agent reply', async () => {
  sessionStorage.clear()
  vi.mocked(api.properties).mockResolvedValue([{ property_id: 'p', community_name: '星河花园', building_no: '3', unit_no: '2', room_no: '1201', address_text: '星河花园 1201' }])
  vi.mocked(api.residentThreads).mockResolvedValue({ items: [], limit: 20, offset: 0 })
  vi.mocked(api.createThread).mockResolvedValue({ thread_id: 't', trace_id: 'trace', message_id: 'm', workflow_stage: 'NEED_INFO', run_status: 'INTERRUPTED', assistant_message: '请补充具体位置', interrupt: null, active_ticket: null, active_appointment: null, policy_status: { sufficiency: null, conflict: false, evidence_ids: [] }, structured_issue: { issue_category: null, issue_location: null, issue_description: null, severity: null }, safety_review_required: false, error_code: null, development_mode: true, conversation_messages: [{ role: 'USER', content: '家里漏水', created_at: '2026-07-27T12:00:00+08:00' }, { role: 'ASSISTANT', content: '请补充具体位置', created_at: '2026-07-27T12:00:01+08:00' }] })
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
  vi.mocked(api.residentThreads).mockResolvedValue({ items: [], limit: 20, offset: 0 })
  vi.mocked(api.getThread).mockResolvedValue({ thread_id: 'saved-thread', trace_id: 'trace', message_id: null, workflow_stage: 'DONE', run_status: 'COMPLETED', assistant_message: null, interrupt: null, active_ticket: null, active_appointment: null, policy_status: { sufficiency: null, conflict: false, evidence_ids: [] }, structured_issue: { issue_category: null, issue_location: null, issue_description: null, severity: null }, safety_review_required: false, error_code: null, development_mode: true })
  render(<ResidentPage />)
  expect(await screen.findByText('DONE')).toBeInTheDocument()
  expect(api.getThread).toHaveBeenCalledWith('token', 'saved-thread')
})

test('keeps previous sessions visible after starting a new session', async () => {
  sessionStorage.clear()
  vi.mocked(api.properties).mockResolvedValue([{ property_id: 'p', community_name: '星河花园', building_no: '3', unit_no: '2', room_no: '1201', address_text: '星河花园 1201' }])
  vi.mocked(api.residentThreads).mockResolvedValue({
    items: [{
      thread_id: 'old-thread', property_id: 'p', workflow_stage: 'NEED_INFO',
      run_status: 'INTERRUPTED', issue_category: 'WATER_LEAK', issue_location: '厨房',
      active_ticket_id: null, updated_at: '2026-07-27T12:00:00+08:00',
    }], limit: 20, offset: 0,
  })
  render(<ResidentPage />)
  expect(await screen.findByText('漏水报修 · 厨房')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /新建会话/ }))
  expect(screen.getByText('漏水报修 · 厨房')).toBeInTheDocument()
})

test('uses the resume endpoint when chatting during a need-information interrupt', async () => {
  sessionStorage.clear()
  sessionStorage.setItem('fixflow.demo.thread_id', 'needs-info')
  vi.mocked(api.properties).mockResolvedValue([{ property_id: 'p', community_name: '星河花园', building_no: '3', unit_no: '2', room_no: '1201', address_text: '星河花园 1201' }])
  vi.mocked(api.residentThreads).mockResolvedValue({ items: [], limit: 20, offset: 0 })
  vi.mocked(api.getThread).mockResolvedValue({
    thread_id: 'needs-info', trace_id: 'trace', message_id: null, workflow_stage: 'NEED_INFO',
    run_status: 'INTERRUPTED', assistant_message: '请补充位置',
    interrupt: { kind: 'NEED_INFORMATION', intent_version: 3, missing_fields: ['ISSUE_LOCATION'], message: '请补充位置' },
    active_ticket: null, active_appointment: null,
    policy_status: { sufficiency: null, conflict: false, evidence_ids: [] },
    structured_issue: { issue_category: 'WATER_LEAK', issue_location: null, issue_description: '漏水', severity: null },
    safety_review_required: false, error_code: null, development_mode: true,
    conversation_messages: [],
  })
  vi.mocked(api.resume).mockResolvedValue({
    thread_id: 'needs-info', trace_id: 'trace-2', message_id: null, workflow_stage: 'FINDING_SLOTS',
    run_status: 'COMPLETED', assistant_message: '已收到位置',
    interrupt: null, active_ticket: null, active_appointment: null,
    policy_status: { sufficiency: 'SUFFICIENT', conflict: false, evidence_ids: [] },
    structured_issue: { issue_category: 'WATER_LEAK', issue_location: '厨房', issue_description: '漏水', severity: 'MEDIUM' },
    safety_review_required: false, error_code: null, development_mode: true,
    conversation_messages: [{ role: 'USER', content: '厨房水槽下方', created_at: '2026-07-27T12:00:00+08:00' }],
  })
  render(<ResidentPage />)
  const input = await screen.findByPlaceholderText('直接补充，例如：故障位置在厨房水槽下方')
  await userEvent.type(input, '厨房水槽下方')
  await userEvent.keyboard('{Enter}')
  expect(api.resume).toHaveBeenCalledWith(
    'token',
    'needs-info',
    expect.objectContaining({
      kind: 'PROVIDE_INFORMATION',
      intent_version: 3,
      user_message: '厨房水槽下方',
    }),
  )
  expect(api.sendMessage).not.toHaveBeenCalled()
})
