import { describe, expect, it } from 'vitest'
import { MESSAGE_OUTCOMES, REQUIRED_USER_ACTIONS } from './messageContract'

describe('public message contract', () => {
  it('keeps terminal outcomes aligned with the backend contract', () => {
    expect(MESSAGE_OUTCOMES).toEqual(['COMPLETED', 'FAILED', 'ESCALATED'])
  })

  it('uses machine actions rather than resident-facing Chinese copy', () => {
    expect(REQUIRED_USER_ACTIONS).toEqual([
      'NONE',
      'PROVIDE_DETAILS',
      'SELECT_SLOT',
      'CONFIRM_ACTION',
      'CONTACT_OPERATOR',
      'RETRY',
    ])
    expect(REQUIRED_USER_ACTIONS.every((value) => /^[A-Z_]+$/.test(value))).toBe(true)
  })
})
