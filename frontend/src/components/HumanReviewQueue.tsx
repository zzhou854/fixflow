import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  EyeOutlined,
  ReloadOutlined,
  UserSwitchOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Segmented,
  Space,
  Tag,
  Typography,
  message as toast,
} from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  HumanReviewCase,
  HumanReviewEvent,
  HumanReviewSafetyLevel,
  HumanReviewStatus,
} from '../types'

const STATUS_LABELS: Record<HumanReviewStatus, string> = {
  OPEN: '待处理',
  CLAIMED: '处理中',
  RESOLVED: '已解决',
  DISMISSED: '已关闭',
}

const SAFETY_LABELS: Record<HumanReviewSafetyLevel, string> = {
  STANDARD: '常规',
  ELEVATED: '需优先关注',
  EMERGENCY: '紧急',
}

const STAGE_LABELS: Record<string, string> = {
  INTERPRETATION: '问题理解未完成',
  PROPERTY_AUTHORIZATION: '房屋权限需要核实',
  SAFETY_REVIEW: '安全风险需要人工判断',
  POLICY_REVIEW: '服务规则或证据需要核实',
  DUPLICATE_CHECK: '重复报修需要确认',
  SCHEDULING: '上门安排需要协助',
  MUTATION_RECONCILIATION: '系统操作结果需要核实',
  UNSUPPORTED_REQUEST: '当前请求暂不支持自动处理',
}

const REASON_LABELS: Record<string, string> = {
  RESIDENT_MANUAL_REQUEST: '住户主动申请物业协助',
  SAFETY_REVIEW_REQUIRED: '存在需要物业核实的安全风险',
  UNSUPPORTED_AUTOMATION: '当前事项暂不支持自动办理',
  PROPERTY_CONTEXT_REQUIRED: '需要核实住户与房屋关系',
  POLICY_EVIDENCE_INSUFFICIENT: '现有服务依据不足，需要人工判断',
  POLICY_CONFLICT: '服务依据存在冲突，需要人工判断',
}

function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? '需要物业工作人员进一步核实'
}

function safeError(reason: unknown): string {
  return reason instanceof ApiError ? reason.body.message : '人工处理队列暂时不可用。'
}

function safetyColor(level: HumanReviewSafetyLevel): string {
  if (level === 'EMERGENCY') return 'red'
  if (level === 'ELEVATED') return 'orange'
  return 'blue'
}

export function HumanReviewQueue({ token }: { token: string }) {
  const [items, setItems] = useState<HumanReviewCase[]>([])
  const [status, setStatus] = useState<HumanReviewStatus>('OPEN')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<HumanReviewCase | null>(null)
  const [events, setEvents] = useState<HumanReviewEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [transitioning, setTransitioning] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await api.humanReviewCases(token, status, 100, 0)
      setItems(page.items)
      setSelected((current) =>
        current
          ? page.items.find((item) => item.case_id === current.case_id) ?? current
          : null,
      )
    } catch (reason) {
      toast.error(safeError(reason))
    } finally {
      setLoading(false)
    }
  }, [status, token])

  useEffect(() => {
    void load()
  }, [load])

  const visible = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase()
    return [...items]
      .filter((item) => {
        if (!keyword) return true
        return `${item.summary} ${item.reason_code} ${item.failure_stage}`
          .toLocaleLowerCase()
          .includes(keyword)
      })
      .sort(
        (left, right) =>
          right.priority - left.priority ||
          right.safety_level.localeCompare(left.safety_level) ||
          left.created_at.localeCompare(right.created_at),
      )
  }, [items, search])

  async function inspect(item: HumanReviewCase) {
    setSelected(item)
    try {
      setEvents((await api.humanReviewEvents(token, item.case_id)).items)
    } catch (reason) {
      setEvents([])
      toast.error(safeError(reason))
    }
  }

  async function transition(
    item: HumanReviewCase,
    target: HumanReviewStatus,
    resolutionCode?: string,
    resolutionNote?: string,
  ) {
    setTransitioning(true)
    try {
      const updated = await api.transitionHumanReview(
        token,
        item.case_id,
        item.version,
        target,
        resolutionCode,
        resolutionNote,
      )
      setSelected(updated)
      await Promise.all([load(), inspect(updated)])
      toast.success(`人工任务已更新为“${STATUS_LABELS[target]}”。`)
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        toast.warning('该任务已被其他人更新，列表已为您刷新。')
        await load()
      } else {
        toast.error(safeError(reason))
      }
    } finally {
      setTransitioning(false)
    }
  }

  function resolve(item: HumanReviewCase, target: 'RESOLVED' | 'DISMISSED') {
    let note = ''
    Modal.confirm({
      title: target === 'RESOLVED' ? '确认问题已经处理？' : '确认关闭这条任务？',
      content: (
        <Input.TextArea
          aria-label="处理说明"
          placeholder="请填写简短的处理说明（可选）"
          maxLength={2000}
          onChange={(event) => {
            note = event.target.value
          }}
        />
      ),
      okText: target === 'RESOLVED' ? '标记已解决' : '关闭任务',
      cancelText: '取消',
      onOk: () =>
        transition(
          item,
          target,
          target === 'RESOLVED' ? 'OPERATOR_RESOLVED' : 'OPERATOR_DISMISSED',
          note.trim() || undefined,
        ),
    })
  }

  return (
    <Card
      className="human-review-queue"
      title={
        <Space>
          <span>待人工处理</span>
          <Tag color={visible.length > 0 ? 'orange' : 'default'}>{visible.length}</Tag>
        </Space>
      }
      extra={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
    >
      <div className="review-toolbar">
        <Segmented
          value={status}
          options={[
            { label: '待处理', value: 'OPEN' },
            { label: '处理中', value: 'CLAIMED' },
            { label: '已解决', value: 'RESOLVED' },
            { label: '已关闭', value: 'DISMISSED' },
          ]}
          onChange={(value) => setStatus(value as HumanReviewStatus)}
        />
        <Input.Search
          allowClear
          value={search}
          placeholder="搜索摘要或原因"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {visible.length === 0 && !loading ? (
        <Empty description="当前没有此类人工任务" />
      ) : (
        <List
          loading={loading}
          className="review-case-list"
          dataSource={visible}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="inspect"
                  icon={<EyeOutlined />}
                  onClick={() => void inspect(item)}
                >
                  查看
                </Button>,
                item.status === 'OPEN' ? (
                  <Button
                    key="claim"
                    type="primary"
                    icon={<UserSwitchOutlined />}
                    onClick={() => void transition(item, 'CLAIMED')}
                  >
                    领取
                  </Button>
                ) : null,
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={
                  <Space wrap>
                    <span>{item.summary}</span>
                    <Tag color={safetyColor(item.safety_level)}>
                      {SAFETY_LABELS[item.safety_level]}
                    </Tag>
                    <Tag>{STATUS_LABELS[item.status]}</Tag>
                  </Space>
                }
                description={
                  <span>
                    {STAGE_LABELS[item.failure_stage] ?? '需要人工核实'} · 优先级 {item.priority} ·{' '}
                    {new Date(item.created_at).toLocaleString('zh-CN')}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      )}
      <Drawer
        width={680}
        title="人工任务详情"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
      >
        {selected && (
          <Space direction="vertical" size="middle" className="drawer-stack">
            <Card size="small" title="业务摘要">
              <Typography.Paragraph>{selected.summary}</Typography.Paragraph>
              <Space wrap>
                <Tag color={safetyColor(selected.safety_level)}>
                  {SAFETY_LABELS[selected.safety_level]}
                </Tag>
                <Tag>{STATUS_LABELS[selected.status]}</Tag>
                <Tag>优先级 {selected.priority}</Tag>
              </Space>
            </Card>
            <Card size="small" title="处理依据">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="需要人工的环节">
                  {STAGE_LABELS[selected.failure_stage] ?? '需要人工核实'}
                </Descriptions.Item>
                <Descriptions.Item label="业务原因">
                  {reasonLabel(selected.reason_code)}
                </Descriptions.Item>
                <Descriptions.Item label="关联工单">
                  {selected.ticket_id ?? '尚未创建工单'}
                </Descriptions.Item>
                <Descriptions.Item label="当前负责人">
                  {selected.assigned_operator_id ? '已由物业人员领取' : '尚未领取'}
                </Descriptions.Item>
              </Descriptions>
              {!selected.ticket_id && (
                <Alert
                  showIcon
                  type="info"
                  message="这是工单创建前的人工任务，系统不会为可见性伪造工单。"
                />
              )}
            </Card>
            <Card size="small" title="处理操作">
              <Space wrap>
                {selected.status === 'OPEN' && (
                  <Button
                    type="primary"
                    loading={transitioning}
                    onClick={() => void transition(selected, 'CLAIMED')}
                  >
                    领取任务
                  </Button>
                )}
                {selected.status === 'CLAIMED' && (
                  <>
                    <Button
                      loading={transitioning}
                      onClick={() => void transition(selected, 'OPEN')}
                    >
                      释放任务
                    </Button>
                    <Button
                      type="primary"
                      icon={<CheckCircleOutlined />}
                      onClick={() => resolve(selected, 'RESOLVED')}
                    >
                      标记已解决
                    </Button>
                    <Button
                      danger
                      icon={<CloseCircleOutlined />}
                      onClick={() => resolve(selected, 'DISMISSED')}
                    >
                      关闭任务
                    </Button>
                  </>
                )}
              </Space>
            </Card>
            <Card size="small" title="技术审计" className="technical-audit-card">
              <details>
                <summary>展开处理记录与内部标识</summary>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="任务 ID">{selected.case_id}</Descriptions.Item>
                  <Descriptions.Item label="会话 ID">{selected.thread_id}</Descriptions.Item>
                  <Descriptions.Item label="失败阶段">{selected.failure_stage}</Descriptions.Item>
                  <Descriptions.Item label="原因代码">{selected.reason_code}</Descriptions.Item>
                  <Descriptions.Item label="最近错误">{selected.last_error_code ?? '无'}</Descriptions.Item>
                  <Descriptions.Item label="版本">{selected.version}</Descriptions.Item>
                </Descriptions>
                <List
                  size="small"
                  dataSource={events}
                  locale={{ emptyText: '暂无状态变更记录' }}
                  renderItem={(event) => (
                    <List.Item>
                      {event.from_status ?? '创建'} → {event.to_status} ·{' '}
                      {new Date(event.occurred_at).toLocaleString('zh-CN')}
                    </List.Item>
                  )}
                />
              </details>
            </Card>
          </Space>
        )}
      </Drawer>
    </Card>
  )
}
