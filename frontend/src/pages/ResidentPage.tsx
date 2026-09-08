import {
  CustomerServiceOutlined,
  LogoutOutlined,
  MenuOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Drawer,
  Input,
  Layout,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message as toast,
} from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, shanghaiReferenceTime } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { DemoBanner } from '../components/DemoBanner'
import { InterruptPanel } from '../components/InterruptPanel'
import { ResidentConversationList } from '../components/ResidentConversationList'
import { TicketSummary } from '../components/TicketSummary'
import { useSSE } from '../hooks/useSSE'
import { progressLabel, residentStageLabel } from '../residentDisplay'
import type { AgentThread, Property, ResidentThreadSummary } from '../types'

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

type FailedAction = { label: string; retry: () => Promise<void> }

const RESIDENT_ERROR_MESSAGES: Record<string, string> = {
  PROPERTY_CONTEXT_REQUIRED: '请先选择需要维修的房屋。',
  PERMISSION_DENIED: '当前账号不能处理这套房屋，请联系物业核实。',
  THREAD_IDENTITY_CONFLICT: '这段会话不属于当前账号，请重新选择会话。',
  VERSION_CONFLICT: '信息刚刚发生了变化，请刷新后再试。',
  TIME_CONFLICT: '这个上门时间刚刚被占用，请重新选择。',
  STALE_RESUME: '页面信息已经更新，请刷新后重新选择。',
  SERVICE_UNAVAILABLE: '服务暂时繁忙，请稍后重试或请物业工作人员协助。',
}

function conversation(thread: AgentThread): ChatMessage[] {
  return (thread.conversation_messages ?? []).map((item) => ({
    role: item.role === 'USER' ? 'user' : 'assistant',
    text: item.content,
  }))
}

function friendlyError(reason: unknown): string {
  if (reason instanceof ApiError) {
    return RESIDENT_ERROR_MESSAGES[reason.body.code]
      ?? '这次处理没有完成，请重试或请物业工作人员协助。'
  }
  if (reason instanceof DOMException && reason.name === 'AbortError') {
    return '处理时间有点长，请重试或请物业工作人员协助。'
  }
  return '服务暂时没有响应，请稍后重试或转交物业工作人员。'
}

export function ResidentPage() {
  const { token, user, logout } = useAuth()
  const [properties, setProperties] = useState<Property[]>([])
  const [propertyId, setPropertyId] = useState<string>()
  const [threads, setThreads] = useState<ResidentThreadSummary[]>([])
  const [allThreads, setAllThreads] = useState<ResidentThreadSummary[]>([])
  const [thread, setThread] = useState<AgentThread | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [allLoading, setAllLoading] = useState(false)
  const [archiveFilter, setArchiveFilter] = useState<'active' | 'archived' | 'all'>('active')
  const [conversationDrawerOpen, setConversationDrawerOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [failedAction, setFailedAction] = useState<FailedAction | null>(null)
  const [pendingStep, setPendingStep] = useState<string | null>(null)
  const messageListRef = useRef<HTMLDivElement>(null)

  const apply = useCallback((next: AgentThread) => {
    setThread(next)
    setMessages(conversation(next))
    setFailedAction(null)
    sessionStorage.setItem('fixflow.demo.thread_id', next.thread_id)
  }, [])

  const refreshThreads = useCallback(async () => {
    if (!token) return
    const result = await api.residentThreads(token, 'active', 5, 0)
    setThreads(result.items)
  }, [token])

  const loadAllThreads = useCallback(
    async (archiveStatus: 'active' | 'archived' | 'all' = archiveFilter) => {
      if (!token) return
      setAllLoading(true)
      try {
        const result = await api.residentThreads(token, archiveStatus, 50, 0)
        setAllThreads(result.items)
      } catch (reason) {
        toast.error(friendlyError(reason))
      } finally {
        setAllLoading(false)
      }
    },
    [archiveFilter, token],
  )

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
  const progress = pendingStep ?? progressLabel(thread, busy)

  const beginProgress = () => {
    setPendingStep('正在理解您的需求')
    const timers = [
      window.setTimeout(() => setPendingStep('正在核对房屋和服务信息'), 3_000),
      window.setTimeout(() => setPendingStep('正在确认处理结果，请稍候'), 9_000),
    ]
    return () => {
      timers.forEach(window.clearTimeout)
      setPendingStep(null)
    }
  }

  useEffect(() => {
    if (!token) return
    void Promise.all([api.properties(token), api.residentThreads(token, 'active', 5, 0)]).then(
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

  useEffect(() => {
    const element = messageListRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages, busy, thread?.interrupt])

  const selectThread = async (threadId: string) => {
    if (!token) return
    setBusy(true)
    try {
      apply(await api.getThread(token, threadId))
      setConversationDrawerOpen(false)
      setMobileMenuOpen(false)
    } catch (reason) {
      toast.error(friendlyError(reason))
    } finally {
      setBusy(false)
    }
  }

  const resume = async (body: object) => {
    if (!token || !thread) return
    const idempotencyKey = crypto.randomUUID()
    const run = async () => {
      setBusy(true)
      const endProgress = beginProgress()
      try {
        apply(await api.resume(token, thread.thread_id, body, idempotencyKey))
        await refreshThreads()
      } catch (reason) {
        setFailedAction({ label: '重新提交', retry: run })
        toast.error(friendlyError(reason))
      } finally {
        endProgress()
        setBusy(false)
      }
    }
    await run()
  }

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!token || !propertyId || !trimmed || awaitingSelection) return
    const existingThread = thread
    const idempotencyKey = crypto.randomUUID()
    const referenceTime = shanghaiReferenceTime()
    setMessages((current) => [...current, { role: 'user', text: trimmed }])
    setBusy(true)
    setInput('')
    setFailedAction(null)
    const run = async () => {
      setBusy(true)
      const endProgress = beginProgress()
      try {
        let next: AgentThread
        if (existingThread && awaitingInformation) {
          next = await api.resume(token, existingThread.thread_id, {
            kind: 'PROVIDE_INFORMATION',
            intent_version: existingThread.interrupt!.intent_version,
            user_message: trimmed,
            reference_time: referenceTime,
            timezone_name: 'Asia/Shanghai',
          }, idempotencyKey)
        } else if (existingThread) {
          next = await api.sendMessage(
            token,
            existingThread.thread_id,
            trimmed,
            idempotencyKey,
            referenceTime,
          )
        } else {
          next = await api.createThread(
            token,
            propertyId,
            trimmed,
            idempotencyKey,
            referenceTime,
          )
        }
        apply(next)
        await refreshThreads()
      } catch (reason) {
        setFailedAction({ label: '重试这条消息', retry: run })
        toast.error(friendlyError(reason))
      } finally {
        endProgress()
        setBusy(false)
      }
    }
    await run()
  }

  const archiveThread = async (item: ResidentThreadSummary) => {
    if (!token) return
    try {
      await api.archiveThread(token, item.thread_id, item.version)
      if (thread?.thread_id === item.thread_id) {
        sessionStorage.removeItem('fixflow.demo.thread_id')
        setThread(null)
        setMessages([])
      }
      await Promise.all([refreshThreads(), loadAllThreads(archiveFilter)])
      toast.success('会话已归档，之后可以随时恢复。')
    } catch (reason) {
      toast.error(friendlyError(reason))
    }
  }

  const restoreThread = async (item: ResidentThreadSummary) => {
    if (!token) return
    try {
      await api.restoreThread(token, item.thread_id, item.version)
      await Promise.all([refreshThreads(), loadAllThreads(archiveFilter)])
      toast.success('会话已恢复到最近列表。')
    } catch (reason) {
      toast.error(friendlyError(reason))
    }
  }

  const newThread = () => {
    sessionStorage.removeItem('fixflow.demo.thread_id')
    setThread(null)
    setMessages([])
    setInput('')
    setFailedAction(null)
    setMobileMenuOpen(false)
  }

  const sidebar = (renderConversationDrawer: boolean) => (
    <div className="resident-sidebar-content">
      <Card title="服务房屋" className="resident-home-card">
        <Select
          aria-label="房屋选择"
          value={propertyId}
          onChange={setPropertyId}
          options={properties.map((item) => ({
            value: item.property_id,
            label: item.address_text,
          }))}
        />
        <Button block type="primary" icon={<PlusOutlined />} onClick={newThread}>
          新建报修会话
        </Button>
      </Card>
      <Card
        title="最近会话"
        className="thread-history-card"
        extra={<span className="recent-limit">最近 5 条</span>}
      >
        <ResidentConversationList
          currentThreadId={thread?.thread_id}
          recentThreads={threads}
          allThreads={allThreads}
          allThreadsLoading={allLoading}
          archiveFilter={archiveFilter}
          drawerOpen={conversationDrawerOpen}
          renderDrawer={renderConversationDrawer}
          onDrawerOpen={() => {
            setConversationDrawerOpen(true)
            void loadAllThreads(archiveFilter)
          }}
          onDrawerClose={() => setConversationDrawerOpen(false)}
          onFilterChange={(filter) => {
            setArchiveFilter(filter)
            void loadAllThreads(filter)
          }}
          onSelect={selectThread}
          onArchive={archiveThread}
          onRestore={restoreThread}
        />
      </Card>
      {thread?.active_ticket && <TicketSummary ticket={thread.active_ticket} />}
    </div>
  )

  return (
    <Layout className="app-shell resident-app-shell">
      <DemoBanner />
      <Layout.Header className="app-header resident-header">
        <Space>
          <Button
            className="mobile-menu-button"
            type="text"
            icon={<MenuOutlined />}
            aria-label="打开会话菜单"
            onClick={() => setMobileMenuOpen(true)}
          />
          <div>
            <Typography.Title level={3}>住户维修助手</Typography.Title>
            <Typography.Text type="secondary">报修、约时间，一步一步帮您处理</Typography.Text>
          </div>
        </Space>
        <Space>
          <Tag color={sse === 'connected' ? 'success' : 'default'}>
            {sse === 'connected' ? '服务连接正常' : '正在连接服务'}
          </Tag>
          <span className="resident-username">{user?.username}</span>
          <Button icon={<LogoutOutlined />} onClick={logout}>
            退出
          </Button>
        </Space>
      </Layout.Header>
      <Layout.Content className="resident-grid">
        <aside className="resident-sidebar">{sidebar(true)}</aside>
        <Drawer
          placement="left"
          width="min(88vw, 360px)"
          title="我的报修"
          open={mobileMenuOpen}
          onClose={() => setMobileMenuOpen(false)}
          className="mobile-sidebar-drawer"
        >
          {sidebar(false)}
        </Drawer>
        <section className="chat-panel">
          <div className="chat-context-bar">
            <div>
              <strong>{thread ? residentStageLabel(thread.workflow_stage) : '准备开始新的报修'}</strong>
              <span>
                {thread?.active_ticket
                  ? '工单进展以物业系统的最新记录为准'
                  : '告诉我发生了什么，我会继续引导您'}
              </span>
            </div>
            {thread?.run_status === 'NEEDS_HUMAN_REVIEW' && (
              <Tag color="warning">物业人工处理中</Tag>
            )}
          </div>
          <div className="chat-scroll-region">
            {thread?.reconciliation && (
              <Alert
                showIcon
                type={thread.reconciliation.status === 'MANUAL_REVIEW' ? 'warning' : 'info'}
                message={
                  thread.reconciliation.status === 'MANUAL_REVIEW'
                    ? '物业工作人员正在核实这次操作。'
                    : thread.reconciliation.status === 'RESOLVED_NOT_COMMITTED'
                      ? '上次操作没有提交，可以安全重试。'
                      : '系统正在核对处理结果，请不要重复提交。'
                }
              />
            )}
            <div className="message-list" aria-label="消息列表" ref={messageListRef}>
              {messages.length === 0 && (
                <div className="empty-chat">
                  <CustomerServiceOutlined />
                  <Typography.Title level={4}>今天需要维修什么？</Typography.Title>
                  <Typography.Paragraph>
                    目前可处理漏水、用电和门锁问题。请说清问题位置和方便上门的时间。
                  </Typography.Paragraph>
                  <div className="quick-prompts">
                    {['厨房水管漏水，明天下午在家', '客厅插座没电了', '入户门锁打不开'].map(
                      (example) => (
                        <Button key={example} onClick={() => void send(example)}>
                          {example}
                        </Button>
                      ),
                    )}
                  </div>
                </div>
              )}
              {messages.map((item, index) => (
                <div key={`${item.role}-${index}`} className={`message ${item.role}`}>
                  {item.text}
                </div>
              ))}
              {progress && (
                <div className={`run-progress ${thread?.run_status === 'FAILED_SAFE' ? 'failed' : ''}`}>
                  {busy && <Spin size="small" />}
                  <span>{progress}</span>
                </div>
              )}
              {failedAction && (
                <Alert
                  showIcon
                  type="error"
                  message="这次没有处理完成"
                  description="您可以重试，或直接请物业工作人员接手。"
                  action={
                    <Space direction="vertical">
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => void failedAction.retry()}
                      >
                        {failedAction.label}
                      </Button>
                      <Button size="small" onClick={() => void send('请转人工处理')}>
                        转人工
                      </Button>
                    </Space>
                  }
                />
              )}
              {thread?.interrupt && !reconciliationBlocked && (
                <InterruptPanel interrupt={thread.interrupt} onResume={resume} />
              )}
            </div>
          </div>
          <div className="composer-area">
            {awaitingSelection && (
              <Alert
                showIcon
                type="info"
                message="请先完成上方的选择，再继续发送新消息。"
              />
            )}
            <Space.Compact className="composer">
              <Input
                disabled={reconciliationBlocked || awaitingSelection || busy}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onPressEnter={() => void send(input)}
                placeholder={
                  awaitingInformation
                    ? '直接补充，例如：漏水位置在厨房水槽下方'
                    : '请描述问题，例如：厨房水龙头漏水，明天下午有空'
                }
                aria-label="输入消息"
              />
              <Button
                disabled={reconciliationBlocked || awaitingSelection || busy}
                type="primary"
                icon={<SendOutlined />}
                onClick={() => void send(input)}
              >
                {awaitingInformation ? '补充信息' : '发送'}
              </Button>
            </Space.Compact>
            <div className="secondary-actions">
              <Button
                disabled={reconciliationBlocked || Boolean(thread?.interrupt) || busy}
                onClick={() => void send('我要改期，明天下午有空')}
              >
                申请改期
              </Button>
              <Button
                disabled={reconciliationBlocked || Boolean(thread?.interrupt) || busy}
                danger
                onClick={() => void send('请转人工处理')}
              >
                请物业协助
              </Button>
              <Typography.Text type="secondary">
                取消工单、取消预约和确认维修结果目前由物业工作人员处理。
              </Typography.Text>
            </div>
          </div>
        </section>
      </Layout.Content>
    </Layout>
  )
}
