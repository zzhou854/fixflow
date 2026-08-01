import { describe, expect, test } from 'vitest'
import { progressLabel, residentStageLabel } from './residentDisplay'
import type { AgentThread } from './types'

function state(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    thread_id: 'thread',
    trace_id: 'trace',
    message_id: null,
    workflow_stage: 'POLICY_CHECK',
    run_status: 'INTERRUPTED',
    message_outcome: 'COMPLETED',
    business_status: 'POLICY_CHECK',
    template_id: 'POLICY_CHECK',
    required_user_action: 'NONE',
    display_action_text: null,
    assistant_message: null,
    interrupt: null,
    active_ticket: null,
    active_appointment: null,
    policy_status: { sufficiency: null, conflict: false, evidence_ids: [] },
    structured_issue: {
      issue_category: null,
      issue_location: null,
      issue_description: null,
      severity: null,
    },
    safety_review_required: false,
    error_code: null,
    development_mode: true,
    ...overrides,
  }
}

describe('resident business language', () => {
  test('never exposes workflow enum names to residents', () => {
    expect(residentStageLabel('POLICY_CHECK')).toBe('正在核对服务规则')
    expect(residentStageLabel('CREATING_TICKET')).toBe('正在提交报修')
    expect(residentStageLabel('FINDING_SLOTS')).toBe('正在查询上门时间')
  })

  test('uses explicit terminal feedback instead of a blank wait', () => {
    expect(progressLabel(state(), true)).toBe('正在核对服务规则')
    expect(
      progressLabel(state({ run_status: 'FAILED_SAFE' }), false),
    ).toBe('本次处理未能完成')
    expect(
      progressLabel(state({ run_status: 'NEEDS_HUMAN_REVIEW' }), false),
    ).toBe('已转交物业工作人员')
  })
})
