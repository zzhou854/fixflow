import { LogoutOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Layout,
  List,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message as toast,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, shanghaiReferenceTime } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { DemoBanner } from '../components/DemoBanner'
import { InterruptPanel } from '../components/InterruptPanel'
import { TicketSummary } from '../components/TicketSummary'
import { useSSE } from '../hooks/useSSE'
import type { AgentThread, Property, ResidentThreadSummary } from '../types'

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

const CATEGORY_LABELS: Record<string, string> = {
  WATER_LEAK: '漏水报修',
  ELECTRICAL: '电气报修',
  DOOR_LOCK: '门锁报修',
}

function conversation(thread: AgentThread): ChatMessage[] {
  return (thread.conversation_messages ?? []).map((item) => ({
    role: item.role === 'USER' ? 'user' : 'assistant',
    text: item.content,
  }))
}

function threadTitle(item: ResidentThreadSummary): string {
  const category = item.issue_category
    ? (CATEGORY_LABELS[item.issue_category] ?? item.issue_category)
    : '新报修会话'
  return item.issue_location ? `${category} · ${item.issue_location}` : category
}

export function ResidentPage() {
  const { token, user, logout } = useAuth()
  const [properties, setProperties] = useState<Property[]>([])
  const [propertyId, setPropertyId] = useState<string>()
  const [threads, setThreads] = useState<ResidentThreadSummary[]>([])
  const [thread, setThread] = useState<AgentThread | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = useCallback((next: AgentThread) => {
    setThread(next)
    setMessages(conversation(next))
    sessionStorage.setItem('fixflow.demo.thread_id', next.thread_id)
  }, [])

  const refreshThreads = useCallback(async () => {
    if (!token) return
    const result = await api.residentThreads(token)
    setThreads(result.items)
  }, [token])

  const reconcile = useCallback(
    async (threadId: string) => {
      if (!token) return
      apply(await api.getThread(token, threadId))
      await refreshThreads()
    },
    [apply, refreshThreads, token],
  )
  const sse = useSSE(thread?.thread_id ?? null, token, reconcile)
  const reconciliationBlocked = ['PENDING', 'PROCESSING', 'MANUAL_REVIEW'].includes(
    thread?.reconciliation?.status ?? '',
  )
  const awaitingInformation = thread?.interrupt?.kind === 'NEED_INFORMATION'
  const awaitingSelection =
    thread?.interrupt?.kind === 'DUPLICATE_TICKET_SELECTION' ||
    thread?.interrupt?.kind === 'APPOINTMENT_SLOT_SELECTION'

  useEffect(() => {
    if (!token) return
    void Promise.all([api.properties(token), api.residentThreads(token)]).then(
      ([propertyItems, threadResult]) => {
        setProperties(propertyItems)
        setPropertyId(propertyItems[0]?.property_id)
        setThreads(threadResult.items)
      },
    )
  }, [token])

  useEffect(() => {
    const saved = sessionStorage.getItem('fixflow.demo.thread_id')
    if (!token || !saved) return
    void api
      .getThread(token, saved)
      .then(apply)
      .catch(() => sessionStorage.removeItem('fixflow.demo.thread_id'))
  }, [apply, token])

  const selectThread = async (threadId: string) => {
    if (!token) return
    setBusy(true)
    try {
      apply(await api.getThread(token, threadId))
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '会话加载失败')
    } finally {
      setBusy(false)
    }
  }

  const resume = async (body: object) => {
    if (!token || !thread) return
    setBusy(true)
    try {
      apply(await api.resume(token, thread.thread_id, body))
      await refreshThreads()
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '补充信息提交失败')
    } finally {
      setBusy(false)
    }
  }

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!token || !propertyId || !trimmed || awaitingSelection) return
    setMessages((current) => [...current, { role: 'user', text: trimmed }])
    setBusy(true)
    setInput('')
    try {
      let next: AgentThread
      if (thread && awaitingInformation) {
        next = await api.resume(token, thread.thread_id, {
          kind: 'PROVIDE_INFORMATION',
          intent_version: thread.interrupt!.intent_version,
          user_message: trimmed,
          reference_time: shanghaiReferenceTime(),
          timezone_name: 'Asia/Shanghai',
        })
      } else if (thread) {
        next = await api.sendMessage(token, thread.thread_id, trimmed)
      } else {
        next = await api.createThread(token, propertyId, trimmed)
      }
      apply(next)
      await refreshThreads()
    } catch (reason) {
      toast.error(reason instanceof ApiError ? reason.body.message : '请求失败')
    } finally {
      setBusy(false)
    }
  }

  const newThread = () => {
    sessionStorage.removeItem('fixflow.demo.thread_id')
    setThread(null)
    setMessages([])
    setInput('')
  }

  return (
    <Layout className="app-shell">
      <DemoBanner />
      <Layout.Header className="app-header">
        <Typography.Title level={3}>住户维修助手</Typography.Title>
        <Space>
          <Tag color={sse === 'connected' ? 'green' : 'default'}>SSE {sse}</Tag>
          <span>{user?.username}</span>
          <Button icon={<LogoutOutlined />} onClick={logout}>
            退出
          </Button>
        </Space>
      </Layout.Header>
      <Layout.Content className="resident-grid">
        <aside>
          <Card title="服务房屋">
            <Select
              aria-label="房屋选择"
              value={propertyId}
              onChange={setPropertyId}
              options={properties.map((item) => ({
                value: item.property_id,
                label: item.address_text,
              }))}
            />
            <Button block icon={<PlusOutlined />} onClick={newThread}>
              新建会话
            </Button>
          </Card>
          <Card title="历史会话" className="thread-history-card">
            {threads.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有历史会话" />
            ) : (
              <List
                size="small"
                dataSource={threads}
                renderItem={(item) => (
                  <List.Item>
                    <button
                      type="button"
                      className={`thread-history-item ${
                        item.thread_id === thread?.thread_id ? 'active' : ''
                      }`}
                      onClick={() => void selectThread(item.thread_id)}
                    >
                      <strong>{threadTitle(item)}</strong>
                      <span>
                        {item.workflow_stage} ·{' '}
                        {new Date(item.updated_at).toLocaleString()}
                      </span>
                    </button>
                  </List.Item>
                )}
              />
            )}
          </Card>
          {thread?.active_ticket && <TicketSummary ticket={thread.active_ticket} />}
          <Card title="当前阶段">
            <Tag color="blue">{thread?.workflow_stage ?? 'INTAKE'}</Tag>
            {thread?.run_status === 'NEEDS_HUMAN_REVIEW' && (
              <Alert type="warning" message="当前需要物业人工处理" />
            )}
          </Card>
        </aside>
        <section className="chat-panel">
          {thread?.reconciliation && (
            <Alert
              type={thread.reconciliation.status === 'MANUAL_REVIEW' ? 'warning' : 'info'}
              message={
                thread.reconciliation.status === 'MANUAL_REVIEW'
                  ? '该操作需要物业人工核查。'
                  : thread.reconciliation.status === 'RESOLVED_NOT_COMMITTED'
                    ? '上次操作确认未提交，可以继续。'
                    : '系统正在核对本次操作是否已经完成，请勿重复提交。'
              }
            />
          )}
          {awaitingInformation && (
            <Alert
              showIcon
              type="warning"
              message="请先补充报修信息"
              description="可以在下方补充卡片或聊天输入框中直接说明，不需要填写英文代码。"
            />
          )}
          {awaitingSelection && (
            <Alert
              showIcon
              type="info"
              message="请先完成上方选择"
              description="完成工单或预约时间选择后，才能继续发送新消息。"
            />
          )}
          <div className="message-list" aria-label="消息列表">
            {messages.length === 0 && (
              <div className="empty-chat">
                请描述漏水、电气或门锁问题，以及您方便的时间。
              </div>
            )}
            {messages.map((item, index) => (
              <div key={`${item.role}-${index}`} className={`message ${item.role}`}>
                {item.text}
              </div>
            ))}
            {busy && <Spin />}
          </div>
          {thread?.interrupt && !reconciliationBlocked && (
            <InterruptPanel interrupt={thread.interrupt} onResume={resume} />
          )}
          <Space.Compact className="composer">
            <Input
              disabled={reconciliationBlocked || awaitingSelection}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onPressEnter={() => void send(input)}
              placeholder={
                awaitingInformation
                  ? '直接补充，例如：故障位置在厨房水槽下方'
                  : '例如：厨房水龙头漏水，明天下午有空'
              }
            />
            <Button
              disabled={reconciliationBlocked || awaitingSelection}
              type="primary"
              icon={<SendOutlined />}
              onClick={() => void send(input)}
            >
              {awaitingInformation ? '提交补充' : '发送'}
            </Button>
          </Space.Compact>
          <Space wrap>
            <Button
              disabled={reconciliationBlocked || Boolean(thread?.interrupt)}
              onClick={() => void send('我要改期，明天下午有空')}
            >
              提交改期请求
            </Button>
            <Button
              disabled={reconciliationBlocked || Boolean(thread?.interrupt)}
              danger
              onClick={() => void send('请转人工处理')}
            >
              请求人工处理
            </Button>
          </Space>
          <Alert
            type="info"
            message="取消工单、取消预约和维修验收首版需物业人工处理。"
          />
        </section>
      </Layout.Content>
    </Layout>
  )
}
