import { Card, Descriptions, Tag } from 'antd'
import {
  CATEGORY_LABELS,
  SEVERITY_LABELS,
  TICKET_STATUS_LABELS,
} from '../residentDisplay'
import type { Ticket } from '../types'

export function TicketSummary({ ticket }: { ticket: Ticket }) {
  return <Card title="当前工单" className="summary-card">
    <Descriptions size="small" column={1}>
      <Descriptions.Item label="报修编号">{ticket.ticket_id.slice(0, 8).toUpperCase()}</Descriptions.Item>
      <Descriptions.Item label="问题类型">{CATEGORY_LABELS[ticket.issue_category] ?? '维修服务'}</Descriptions.Item>
      <Descriptions.Item label="位置">{ticket.issue_location}</Descriptions.Item>
      <Descriptions.Item label="当前进展"><Tag color="cyan">{TICKET_STATUS_LABELS[ticket.ticket_status] ?? '处理中'}</Tag></Descriptions.Item>
      <Descriptions.Item label="处理优先级">{SEVERITY_LABELS[ticket.severity] ?? '常规'}</Descriptions.Item>
      <Descriptions.Item label="返工次数">{ticket.rework_count}</Descriptions.Item>
      <Descriptions.Item label="物业人工处理">{ticket.ticket_status === 'ESCALATED' ? '是' : '否'}</Descriptions.Item>
      <Descriptions.Item label="当前预约">{ticket.appointment ? new Date(ticket.appointment.scheduled_start).toLocaleString() : '暂无'}</Descriptions.Item>
    </Descriptions>
  </Card>
}
