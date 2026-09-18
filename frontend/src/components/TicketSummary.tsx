import { CalendarOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { Button, Card, Descriptions, Tag } from 'antd'
import { useEffect, useState } from 'react'
import {
  CATEGORY_LABELS,
  SEVERITY_LABELS,
  TICKET_STATUS_LABELS,
} from '../residentDisplay'
import type { Ticket } from '../types'

const appointmentDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'short',
})

const appointmentTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function appointmentParts(ticket: Ticket): { date: string; time: string } | null {
  if (!ticket.appointment) return null
  const start = new Date(ticket.appointment.scheduled_start)
  const end = new Date(ticket.appointment.scheduled_end)
  return {
    date: appointmentDateFormatter.format(start).replace('日周', '日 周'),
    time: `${appointmentTimeFormatter.format(start)}–${appointmentTimeFormatter.format(end)}`,
  }
}

export function TicketSummary({
  ticket,
  accepting = false,
  onAccept,
}: {
  ticket: Ticket
  accepting?: boolean
  onAccept?: () => void
}) {
  const [currentTime, setCurrentTime] = useState(() => Date.now())
  const scheduledStart = ticket.appointment?.scheduled_start
  useEffect(() => {
    if (!scheduledStart) return
    const startTime = new Date(scheduledStart).getTime()
    if (currentTime >= startTime) return
    const delay = Math.max(0, startTime - Date.now())
    const timer = window.setTimeout(
      () => setCurrentTime(Date.now()),
      Math.min(delay + 50, 2_147_483_647),
    )
    return () => window.clearTimeout(timer)
  }, [currentTime, scheduledStart])
  const appointment = appointmentParts(ticket)
  const appointmentHasStarted = ticket.appointment
    ? currentTime >= new Date(ticket.appointment.scheduled_start).getTime()
    : false
  const residentCanConfirm = (
    ['IN_PROGRESS', 'PENDING_ACCEPTANCE'].includes(ticket.ticket_status)
    || (ticket.ticket_status === 'SCHEDULED' && appointmentHasStarted)
  ) && Boolean(onAccept)
  const confirmationTitle = ticket.ticket_status === 'PENDING_ACCEPTANCE'
    ? '请确认维修结果'
    : '维修完成后请确认'

  return <Card title="当前工单" className="summary-card">
    {appointment && <div className="appointment-summary">
      <CalendarOutlined aria-hidden />
      <div>
        <span>预约上门</span>
        <strong>{appointment.date}</strong>
        <b>{appointment.time}</b>
      </div>
    </div>}
    <Descriptions className="ticket-summary-details" size="small" column={1}>
      <Descriptions.Item label="报修编号">{ticket.ticket_id.slice(0, 8).toUpperCase()}</Descriptions.Item>
      <Descriptions.Item label="问题类型">{CATEGORY_LABELS[ticket.issue_category] ?? '维修服务'}</Descriptions.Item>
      <Descriptions.Item label="位置">{ticket.issue_location}</Descriptions.Item>
      <Descriptions.Item label="当前进展"><Tag color="cyan">{TICKET_STATUS_LABELS[ticket.ticket_status] ?? '处理中'}</Tag></Descriptions.Item>
      <Descriptions.Item label="处理优先级">{SEVERITY_LABELS[ticket.severity] ?? '常规'}</Descriptions.Item>
    </Descriptions>
    {residentCanConfirm && <div className="repair-confirmation">
      <div className="repair-confirmation-copy">
        <CheckCircleOutlined aria-hidden />
        <div>
          <strong>{confirmationTitle}</strong>
        </div>
      </div>
      <Button block type="primary" loading={accepting} onClick={onAccept}>
        确认已修好
      </Button>
    </div>}
  </Card>
}
