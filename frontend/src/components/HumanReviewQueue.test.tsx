import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import type { HumanReviewCase } from '../types'
import { HumanReviewQueue } from './HumanReviewQueue'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      humanReviewCases: vi.fn(),
      transitionHumanReview: vi.fn(),
      humanReviewEvents: vi.fn(),
    },
  }
})

function reviewCase(overrides: Partial<HumanReviewCase> = {}): HumanReviewCase {
  return {
    case_id: 'case-1',
    thread_id: 'thread-1',
    resident_id: 'resident-1',
    property_id: 'property-1',
    ticket_id: null,
    source_run_id: 'run-1',
    intent_version: 2,
    failure_stage: 'SAFETY_REVIEW',
    reason_code: 'SAFETY_REVIEW_REQUIRED',
    last_error_code: null,
    safety_level: 'EMERGENCY',
    priority: 100,
    summary: '住户报告电线冒烟，需要物业立即核实',
    status: 'OPEN',
    assigned_operator_id: null,
    version: 1,
    created_at: '2026-07-31T08:00:00+08:00',
    updated_at: '2026-07-31T08:00:00+08:00',
    claimed_at: null,
    resolved_at: null,
    resolution_code: null,
    resolution_note: null,
    resident_username: 'resident_test',
    property_address: '星河花园 1 栋 1 单元 101',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.humanReviewCases).mockResolvedValue({
    items: [reviewCase()],
    limit: 100,
    offset: 0,
  })
  vi.mocked(api.humanReviewEvents).mockResolvedValue({ items: [] })
})

test('renders a prioritized pre-ticket review without fabricating a ticket', async () => {
  render(<HumanReviewQueue token="token" />)
  expect(await screen.findByText('住户报告电线冒烟，需要物业立即核实')).toBeInTheDocument()
  expect(screen.getByText('紧急')).toBeInTheDocument()
  expect(screen.getByText('星河花园 1 栋 1 单元 101')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /查看/ }))
  expect(await screen.findByText('尚未创建工单')).toBeInTheDocument()
  expect(screen.getByText('存在需要物业核实的安全风险')).toBeInTheDocument()
  expect(screen.getByText('服务房屋')).toBeInTheDocument()
  expect(screen.getByText(/系统不会为可见性伪造工单/)).toBeInTheDocument()
  expect(screen.getByText('技术审计')).toBeInTheDocument()
})

test('claims with the optimistic version and reloads the queue', async () => {
  const claimed = reviewCase({
    status: 'CLAIMED',
    assigned_operator_id: 'operator',
    version: 2,
  })
  vi.mocked(api.transitionHumanReview).mockResolvedValue(claimed)
  render(<HumanReviewQueue token="token" />)
  await userEvent.click(await screen.findByRole('button', { name: /领取/ }))
  expect(api.transitionHumanReview).toHaveBeenCalledWith(
    'token',
    'case-1',
    1,
    'CLAIMED',
    undefined,
    undefined,
  )
})

test('can hide seeded demonstration tasks from a clean operator workspace', async () => {
  vi.mocked(api.humanReviewCases).mockResolvedValue({
    items: [
      reviewCase({ case_id: 'demo-case', resident_username: 'resident_demo' }),
      reviewCase({ case_id: 'test-case', summary: '用户测试任务' }),
    ],
    limit: 100,
    offset: 0,
  })
  render(<HumanReviewQueue token="token" excludedResidentUsername="resident_demo" />)
  expect(await screen.findByText('用户测试任务')).toBeInTheDocument()
  expect(screen.queryByText('住户报告电线冒烟，需要物业立即核实')).not.toBeInTheDocument()
})

test('explains an overdue appointment in business language', async () => {
  vi.mocked(api.humanReviewCases).mockResolvedValue({
    items: [reviewCase({
      failure_stage: 'SCHEDULING',
      reason_code: 'OVERDUE_APPOINTMENT_REVIEW_REQUIRED',
      summary: '原预约已经过期，需要核实维修结果',
    })],
    limit: 100,
    offset: 0,
  })
  render(<HumanReviewQueue token="token" />)
  await userEvent.click(await screen.findByRole('button', { name: /查看/ }))
  expect(screen.getByText('原上门时间已过，但维修结果尚未确认')).toBeInTheDocument()
})

test('refreshes instead of overwriting an optimistic conflict', async () => {
  vi.mocked(api.transitionHumanReview).mockRejectedValue(
    new ApiError(
      {
        code: 'VERSION_CONFLICT',
        message: '任务已更新',
        field_errors: {},
        trace_id: 'trace',
        retryable: true,
      },
      409,
    ),
  )
  render(<HumanReviewQueue token="token" />)
  await userEvent.click(await screen.findByRole('button', { name: /领取/ }))
  await waitFor(() => expect(api.humanReviewCases).toHaveBeenCalledTimes(2))
  expect(api.transitionHumanReview).toHaveBeenCalledTimes(1)
})
