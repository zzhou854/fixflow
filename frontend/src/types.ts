export type ActorType = 'RESIDENT' | 'OPERATOR'
export type WorkflowStage =
  | 'INTAKE' | 'NEED_PROPERTY' | 'NEED_INFO' | 'EMERGENCY_REVIEW'
  | 'POLICY_CHECK' | 'DUPLICATE_CHECK' | 'EXISTING_TICKET' | 'CREATING_TICKET'
  | 'UNKNOWN_COMMIT' | 'FINDING_SLOTS' | 'AWAITING_SLOT_CONFIRMATION'
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
  source: 'API' | 'AGENT' | 'MCP' | 'DOMAIN' | 'OUTBOX'
  event_type: string; node_name: string | null; operation_id: string | null
  payload: Record<string, string | number | boolean | null>; occurred_at: string
}
export interface ApiErrorBody { code: string; message: string; field_errors: Record<string, string>; trace_id: string; retryable: boolean }
