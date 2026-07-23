export type ActorType = 'RESIDENT' | 'OPERATOR'
export type WorkflowStage =
  | 'INTAKE' | 'NEED_PROPERTY' | 'NEED_INFO' | 'EMERGENCY_REVIEW'
  | 'POLICY_CHECK' | 'DUPLICATE_CHECK' | 'EXISTING_TICKET' | 'CREATING_TICKET'
  | 'RECONCILIATION_PENDING' | 'FINDING_SLOTS' | 'AWAITING_SLOT_CONFIRMATION'
  | 'BOOKING' | 'MONITORING_APPOINTMENT' | 'RESCHEDULING' | 'STATUS_CONFLICT'
  | 'AWAITING_ACCEPTANCE' | 'PLANNING_REWORK' | 'HUMAN_REVIEW' | 'DONE'

export interface User { user_id: string; username: string; actor_type: ActorType }
export interface LoginResult { access_token: string; token_type: string; expires_at: string; user: User }
export interface Property {
  property_id: string; community_name: string; building_no: string; unit_no: string
  room_no: string; address_text: string
}
export interface Appointment {
  appointment_id: string; worker_id: string; purpose: string; status: string
  scheduled_start: string; scheduled_end: string; appointment_version: number
}
export interface Ticket {
  ticket_id: string; resident_id: string; resident_username: string; property_id: string
  property_label: string; issue_category: string; issue_location: string; severity: string
  ticket_status: string; rework_count: number; version: number
  appointment: Appointment | null; updated_at: string
}
export interface TicketHistory {
  from_status: string | null; to_status: string; action: string; actor_type: ActorType
  reason_code: string | null; reason_text: string | null; version_after: number; created_at: string
}
export interface AppointmentHistory {
  from_status: string | null; to_status: string; actor_type: ActorType
  reason_code: string | null; reason_text: string | null; version_after: number; created_at: string
}
export interface WorkerEvent {
  event_id: string; event_type: string; sequence_no: number; subject_worker_id: string
}
export interface TicketDetail {
  ticket: Ticket; issue_description: string; escalated_from_status: string | null
  ticket_history: TicketHistory[]; appointment_history: AppointmentHistory[]
  latest_worker_event: WorkerEvent | null
}
export interface NeedInformationInterrupt {
  kind: 'NEED_INFORMATION'; intent_version: number; missing_fields: string[]; message: string
}
export interface DuplicateInterrupt {
  kind: 'DUPLICATE_TICKET_SELECTION'; intent_version: number; candidates_fingerprint: string
  tickets: Array<{ ticket_id: string; ticket_version: number; ticket_status: string; issue_location: string }>
}
export interface SlotInterrupt {
  kind: 'APPOINTMENT_SLOT_SELECTION'; intent_version: number; candidates_fingerprint: string
  slots: Array<{ rank: number; worker_id: string; scheduled_start: string; scheduled_end: string; booking_guaranteed: false }>
}
export type Interrupt = NeedInformationInterrupt | DuplicateInterrupt | SlotInterrupt
export interface AgentThread {
  thread_id: string; trace_id: string; run_id?: string | null; message_id: string | null; workflow_stage: WorkflowStage
  run_status: 'COMPLETED' | 'INTERRUPTED' | 'NEEDS_HUMAN_REVIEW' | 'FAILED_SAFE'
  assistant_message: string | null; interrupt: Interrupt | null
  active_ticket: Ticket | null; active_appointment: Appointment | null
  policy_status: { sufficiency: string | null; conflict: boolean; evidence_ids: string[] }
  structured_issue: { issue_category: string | null; issue_location: string | null; issue_description: string | null; severity: string | null }
  safety_review_required: boolean; error_code: string | null; development_mode: true
  reconciliation?: ResidentReconciliation | null
}
export type ReconciliationStatus = 'PENDING'|'PROCESSING'|'RESOLVED_COMMITTED'|'RESOLVED_NOT_COMMITTED'|'MANUAL_REVIEW'
export interface ResidentReconciliation { case_id: string; status: ReconciliationStatus; action: string; retry_allowed: boolean }
export interface ReconciliationCase {
  case_id: string; operation_id_short?: string; thread_id_short?: string | null; original_run_id_short?: string
  operation_type: string; status: ReconciliationStatus; target_entity_type?: string | null
  target_entity_id: string | null; expected_entity_version?: number | null; attempt_count: number
  evidence_status: string | null; last_error_code?: string | null; resolution_code: string | null
  safe_result?: Record<string, unknown> | null; retry_allowed: boolean; created_at: string
  updated_at: string; resolved_at?: string | null
}
export interface OperationResponse {
  ok: boolean; code: string; resource_type: string | null; resource_id: string | null
  resource_version: number | null; replayed: boolean
  reconciliation: null | {
    case_id: string; action: string; status: ReconciliationStatus
    retry_allowed: boolean; ticket_id: string
  }
}
export interface OperatorThread {
  thread_id: string; workflow_stage: WorkflowStage
  run_status: 'COMPLETED' | 'INTERRUPTED' | 'NEEDS_HUMAN_REVIEW' | 'FAILED_SAFE'
  task_intent: string; issue_category: string | null; issue_location: string | null; severity: string | null
  policy_sufficiency: string | null; policy_conflict: boolean; policy_evidence_summary: string[]
  missing_fields: string[]; active_ticket_id: string | null; active_appointment_id: string | null
  human_review_required: boolean; updated_at: string | null
}
export interface AgentRun {
  run_id: string; thread_id: string | null; trace_id: string
  trigger: 'THREAD_CREATED' | 'MESSAGE' | 'RESUME' | 'OPERATOR_ACTION'
  status: 'RUNNING' | 'INTERRUPTED' | 'COMPLETED' | 'FAILED_SAFE' | 'FAILED'
  started_at: string; finished_at: string | null; error_code: string | null
}
export interface TraceEvent {
  event_id: string; sequence_number: number | null
  source: 'API' | 'AGENT' | 'MCP' | 'DOMAIN' | 'OUTBOX' | 'RECONCILIATION' | 'REPLAY'
  event_type: string; node_name: string | null; operation_id: string | null
  payload: Record<string, string | number | boolean | null>; occurred_at: string
}
export type ReplayBundleStatus = 'CAPTURING'|'READY'|'INCOMPLETE'|'INVALID'|'UNAVAILABLE'
export type ReplayExecutionStatus = 'RUNNING'|'PASSED'|'DIVERGED'|'INCOMPLETE'|'UNSUPPORTED_SCHEMA'|'FAILED_SAFE'
export interface ReplayMismatch {
  mismatch_type: string; step_key: string | null
  expected_summary: string | null; actual_summary: string | null
}
export interface ReplayExecution {
  execution_id: string; bundle_id: string; status: ReplayExecutionStatus
  runtime_revision: string; graph_schema_version: number; started_at: string
  completed_at: string | null; actual_route_fingerprint: string | null
  actual_state_fingerprint: string | null; mismatches: ReplayMismatch[]
  recommendation: string | null; error_code: string | null
}
export interface ReplayBundle {
  bundle_id: string; original_run_id: string; status: ReplayBundleStatus
  schema_version: number; graph_schema_version: number; runtime_revision: string
  artifact_integrity: string; expected_route_fingerprint: string | null
  expected_state_fingerprint: string | null; step_count: number
  capture_error_code: string | null; captured_at: string; finalized_at: string | null
  latest_execution: ReplayExecution | null
}
export interface ReplayRun {
  run_id: string; thread_id: string | null; trigger_type: AgentRun['trigger']
  original_run_status: AgentRun['status']; replayability: ReplayBundleStatus
  bundle_id: string | null; bundle_status: ReplayBundleStatus
  latest_replay_status: ReplayExecutionStatus | null; started_at: string
}
export interface ReplayRunDetail {
  run: ReplayRun; bundle: ReplayBundle | null
  current_business_state: Record<string, string | number | boolean | null> | null
}
export interface ApiErrorBody { code: string; message: string; field_errors: Record<string, string>; trace_id: string; retryable: boolean }
