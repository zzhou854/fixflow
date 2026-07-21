import { Button, Card, Form, Input, List, Space, Tag, Typography } from 'antd'
import type { Interrupt } from '../types'

export function InterruptPanel({ interrupt, onResume }: { interrupt: Interrupt; onResume: (body: object) => Promise<void> }) {
  if (interrupt.kind === 'NEED_INFORMATION') {
    return <Card title="需要补充信息">
      <Typography.Paragraph>{interrupt.message}</Typography.Paragraph>
      <Typography.Text type="secondary">缺失：{interrupt.missing_fields.join('、')}</Typography.Text>
      <Form onFinish={(value: { information: string }) => onResume({
        kind: 'PROVIDE_INFORMATION', intent_version: interrupt.intent_version,
        user_message: value.information, reference_time: new Date().toISOString(), timezone_name: 'Asia/Shanghai',
      })}>
        <Form.Item name="information" rules={[{ required: true }]}><Input.TextArea aria-label="补充信息" /></Form.Item>
        <Button htmlType="submit" type="primary">提交补充信息</Button>
      </Form>
    </Card>
  }
  if (interrupt.kind === 'DUPLICATE_TICKET_SELECTION') {
    return <Card title="发现可能重复的工单"><List dataSource={interrupt.tickets} renderItem={(ticket) => <List.Item actions={[
      <Button key="select" onClick={() => void onResume({ kind: 'SELECT_DUPLICATE_TICKET', intent_version: interrupt.intent_version, candidates_fingerprint: interrupt.candidates_fingerprint, ticket_id: ticket.ticket_id })}>选择已有工单</Button>,
    ]}><Space direction="vertical"><span>{ticket.ticket_id}</span><span>{ticket.issue_location} · {ticket.ticket_status}</span></Space></List.Item>} /></Card>
  }
  return <Card title="选择上门时间">
    <Typography.Paragraph type="secondary">候选时间不是预约保证，提交后仍会进行并发冲突校验。</Typography.Paragraph>
    <List dataSource={interrupt.slots} renderItem={(slot) => <List.Item actions={[
      <Button key="select" type="primary" onClick={() => void onResume({ kind: 'SELECT_APPOINTMENT_SLOT', intent_version: interrupt.intent_version, candidates_fingerprint: interrupt.candidates_fingerprint, rank: slot.rank })}>选择</Button>,
    ]}><Space direction="vertical"><span>{new Date(slot.scheduled_start).toLocaleString()}</span><span>维修人员 {slot.worker_id}</span><Tag>排名 {slot.rank}</Tag><Tag color="orange">booking_guaranteed=false</Tag></Space></List.Item>} />
  </Card>
}
