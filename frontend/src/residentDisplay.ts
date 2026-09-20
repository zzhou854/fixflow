import type { AgentThread, ResidentThreadSummary, WorkflowStage } from './types'

export const CATEGORY_LABELS: Record<string, string> = {
  WATER_LEAK: '漏水报修',
  ELECTRICAL: '电气报修',
  DOOR_LOCK: '门锁报修',
}

export const TICKET_STATUS_LABELS: Record<string, string> = {
  OPEN: '等待安排',
  SCHEDULED: '已安排上门',
  IN_PROGRESS: '正在维修',
  PENDING_ACCEPTANCE: '等待确认结果',
  REWORK_REQUIRED: '等待返工',
  ESCALATED: '物业人工处理中',
  CANCELLED: '已取消',
  CLOSED: '已完成',
}

export const SEVERITY_LABELS: Record<string, string> = {
  LOW: '一般',
  MEDIUM: '常规',
  HIGH: '优先',
  EMERGENCY: '紧急',
}

export const STAGE_LABELS: Record<WorkflowStage, string> = {
  INTAKE: '正在了解情况',
  NEED_PROPERTY: '等待选择房屋',
  NEED_INFO: '等待补充信息',
  EMERGENCY_REVIEW: '紧急人工处理中',
  POLICY_CHECK: '正在核对服务规则',
  DUPLICATE_CHECK: '正在核对已有报修',
  EXISTING_TICKET: '已找到相关报修',
  CREATING_TICKET: '正在提交报修',
  RECONCILIATION_PENDING: '正在核实处理结果',
  FINDING_SLOTS: '正在查询上门时间',
  AWAITING_SLOT_CONFIRMATION: '等待选择上门时间',
  BOOKING: '正在确认预约',
  MONITORING_APPOINTMENT: '等待上门维修',
  RESCHEDULING: '正在调整预约',
  STATUS_CONFLICT: '需要物业核实',
  AWAITING_ACCEPTANCE: '等待确认维修结果',
  PLANNING_REWORK: '正在安排返工',
  HUMAN_REVIEW: '物业人工处理中',
  DONE: '本次处理已完成',
}

export const FIELD_LABELS: Record<string, string> = {
  PROPERTY: '需要维修的房屋',
  ISSUE_CATEGORY: '是哪类问题（漏水、用电或门锁）',
  ISSUE_LOCATION: '问题在哪个房间（例如卧室、厨房或卫生间，说明到房间即可）',
  ISSUE_DESCRIPTION: '具体出现了什么情况',
  AVAILABILITY: '您方便维修人员上门的时间',
}

export function threadTitle(item: ResidentThreadSummary): string {
  const category = item.issue_category
    ? (CATEGORY_LABELS[item.issue_category] ?? '维修服务')
    : '新报修会话'
  return item.issue_location ? `${category} · ${item.issue_location}` : category
}

export function residentStageLabel(stage: WorkflowStage): string {
  return STAGE_LABELS[stage]
}

export function progressLabel(thread: AgentThread | null, busy: boolean): string | null {
  if (!thread && busy) return '正在理解您的问题'
  if (!thread) return null
  if (thread.run_status === 'FAILED_SAFE') return '本次处理未能完成'
  if (thread.run_status === 'NEEDS_HUMAN_REVIEW') return '已转交物业工作人员'
  if (!busy && thread.run_status === 'COMPLETED') return null
  return residentStageLabel(thread.workflow_stage)
}
