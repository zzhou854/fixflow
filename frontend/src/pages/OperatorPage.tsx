import { LogoutOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Drawer, Input, Layout, List, Select, Space, Table, Tag,
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
  TICKET_STATUS_LABELS,
} from '../residentDisplay'
import type { OperatorThread, Ticket, TicketDetail } from '../types'

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

  return <Layout className="app-shell">
    <DemoBanner />
    <Layout.Header className="app-header">
      <Typography.Title level={3}>物业工作台</Typography.Title>
      <Space><span>{user?.username}</span><Button icon={<LogoutOutlined />} onClick={logout}>退出</Button></Space>
    </Layout.Header>
    <Layout.Content className="operator-content">
      {token && <HumanReviewQueue token={token} />}
      {token && <ReconciliationCases token={token} />}
      <Card title="工单工作区"><Space wrap>
        <Select aria-label="工单状态" allowClear placeholder="工单状态" onChange={setStatus} options={['OPEN','SCHEDULED','IN_PROGRESS','PENDING_ACCEPTANCE','REWORK_REQUIRED','ESCALATED','CANCELLED','CLOSED'].map((value) => ({ value, label: TICKET_STATUS_LABELS[value] ?? value }))} />
        <Select aria-label="故障类别" allowClear placeholder="故障类别" onChange={setCategory} options={['WATER_LEAK','ELECTRICAL','DOOR_LOCK'].map((value) => ({ value, label: CATEGORY_LABELS[value] ?? value }))} />
        <Select aria-label="严重程度" allowClear placeholder="处理优先级" onChange={setSeverity} options={['LOW','MEDIUM','HIGH','EMERGENCY'].map((value) => ({ value, label: SEVERITY_LABELS[value] ?? value }))} />
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
      </Space></Card>
      <Card title="已知 Agent Thread 审查" size="small">
        <Space.Compact block>
          <Input aria-label="Agent Thread ID" placeholder="输入已知 thread_id（不会按工单猜测）" value={threadId} onChange={(event) => setThreadId(event.target.value)} />
          <Button onClick={() => void inspectThread()}>查看会话</Button>
        </Space.Compact>
        {thread && <Descriptions column={1} bordered size="small" className="thread-review">
          <Descriptions.Item label="Workflow Stage">{thread.workflow_stage}</Descriptions.Item>
          <Descriptions.Item label="Run Status">{thread.run_status}</Descriptions.Item>
          <Descriptions.Item label="Task Intent">{thread.task_intent}</Descriptions.Item>
          <Descriptions.Item label="结构化故障">{thread.issue_category ?? '未提取'} / {thread.issue_location ?? '未提取'} / {thread.severity ?? '未确定'}</Descriptions.Item>
          <Descriptions.Item label="Policy Sufficiency">{thread.policy_sufficiency ?? '未检索'}</Descriptions.Item>
          <Descriptions.Item label="Policy Conflict">{thread.policy_conflict ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="Policy Evidence 摘要">{thread.policy_evidence_summary.join(', ') || '暂无'}</Descriptions.Item>
          <Descriptions.Item label="人工审查">{thread.human_review_required ? '需要' : '否'}</Descriptions.Item>
        </Descriptions>}
        {thread && token && <TraceTimeline token={token} threadId={thread.thread_id} />}
        {thread && token && <RecoveryConsole token={token} threadId={thread.thread_id} />}
      </Card>
      <Table rowKey="ticket_id" dataSource={tickets} pagination={false} onRow={(record) => ({ onClick: () => void openTicket(record) })} columns={[
        { title: '工单', dataIndex: 'ticket_id', ellipsis: true },
        { title: '住户', dataIndex: 'resident_username' },
        { title: '房屋', dataIndex: 'property_label' },
        { title: '类别', dataIndex: 'issue_category', render: (value: string) => CATEGORY_LABELS[value] ?? value },
        { title: '位置', dataIndex: 'issue_location' },
        { title: '优先级', dataIndex: 'severity', render: (value: string) => SEVERITY_LABELS[value] ?? value },
        { title: '状态', dataIndex: 'ticket_status', render: (value: string) => <Tag color="cyan">{TICKET_STATUS_LABELS[value] ?? value}</Tag> },
        { title: '预约', render: (_, row: Ticket) => row.appointment ? new Date(row.appointment.scheduled_start).toLocaleString() : '暂无' },
      ]} />
      <Drawer width={640} title="工单详情" open={Boolean(detail)} onClose={() => setDetail(null)}>
        {detail && <Space direction="vertical" className="drawer-stack">
          <Descriptions column={1} bordered>
            <Descriptions.Item label="工单号">{detail.ticket.ticket_id}</Descriptions.Item>
            <Descriptions.Item label="住户">{detail.ticket.resident_username}</Descriptions.Item>
            <Descriptions.Item label="房屋">{detail.ticket.property_label}</Descriptions.Item>
            <Descriptions.Item label="类别/位置">{detail.ticket.issue_category} / {detail.ticket.issue_location}</Descriptions.Item>
            <Descriptions.Item label="描述">{detail.issue_description}</Descriptions.Item>
            <Descriptions.Item label="状态">{detail.ticket.ticket_status}</Descriptions.Item>
            <Descriptions.Item label="预约">{detail.ticket.appointment ? `${detail.ticket.appointment.status} · ${new Date(detail.ticket.appointment.scheduled_start).toLocaleString()}` : '暂无'}</Descriptions.Item>
            <Descriptions.Item label="最新 Worker Event">{detail.latest_worker_event?.event_type ?? '暂无'}</Descriptions.Item>
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
          <Card size="small" title="工单状态历史"><List dataSource={detail.ticket_history} renderItem={(item) => <List.Item>{item.from_status ?? '创建'} → {item.to_status} · {item.action}</List.Item>} /></Card>
          <Card size="small" title="预约状态历史"><List dataSource={detail.appointment_history} renderItem={(item) => <List.Item>{item.from_status ?? '创建'} → {item.to_status}</List.Item>} /></Card>
          <Alert message="仅可审查 Agent State 中已关联工单且经数据库快照复核的会话；未建工单的人工审查会话首版不进入物业待办。" />
          <Alert type="info" message="执行 Trace 请通过上方已关联 Thread 审查；业务状态历史不会冒充 Agent Trace。" />
        </Space>}
      </Drawer>
    </Layout.Content>
  </Layout>
}
