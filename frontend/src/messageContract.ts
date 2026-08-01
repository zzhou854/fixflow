export const MESSAGE_OUTCOMES = ['COMPLETED', 'FAILED', 'ESCALATED'] as const
export type MessageOutcome = (typeof MESSAGE_OUTCOMES)[number]

export const REQUIRED_USER_ACTIONS = [
  'NONE',
  'PROVIDE_DETAILS',
  'SELECT_SLOT',
  'CONFIRM_ACTION',
  'CONTACT_OPERATOR',
  'RETRY',
] as const
export type RequiredUserAction = (typeof REQUIRED_USER_ACTIONS)[number]
