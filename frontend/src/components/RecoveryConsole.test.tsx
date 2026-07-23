import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { RecoveryConsole } from './RecoveryConsole'

vi.mock('../api/client', async (load) => {
  const actual = await load<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, replayRuns: vi.fn(), replayRun: vi.fn(), verifyReplay: vi.fn() } }
})

const run = {
  run_id: '11111111-1111-1111-1111-111111111111', thread_id: 'thread-1',
  trigger_type: 'MESSAGE' as const, original_run_status: 'INTERRUPTED' as const,
  replayability: 'READY' as const, bundle_id: 'bundle-1', bundle_status: 'READY' as const,
  latest_replay_status: null, started_at: '2026-07-23T00:00:00Z',
}

test('renders safe replay evidence and a deterministic verify action', async () => {
  vi.mocked(api.replayRuns).mockResolvedValue({ items: [run] })
  vi.mocked(api.replayRun).mockResolvedValue({
    run,
    bundle: {
      bundle_id: 'bundle-1', original_run_id: run.run_id, status: 'READY',
      schema_version: 1, graph_schema_version: 1, runtime_revision: 'test',
      artifact_integrity: 'CHECKSUM_PRESENT', expected_route_fingerprint: null,
      expected_state_fingerprint: null, step_count: 8, capture_error_code: null,
      captured_at: '2026-07-23T00:00:00Z', finalized_at: '2026-07-23T00:00:01Z',
      latest_execution: null,
    },
    current_business_state: { ticket_status: 'OPEN', ticket_version: 1 },
  })
  vi.mocked(api.verifyReplay).mockResolvedValue({
    execution_id: 'execution-1', bundle_id: 'bundle-1', status: 'PASSED',
    runtime_revision: 'test', graph_schema_version: 1,
    started_at: '2026-07-23T00:00:02Z', completed_at: '2026-07-23T00:00:03Z',
    actual_route_fingerprint: null, actual_state_fingerprint: null, mismatches: [],
    recommendation: 'OWNER_RESUME_REQUIRED', error_code: null,
  })
  render(<RecoveryConsole token="token" threadId="thread-1" />)
  await userEvent.click(await screen.findByRole('button', { name: '查看证据' }))
  expect(await screen.findByText('CHECKSUM_PRESENT')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '验证确定性重放' }))
  expect(await screen.findByText('PASSED')).toBeInTheDocument()
  expect(screen.getByText('恢复建议：OWNER_RESUME_REQUIRED')).toBeInTheDocument()
  expect(screen.queryByText(/应用 Replay|重发 Mutation|修改 Checkpoint/)).not.toBeInTheDocument()
})

test('shows loading, empty and non-ready states without unsafe actions', async () => {
  vi.mocked(api.replayRuns).mockResolvedValue({ items: [] })
  render(<RecoveryConsole token="token" threadId="thread-2" />)
  expect(screen.getByLabelText('恢复控制台加载中')).toBeInTheDocument()
  expect(await screen.findByText('暂无可审查的执行')).toBeInTheDocument()
})

test('shows a safe loading error without raw internal data', async () => {
  vi.mocked(api.replayRuns).mockRejectedValue(new Error('database password=secret'))
  render(<RecoveryConsole token="token" threadId="thread-3" />)
  expect(await screen.findByText('恢复记录加载失败')).toBeInTheDocument()
  expect(screen.queryByText(/password=secret/)).not.toBeInTheDocument()
})

test.each([
  'DIVERGED',
  'INCOMPLETE',
  'UNSUPPORTED_SCHEMA',
  'FAILED_SAFE',
] as const)('renders %s and safe mismatch summaries', async (status) => {
  vi.mocked(api.replayRuns).mockResolvedValue({ items: [run] })
  vi.mocked(api.replayRun).mockResolvedValue({
    run,
    bundle: {
      bundle_id: 'bundle-1', original_run_id: run.run_id, status: 'READY',
      schema_version: 1, graph_schema_version: 1, runtime_revision: 'test',
      artifact_integrity: 'CHECKSUM_PRESENT', expected_route_fingerprint: null,
      expected_state_fingerprint: null, step_count: 8, capture_error_code: null,
      captured_at: '2026-07-23T00:00:00Z', finalized_at: '2026-07-23T00:00:01Z',
      latest_execution: {
        execution_id: 'execution-1', bundle_id: 'bundle-1', status,
        runtime_revision: 'test', graph_schema_version: 1,
        started_at: '2026-07-23T00:00:02Z', completed_at: '2026-07-23T00:00:03Z',
        actual_route_fingerprint: null, actual_state_fingerprint: null,
        mismatches: [{
          mismatch_type: 'FINAL_STATE_MISMATCH', step_key: 'final-state',
          expected_summary: 'expected-safe-fingerprint',
          actual_summary: 'actual-safe-fingerprint',
        }],
        recommendation: 'MANUAL_REVIEW_REQUIRED', error_code: null,
      },
    },
    current_business_state: null,
  })
  render(<RecoveryConsole token="token" threadId={`thread-${status}`} />)
  await userEvent.click(await screen.findByRole('button', { name: '查看证据' }))
  expect(await screen.findByText(status)).toBeInTheDocument()
  expect(screen.getByText('预期：expected-safe-fingerprint')).toBeInTheDocument()
  expect(screen.getByText('实际：actual-safe-fingerprint')).toBeInTheDocument()
})
