import { Alert, Button, Card, Form, Input, List, Space, Tag, Typography } from 'antd'
import { shanghaiReferenceTime } from '../api/client'
import { FIELD_LABELS, TICKET_STATUS_LABELS } from '../residentDisplay'
import type { Interrupt } from '../types'

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
              <span>上门服务时间：{new Date(slot.scheduled_start).toLocaleString('zh-CN')}</span>
              <Space>
                <Tag>推荐顺序 {slot.rank}</Tag>
                <Typography.Text type="secondary">
                  选择后系统会再次确认，成功后才算预约完成
                </Typography.Text>
              </Space>
            </Space>
          </List.Item>
        )}
      />
    </Card>
  )
}
