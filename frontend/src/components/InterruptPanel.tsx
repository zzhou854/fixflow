import { Alert, Button, Card, Form, Input, List, Space, Tag, Typography } from 'antd'
import { shanghaiReferenceTime } from '../api/client'
import type { Interrupt } from '../types'

const FIELD_LABELS: Record<string, string> = {
  PROPERTY: '需要维修的房屋',
  ISSUE_CATEGORY: '故障类型（漏水、电气或门锁）',
  ISSUE_LOCATION: '故障发生的位置（例如厨房、卫生间、客厅或入户门）',
  ISSUE_DESCRIPTION: '具体的故障现象',
  AVAILABILITY: '方便维修人员上门的时间',
}

function missingFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? '必要的报修信息'
}

export function InterruptPanel({
  interrupt,
  onResume,
}: {
  interrupt: Interrupt
  onResume: (body: object) => Promise<void>
}) {
  if (interrupt.kind === 'NEED_INFORMATION') {
    const labels = interrupt.missing_fields.map(missingFieldLabel)
    return (
      <Card className="interrupt-card" title="还需要补充一点信息">
        <Alert
          showIcon
          type="warning"
          message={`请告诉我：${labels.join('、')}`}
          description="不用填写代码或英文，像平时聊天一样描述即可。"
        />
        <Typography.Paragraph className="interrupt-explanation">
          {interrupt.message}
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
              placeholder="例如：漏水位置在厨房水槽下方"
            />
          </Form.Item>
          <Button htmlType="submit" type="primary">
            提交补充信息
          </Button>
        </Form>
      </Card>
    )
  }

  if (interrupt.kind === 'DUPLICATE_TICKET_SELECTION') {
    return (
      <Card className="interrupt-card" title="发现可能重复的工单">
        <Alert
          showIcon
          type="info"
          message="请先确认是否是同一个问题"
          description="为避免重复派单，当前不能强制新建工单。"
        />
        <List
          dataSource={interrupt.tickets}
          renderItem={(ticket) => (
            <List.Item
              actions={[
                <Button
                  key="select"
                  onClick={() =>
                    void onResume({
                      kind: 'SELECT_DUPLICATE_TICKET',
                      intent_version: interrupt.intent_version,
                      candidates_fingerprint: interrupt.candidates_fingerprint,
                      ticket_id: ticket.ticket_id,
                    })
                  }
                >
                  选择已有工单
                </Button>,
              ]}
            >
              <Space direction="vertical">
                <span>{ticket.ticket_id}</span>
                <span>
                  {ticket.issue_location} · {ticket.ticket_status}
                </span>
              </Space>
            </List.Item>
          )}
        />
      </Card>
    )
  }

  return (
    <Card className="interrupt-card" title="选择上门时间">
      <Alert
        showIcon
        type="info"
        message="请选择一个方便的候选时间"
        description="候选时间并非预约保证；提交后系统还会进行并发冲突校验。"
      />
      <List
        dataSource={interrupt.slots}
        renderItem={(slot) => (
          <List.Item
            actions={[
              <Button
                key="select"
                type="primary"
                onClick={() =>
                  void onResume({
                    kind: 'SELECT_APPOINTMENT_SLOT',
                    intent_version: interrupt.intent_version,
                    candidates_fingerprint: interrupt.candidates_fingerprint,
                    rank: slot.rank,
                  })
                }
              >
                选择
              </Button>,
            ]}
          >
            <Space direction="vertical">
              <span>{new Date(slot.scheduled_start).toLocaleString()}</span>
              <span>维修人员 {slot.worker_id}</span>
              <Space>
                <Tag>排名 {slot.rank}</Tag>
                <Tag color="orange">booking_guaranteed=false</Tag>
                <Typography.Text type="secondary">提交后确认预约</Typography.Text>
              </Space>
            </Space>
          </List.Item>
        )}
      />
    </Card>
  )
}
