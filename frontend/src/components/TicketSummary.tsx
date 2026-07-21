import { Card, Descriptions, Tag } from 'antd'
import type { Ticket } from '../types'

export function TicketSummary({ ticket }: { ticket: Ticket }) {
  return <Card title="当前工单" className="summary-card">
    <Descriptions size="small" column={1}>
      <Descriptions.Item label="工单号">{ticket.ticket_id}</Descriptions.Item>
      <Descriptions.Item label="故障类别">{ticket.issue_category}</Descriptions.Item>
      <Descriptions.Item label="位置">{ticket.issue_location}</Descriptions.Item>
      <Descriptions.Item label="状态"><Tag color="cyan">{ticket.ticket_status}</Tag></Descriptions.Item>
      <Descriptions.Item label="Severity">{ticket.severity}</Descriptions.Item>
      <Descriptions.Item label="返工次数">{ticket.rework_count}</Descriptions.Item>
      <Descriptions.Item label="人工升级">{ticket.ticket_status === 'ESCALATED' ? '是' : '否'}</Descriptions.Item>
      <Descriptions.Item label="当前预约">{ticket.appointment ? new Date(ticket.appointment.scheduled_start).toLocaleString() : '暂无'}</Descriptions.Item>
    </Descriptions>
  </Card>
}
