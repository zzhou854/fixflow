import { LogoutOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Drawer, Input, Layout, List, Select, Space, Table, Tabs, Tag,
  Typography, message as toast,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { DemoBanner } from '../components/DemoBanner'
import { TraceTimeline } from '../components/TraceTimeline'
import { ReconciliationCases } from '../components/ReconciliationCases'
import { RecoveryConsole } from '../components/RecoveryConsole'
import { HumanReviewQueue } from '../components/HumanReviewQueue'
import {
  CATEGORY_LABELS,
  SEVERITY_LABELS,
  STAGE_LABELS,
  TICKET_STATUS_LABELS,
} from '../residentDisplay'
import type { OperatorThread, Ticket, TicketDetail } from '../types'

const RUN_STATUS_LABELS: Record<OperatorThread['run_status'], string> = {
  COMPLETED: '已完成本轮处理',
  INTERRUPTED: '等待住户操作',
  NEEDS_HUMAN_REVIEW: '需要人工跟进',
  FAILED_SAFE: '自动处理未完成',
}

const INTENT_LABELS: Record<string, string> = {
  NEW_REPAIR: '新建报修',
  QUERY_STATUS: '查询进度',
  RESCHEDULE: '申请改期',
  REQUEST_HUMAN: '请求人工协助',
  CANCEL_TICKET: '取消工单',
  CANCEL_APPOINTMENT: '取消预约',
  ACCEPT_REPAIR: '确认维修结果',
  REJECT_REPAIR: '反馈仍需处理',
  UNKNOWN: '尚未识别',
}

const POLICY_LABELS: Record<string, string> = {
  SUFFICIENT: '依据充分',
  INSUFFICIENT: '依据不足',
  NOT_REQUIRED: '本轮无需核对',
  NOT_CHECKED: '尚未核对',
}

const APPOINTMENT_STATUS_LABELS: Record<string, string> = {
  BOOKED: '已预约',
  FULFILLED: '已上门',
  SUPERSEDED: '已改期',
  CANCELLED: '已取消',
  NO_SHOW: '未按时到场',
}

const WORKER_EVENT_LABELS: Record<string, string> = {
  ACCEPTED: '已接受安排',
  REJECTED: '无法接受本次安排',
  DEPARTED: '已出发',
  ARRIVED: '已到达',
  STARTED: '已开始维修',
  COMPLETED: '已完成本次维修',
  FAILED_TO_COMPLETE: '本次未能完成',
  CANCELLED: '已取消',
  NO_SHOW: '未按时到场',
}

function shortId(value: string): string {
  return value.slice(0, 8)
}

interface EscalationAttempt {
  ticketId: string
  idempotencyKey: string
  caseId: string | null
  status: string
}

export function OperatorPage() {
  const { token, user, logout } = useAuth()
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [threadId, setThreadId] = useState('')
  const [thread, setThread] = useState<OperatorThread | null>(null)
  const [status, setStatus] = useState<string>()
  const [category, setCategory] = useState<string>()
  const [severity, setSeverity] = useState<string>()
  const [escalation, setEscalation] = useState<EscalationAttempt | null>(null)

  const load = useCallback(async () => {
    if (!token) return
    const query = new URLSearchParams()
    if (status) query.set('ticket_status', status)
    if (category) query.set('issue_category', category)
    if (severity) query.set('severity', severity)
    try {
      setTickets((await api.operatorTickets(token, `?${query}`)).items)
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '加载失败')
    }
  }, [token, status, category, severity])

  useEffect(() => { void load() }, [load])

  async function openTicket(ticket: Ticket) {
    if (!token) return
    try { setDetail(await api.operatorTicket(token, ticket.ticket_id)) }
    catch (reason) { toast.error(reason instanceof ApiError ? reason.body.message : '详情加载失败') }
  }

  async function inspectThread() {
    if (!token || !threadId.trim()) return
    try { setThread(await api.operatorThread(token, threadId.trim())) }
    catch (reason) { toast.error(reason instanceof ApiError ? reason.body.message : '会话加载失败') }
  }

  async function escalateTicket() {
    if (!token || !detail) return
    const ticket = detail.ticket
    const previous = escalation?.ticketId === ticket.ticket_id ? escalation : null
    const idempotencyKey = previous?.idempotencyKey ?? crypto.randomUUID()
    try {
      const result = await api.operatorEscalate(token, ticket.ticket_id, ticket.version, idempotencyKey)
      if (result.reconciliation) {
        setEscalation({
          ticketId: ticket.ticket_id,
          idempotencyKey,
          caseId: result.reconciliation.case_id,
          status: result.reconciliation.status,
        })
        toast.warning('系统正在核对本次升级是否已经提交，请勿重复操作。')
        return
      }
      setEscalation(null)
      setDetail(await api.operatorTicket(token, ticket.ticket_id))
      await load()
      toast.success('工单已进入人工升级状态。')
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '人工升级失败')
    }
  }

  async function refreshEscalation() {
    if (!token || !escalation?.caseId) return
    try {
      const current = await api.reconciliationCase(token, escalation.caseId)
      setEscalation({ ...escalation, status: current.status })
      if (current.status === 'RESOLVED_COMMITTED' && detail) {
        setDetail(await api.operatorTicket(token, detail.ticket.ticket_id))
        await load()
      }
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '对账状态加载失败')
    }
  }

  const businessWorkspace = <>
    {token && <HumanReviewQueue token={token} />}
    <Card title="工单处理">
      <Space wrap>
        <Select aria-label="工单状态" allowClear placeholder="工单状态" onChange={setStatus} options={['OPEN','SCHEDULED','IN_PROGRESS','PENDING_ACCEPTANCE','REWORK_REQUIRED','ESCALATED','CANCELLED','CLOSED'].map((value) => ({ value, label: TICKET_STATUS_LABELS[value] ?? value }))} />
        <Select aria-label="故障类别" allowClear placeholder="问题类型" onChange={setCategory} options={['WATER_LEAK','ELECTRICAL','DOOR_LOCK'].map((value) => ({ value, label: CATEGORY_LABELS[value] ?? value }))} />
        <Select aria-label="严重程度" allowClear placeholder="处理优先级" onChange={setSeverity} options={['LOW','MEDIUM','HIGH','EMERGENCY'].map((value) => ({ value, label: SEVERITY_LABELS[value] ?? value }))} />
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
      </Space>
    </Card>
    <Table rowKey="ticket_id" dataSource={tickets} pagination={false} onRow={(record) => ({ onClick: () => void openTicket(record) })} columns={[
      { title: '工单号', dataIndex: 'ticket_id', render: (value: string) => shortId(value) },
      { title: '住户', dataIndex: 'resident_username' },
      { title: '房屋', dataIndex: 'property_label' },
      { title: '问题类型', dataIndex: 'issue_category', render: (value: string) => CATEGORY_LABELS[value] ?? '其他问题' },
      { title: '位置', dataIndex: 'issue_location' },
      { title: '优先级', dataIndex: 'severity', render: (value: string) => SEVERITY_LABELS[value] ?? '待确认' },
      { title: '处理进展', dataIndex: 'ticket_status', render: (value: string) => <Tag color="cyan">{TICKET_STATUS_LABELS[value] ?? '处理中'}</Tag> },
      { title: '上门时间', render: (_: unknown, row: Ticket) => row.appointment ? new Date(row.appointment.scheduled_start).toLocaleString() : '尚未安排' },
    ]} />
  </>

  const reviewWorkspace = <Card title="会话处理依据" size="small">
    <Alert
      type="info"
      showIcon
      message="这里只展示与正式工单关联的会话摘要"
      description="用于理解系统为何给出当前处理结果；查看不会改变住户会话或工单状态。"
    />
    <Space.Compact block className="thread-review-search">
      <Input aria-label="关联会话编号" placeholder="输入工单中记录的会话编号" value={threadId} onChange={(event) => setThreadId(event.target.value)} />
      <Button onClick={() => void inspectThread()}>查看处理依据</Button>
    </Space.Compact>
    {thread && <Descriptions column={1} bordered size="small" className="thread-review">
      <Descriptions.Item label="当前处理阶段">{STAGE_LABELS[thread.workflow_stage] ?? '处理中'}</Descriptions.Item>
      <Descriptions.Item label="本轮结果">{RUN_STATUS_LABELS[thread.run_status]}</Descriptions.Item>
      <Descriptions.Item label="住户诉求">{INTENT_LABELS[thread.task_intent] ?? '其他诉求'}</Descriptions.Item>
      <Descriptions.Item label="问题摘要">{CATEGORY_LABELS[thread.issue_category ?? ''] ?? '尚未识别'} · {thread.issue_location ?? '位置待补充'} · {SEVERITY_LABELS[thread.severity ?? ''] ?? '优先级待确认'}</Descriptions.Item>
      <Descriptions.Item label="服务依据">{POLICY_LABELS[thread.policy_sufficiency ?? 'NOT_CHECKED'] ?? '尚未核对'}</Descriptions.Item>
      <Descriptions.Item label="依据是否冲突">{thread.policy_conflict ? '存在冲突，需要人工判断' : '未发现冲突'}</Descriptions.Item>
      <Descriptions.Item label="依据摘要">{thread.policy_evidence_summary.join('；') || '暂无可展示摘要'}</Descriptions.Item>
      <Descriptions.Item label="人工跟进">{thread.human_review_required ? '需要' : '暂不需要'}</Descriptions.Item>
    </Descriptions>}
  </Card>

  const technicalWorkspace = <>
    <Alert
      type="warning"
      showIcon
      message="技术审计区"
      description="仅供排障与审计使用。这里的内部状态和执行记录不是住户可见的业务进展。"
    />
    {token && <ReconciliationCases token={token} />}
    {thread && token && <TraceTimeline token={token} threadId={thread.thread_id} />}
    {thread && token && <RecoveryConsole token={token} threadId={thread.thread_id} />}
    {!thread && <Card size="small"><Typography.Text type="secondary">请先在“处理依据”中打开一个已关联工单的会话。</Typography.Text></Card>}
  </>

  return <Layout className="app-shell">
    <DemoBanner />
    <Layout.Header className="app-header">
      <Typography.Title level={3}>物业工作台</Typography.Title>
      <Space><span>{user?.username}</span><Button icon={<LogoutOutlined />} onClick={logout}>退出</Button></Space>
    </Layout.Header>
    <Layout.Content className="operator-content">
      <Tabs
        className="operator-workspace-tabs"
        defaultActiveKey="business"
        items={[
          { key: 'business', label: '业务处理', children: businessWorkspace },
          { key: 'review', label: '处理依据', children: reviewWorkspace },
          { key: 'technical', label: '技术审计', children: technicalWorkspace },
        ]}
      />
      <Drawer width={640} title="工单详情" open={Boolean(detail)} onClose={() => setDetail(null)}>
        {detail && <Space direction="vertical" className="drawer-stack">
          <Descriptions column={1} bordered>
            <Descriptions.Item label="工单号">{detail.ticket.ticket_id}</Descriptions.Item>
            <Descriptions.Item label="住户">{detail.ticket.resident_username}</Descriptions.Item>
            <Descriptions.Item label="房屋">{detail.ticket.property_label}</Descriptions.Item>
            <Descriptions.Item label="问题类型/位置">{CATEGORY_LABELS[detail.ticket.issue_category] ?? '其他问题'} / {detail.ticket.issue_location}</Descriptions.Item>
            <Descriptions.Item label="描述">{detail.issue_description}</Descriptions.Item>
            <Descriptions.Item label="处理进展">{TICKET_STATUS_LABELS[detail.ticket.ticket_status] ?? '处理中'}</Descriptions.Item>
            <Descriptions.Item label="上门安排">{detail.ticket.appointment ? new Date(detail.ticket.appointment.scheduled_start).toLocaleString() : '尚未安排'}</Descriptions.Item>
            <Descriptions.Item label="维修人员最新动态">{detail.latest_worker_event ? (WORKER_EVENT_LABELS[detail.latest_worker_event.event_type] ?? '状态已更新') : '暂无动态'}</Descriptions.Item>
          </Descriptions>
          {detail.ticket.ticket_status !== 'ESCALATED' && <Card size="small" title="人工升级">
            <Space direction="vertical">
              {escalation?.ticketId === detail.ticket.ticket_id && <Alert
                type={escalation.status === 'MANUAL_REVIEW' ? 'warning' : 'info'}
                message={`对账状态：${escalation.status}`}
                description={escalation.status === 'RESOLVED_NOT_COMMITTED'
                  ? '权威证据确认上次未提交，可使用原请求正式重试。'
                  : '核对完成前不会自动重复升级。'}
              />}
              <Space>
                <Button
                  type="primary"
                  disabled={Boolean(escalation && escalation.ticketId === detail.ticket.ticket_id && escalation.status !== 'RESOLVED_NOT_COMMITTED')}
                  onClick={() => void escalateTicket()}
                >{escalation?.status === 'RESOLVED_NOT_COMMITTED' ? '使用原请求重试' : '人工升级'}</Button>
                {escalation?.caseId && <Button onClick={() => void refreshEscalation()}>刷新对账状态</Button>}
              </Space>
            </Space>
          </Card>}
          <Card size="small" title="工单处理记录"><List dataSource={detail.ticket_history} renderItem={(item) => <List.Item>{item.from_status ? (TICKET_STATUS_LABELS[item.from_status] ?? '处理中') : '创建工单'} → {TICKET_STATUS_LABELS[item.to_status] ?? '处理中'}</List.Item>} /></Card>
          <Card size="small" title="上门安排记录"><List dataSource={detail.appointment_history} renderItem={(item) => <List.Item>{item.from_status ? (APPOINTMENT_STATUS_LABELS[item.from_status] ?? '已更新') : '创建预约'} → {APPOINTMENT_STATUS_LABELS[item.to_status] ?? '已更新'}</List.Item>} /></Card>
          <Alert message="会话处理依据仅在系统已确认与该工单关联时开放；未创建工单的异常请求请在“待人工处理”中查看。" />
        </Space>}
      </Drawer>
    </Layout.Content>
  </Layout>
}
