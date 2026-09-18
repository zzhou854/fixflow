import { Button, Card, Form, Input, List, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { shanghaiReferenceTime } from '../api/client'
import { FIELD_LABELS, TICKET_STATUS_LABELS } from '../residentDisplay'
import type { Interrupt } from '../types'

const slotDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: 'long',
  day: 'numeric',
  weekday: 'short',
})

const slotTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function missingFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? '必要的报修信息'
}

function slotParts(startValue: string, endValue: string) {
  const start = new Date(startValue)
  const end = new Date(endValue)
  return {
    date: slotDateFormatter.format(start).replace('日周', '日 周'),
    time: `${slotTimeFormatter.format(start)}–${slotTimeFormatter.format(end)}`,
  }
}

export function InterruptPanel({
  interrupt,
  onResume,
  busy = false,
}: {
  interrupt: Interrupt
  onResume: (body: object) => Promise<void>
  busy?: boolean
}) {
  const [selectedRank, setSelectedRank] = useState<number | null>(null)

  if (interrupt.kind === 'NEED_INFORMATION') {
    const labels = interrupt.missing_fields.map(missingFieldLabel)
    return (
      <Card className="interrupt-card information-card" title="还需要补充一点信息">
        <Typography.Paragraph>
          请告诉我：<strong>{labels.join('、')}</strong>
        </Typography.Paragraph>
        <Form
          onFinish={(value: { information: string }) =>
            onResume({
              kind: 'PROVIDE_INFORMATION',
              intent_version: interrupt.intent_version,
              user_message: value.information,
              reference_time: shanghaiReferenceTime(),
              timezone_name: 'Asia/Shanghai',
            })
          }
        >
          <Form.Item
            name="information"
            rules={[{ required: true, message: '请填写需要补充的信息' }]}
          >
            <Input.TextArea
              aria-label="补充信息"
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="像平时聊天一样描述即可"
            />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={busy}>
            提交补充信息
          </Button>
        </Form>
      </Card>
    )
  }

  if (interrupt.kind === 'DUPLICATE_TICKET_SELECTION') {
    return (
      <Card className="interrupt-card" title="发现可能重复的工单">
        <Typography.Paragraph type="secondary">
          请确认是否是同一个问题，避免物业重复派单。
        </Typography.Paragraph>
        <List
          dataSource={interrupt.tickets}
          renderItem={(ticket) => (
            <List.Item
              actions={[
                <Button
                  key="select"
                  disabled={busy}
                  onClick={() =>
                    void onResume({
                      kind: 'SELECT_DUPLICATE_TICKET',
                      intent_version: interrupt.intent_version,
                      candidates_fingerprint: interrupt.candidates_fingerprint,
                      ticket_id: ticket.ticket_id,
                    })
                  }
                >
                  使用这张工单
                </Button>,
              ]}
            >
              <Space direction="vertical">
                <span>
                  {ticket.issue_location} ·{' '}
                  {TICKET_STATUS_LABELS[ticket.ticket_status] ?? '处理中'}
                </span>
              </Space>
            </List.Item>
          )}
        />
      </Card>
    )
  }

  return (
    <Card
      className="interrupt-card slot-picker-card"
      title="选择上门时间"
      extra={
        <Button
          type="text"
          disabled={busy || selectedRank !== null}
          onClick={() =>
            void onResume({
              kind: 'CANCEL_APPOINTMENT_SLOT_SELECTION',
              intent_version: interrupt.intent_version,
              candidates_fingerprint: interrupt.candidates_fingerprint,
            })
          }
        >
          暂不选择
        </Button>
      }
    >
      <List
        className="slot-list"
        dataSource={interrupt.slots}
        renderItem={(slot) => {
          const value = slotParts(slot.scheduled_start, slot.scheduled_end)
          return (
            <List.Item className="slot-row">
              <div className="slot-time">
                <span>{value.date}</span>
                <strong>{value.time}</strong>
              </div>
              <Space>
                {slot.rank === 1
                  ? <Tag color="green">推荐</Tag>
                  : <Tag>备选 {slot.rank}</Tag>}
                <Button
                  type={slot.rank === 1 ? 'primary' : 'default'}
                  loading={selectedRank === slot.rank}
                  disabled={busy || selectedRank !== null}
                  onClick={() => {
                    setSelectedRank(slot.rank)
                    void onResume({
                      kind: 'SELECT_APPOINTMENT_SLOT',
                      intent_version: interrupt.intent_version,
                      candidates_fingerprint: interrupt.candidates_fingerprint,
                      rank: slot.rank,
                    }).finally(() => setSelectedRank(null))
                  }}
                >
                  选这个时间
                </Button>
              </Space>
            </List.Item>
          )
        }}
      />
    </Card>
  )
}
