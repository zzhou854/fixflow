import type { AgentRun, AgentThread, ApiErrorBody, HumanReviewCase, HumanReviewEvent, HumanReviewStatus, LoginResult, OperationResponse, OperatorThread, Property, ReconciliationCase, ReplayExecution, ReplayRun, ReplayRunDetail, ResidentThreadSummary, Ticket, TicketDetail, TraceEvent } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
const SHANGHAI_OFFSET_MILLISECONDS = 8 * 60 * 60 * 1000
const API_REQUEST_TIMEOUT_MILLISECONDS = 40_000

export function shanghaiReferenceTime(now: Date = new Date()): string {
  return new Date(now.getTime() + SHANGHAI_OFFSET_MILLISECONDS)
    .toISOString()
    .replace('Z', '+08:00')
}

export class ApiError extends Error {
  constructor(public readonly body: ApiErrorBody, public readonly status: number) {
    super(body.message)
  }
}

export async function apiRequest<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MILLISECONDS)
  const abort = () => controller.abort()
  init.signal?.addEventListener('abort', abort, { once: true })
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: controller.signal })
    if (!response.ok) throw new ApiError(await response.json() as ApiErrorBody, response.status)
    return await response.json() as T
  } finally {
    window.clearTimeout(timeout)
    init.signal?.removeEventListener('abort', abort)
  }
}

export const api = {
  login: (username: string, password: string) => apiRequest<LoginResult>('/api/v1/auth/login', null, {
    method: 'POST', body: JSON.stringify({ username, password }),
  }),
  me: (token: string) => apiRequest<LoginResult['user']>('/api/v1/auth/me', token),
  properties: (token: string) => apiRequest<Property[]>('/api/v1/resident/properties', token),
  residentTickets: (token: string) => apiRequest<{ items: Ticket[] }>('/api/v1/resident/tickets', token),
  operatorTickets: (token: string, query = '') => apiRequest<{ items: Ticket[] }>(`/api/v1/operator/tickets${query}`, token),
  operatorTicket: (token: string, ticketId: string) => apiRequest<TicketDetail>(`/api/v1/operator/tickets/${ticketId}`, token),
  operatorThread: (token: string, threadId: string) => apiRequest<OperatorThread>(`/api/v1/operator/threads/${threadId}`, token),
  operatorRuns: (token: string, threadId: string, query = '') => apiRequest<{ items: AgentRun[] }>(`/api/v1/operator/threads/${threadId}/runs${query}`, token),
  operatorRunEvents: (token: string, runId: string, query = '') => apiRequest<{ items: TraceEvent[] }>(`/api/v1/operator/runs/${runId}/events${query}`, token),
  replayRuns: (token: string, threadId: string) => apiRequest<{ items: ReplayRun[] }>(`/api/v1/operator/threads/${threadId}/replay-runs`, token),
  replayRun: (token: string, runId: string) => apiRequest<ReplayRunDetail>(`/api/v1/operator/runs/${runId}/replay`, token),
  verifyReplay: (token: string, runId: string, idempotencyKey: string = crypto.randomUUID()) => apiRequest<ReplayExecution>(`/api/v1/operator/runs/${runId}/replay/verify`, token, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
  }),
  reconciliationCases: (token: string, query = '') => apiRequest<{items: ReconciliationCase[]}>(`/api/v1/operator/reconciliation/cases${query}`,token),
  recheckReconciliation: (token: string, caseId: string) => apiRequest<ReconciliationCase>(`/api/v1/operator/reconciliation/cases/${caseId}/recheck`,token,{method:'POST'}),
  reconciliationCase: (token: string, caseId: string) => apiRequest<ReconciliationCase>(`/api/v1/operator/reconciliation/cases/${caseId}`, token),
  humanReviewCases: (token: string, status?: HumanReviewStatus, limit = 100, offset = 0) => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (status) query.set('status', status)
    return apiRequest<{ items: HumanReviewCase[]; limit: number; offset: number }>(
      `/api/v1/operator/human-review-cases?${query}`,
      token,
    )
  },
  transitionHumanReview: (
    token: string,
    caseId: string,
    expectedVersion: number,
    targetStatus: HumanReviewStatus,
    resolutionCode?: string,
    resolutionNote?: string,
  ) => apiRequest<HumanReviewCase>(
    `/api/v1/operator/human-review-cases/${caseId}/transition`,
    token,
    {
      method: 'POST',
      body: JSON.stringify({
        target_status: targetStatus,
        expected_version: expectedVersion,
        resolution_code: resolutionCode ?? null,
        resolution_note: resolutionNote ?? null,
      }),
    },
  ),
  humanReviewEvents: (token: string, caseId: string) =>
    apiRequest<{ items: HumanReviewEvent[] }>(
      `/api/v1/operator/human-review-cases/${caseId}/events`,
      token,
    ),
  operatorEscalate: (token: string, ticketId: string, expectedVersion: number, idempotencyKey: string) => apiRequest<OperationResponse>(`/api/v1/operator/tickets/${ticketId}/escalate`, token, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({
      expected_version: expectedVersion,
      reason_code: 'OPERATOR_REVIEW',
      reason_text: '物业工作台人工升级',
      evidence: [],
    }),
  }),
  getThread: (token: string, threadId: string) => apiRequest<AgentThread>(`/api/v1/agent/threads/${threadId}`, token),
  residentThreads: (token: string, archiveStatus: 'active'|'archived'|'all' = 'active', limit = 5, offset = 0) => apiRequest<{ items: ResidentThreadSummary[]; limit: number; offset: number }>(`/api/v1/agent/threads?archive_status=${archiveStatus}&limit=${limit}&offset=${offset}`, token),
  archiveThread: (token: string, threadId: string, expectedVersion: number) => apiRequest<{thread_id: string; lifecycle_status: 'ARCHIVED'; archived_at: string; version: number}>(`/api/v1/agent/threads/${threadId}/archive`, token, {
    method: 'POST', body: JSON.stringify({ expected_version: expectedVersion }),
  }),
  restoreThread: (token: string, threadId: string, expectedVersion: number) => apiRequest<{thread_id: string; lifecycle_status: 'ACTIVE'; archived_at: null; version: number}>(`/api/v1/agent/threads/${threadId}/restore`, token, {
    method: 'POST', body: JSON.stringify({ expected_version: expectedVersion }),
  }),
  createThread: (token: string, property_id: string, initial_message: string, idempotencyKey: string = crypto.randomUUID(), referenceTime: string = shanghaiReferenceTime()) => apiRequest<AgentThread>('/api/v1/agent/threads', token, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ property_id, initial_message, timezone_name: 'Asia/Shanghai', reference_time: referenceTime }),
  }),
  sendMessage: (token: string, threadId: string, message: string, idempotencyKey: string = crypto.randomUUID(), referenceTime: string = shanghaiReferenceTime()) => apiRequest<AgentThread>(`/api/v1/agent/threads/${threadId}/messages`, token, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ message, message_id: idempotencyKey, timezone_name: 'Asia/Shanghai', reference_time: referenceTime }),
  }),
  resume: (token: string, threadId: string, body: object, idempotencyKey: string = crypto.randomUUID()) => apiRequest<AgentThread>(`/api/v1/agent/threads/${threadId}/resume`, token, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(body),
  }),
}

export function eventStreamUrl(threadId: string): string {
  return `${API_BASE}/api/v1/agent/threads/${threadId}/events`
}
