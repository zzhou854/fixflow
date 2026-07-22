import { LogoutOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Input, Layout, Select, Space, Spin, Tag, Typography, message as toast } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { api, ApiError } from '../api/client'
import { DemoBanner } from '../components/DemoBanner'
import { InterruptPanel } from '../components/InterruptPanel'
import { TicketSummary } from '../components/TicketSummary'
import { useSSE } from '../hooks/useSSE'
import type { AgentThread, Property } from '../types'

interface ChatMessage { role: 'user' | 'assistant'; text: string }

export function ResidentPage() {
  const { token, user, logout } = useAuth()
  const [properties, setProperties] = useState<Property[]>([])
  const [propertyId, setPropertyId] = useState<string>()
  const [thread, setThread] = useState<AgentThread | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const reconcile = useCallback(async (threadId: string) => {
    if (token) setThread(await api.getThread(token, threadId))
  }, [token])
  const sse = useSSE(thread?.thread_id ?? null, token, reconcile)
  const reconciliationBlocked = ['PENDING', 'PROCESSING', 'MANUAL_REVIEW'].includes(thread?.reconciliation?.status ?? '')
  useEffect(() => { if (token) void api.properties(token).then((items) => { setProperties(items); setPropertyId(items[0]?.property_id) }) }, [token])
  useEffect(() => {
    const saved = sessionStorage.getItem('fixflow.demo.thread_id')
    if (token && saved) void api.getThread(token, saved).then(setThread).catch(() => sessionStorage.removeItem('fixflow.demo.thread_id'))
  }, [token])
  const apply = (next: AgentThread) => {
    setThread(next)
    sessionStorage.setItem('fixflow.demo.thread_id', next.thread_id)
    if (next.assistant_message) setMessages((current) => [...current, { role: 'assistant', text: next.assistant_message! }])
  }
  const send = async (text: string) => {
    if (!token || !propertyId || !text.trim()) return
    setMessages((current) => [...current, { role: 'user', text }]); setBusy(true); setInput('')
    try { apply(thread ? await api.sendMessage(token, thread.thread_id, text) : await api.createThread(token, propertyId, text)) }
    catch (reason) { toast.error(reason instanceof ApiError ? reason.body.message : '请求失败') }
    finally { setBusy(false) }
  }
  const resume = async (body: object) => {
    if (!token || !thread) return
    setBusy(true)
    try { apply(await api.resume(token, thread.thread_id, body)) }
    catch (reason) { toast.error(reason instanceof ApiError ? reason.body.message : '恢复会话失败') }
    finally { setBusy(false) }
  }
  return <Layout className="app-shell">
    <DemoBanner />
    <Layout.Header className="app-header"><Typography.Title level={3}>住户维修助手</Typography.Title><Space><Tag color={sse === 'connected' ? 'green' : 'default'}>SSE {sse}</Tag><span>{user?.username}</span><Button icon={<LogoutOutlined />} onClick={logout}>退出</Button></Space></Layout.Header>
    <Layout.Content className="resident-grid">
      <aside><Card title="服务房屋"><Select aria-label="房屋选择" value={propertyId} onChange={setPropertyId} options={properties.map((item) => ({ value: item.property_id, label: item.address_text }))} /><Button block icon={<PlusOutlined />} onClick={() => { sessionStorage.removeItem('fixflow.demo.thread_id'); setThread(null); setMessages([]) }}>新建会话</Button></Card>{thread?.active_ticket && <TicketSummary ticket={thread.active_ticket} />}<Card title="当前阶段"><Tag color="blue">{thread?.workflow_stage ?? 'INTAKE'}</Tag>{thread?.run_status === 'NEEDS_HUMAN_REVIEW' && <Alert type="warning" message="当前需物业人工处理" />}</Card></aside>
      <section className="chat-panel">{thread?.reconciliation && <Alert type={thread.reconciliation.status === 'MANUAL_REVIEW' ? 'warning' : 'info'} message={thread.reconciliation.status === 'MANUAL_REVIEW' ? '该操作需要物业人工核查。' : thread.reconciliation.status === 'RESOLVED_NOT_COMMITTED' ? '上次操作确认未提交，可以继续。' : '系统正在核对本次操作是否已经完成，请勿重复提交。'} />}<div className="message-list" aria-label="消息列表">{messages.length === 0 && <div className="empty-chat">请描述漏水、电气或门锁问题，以及您方便的时间。</div>}{messages.map((item, index) => <div key={`${item.role}-${index}`} className={`message ${item.role}`}>{item.text}</div>)}{busy && <Spin />}</div>{thread?.interrupt && !reconciliationBlocked && <InterruptPanel interrupt={thread.interrupt} onResume={resume} />}<Space.Compact className="composer"><Input disabled={reconciliationBlocked} value={input} onChange={(event) => setInput(event.target.value)} onPressEnter={() => void send(input)} placeholder="例如：厨房水龙头漏水，明天下午有空" /><Button disabled={reconciliationBlocked} type="primary" icon={<SendOutlined />} onClick={() => void send(input)}>发送</Button></Space.Compact><Space wrap><Button disabled={reconciliationBlocked} onClick={() => void send('我要改期，明天下午有空')}>提交改期请求</Button><Button disabled={reconciliationBlocked} danger onClick={() => void send('请转人工处理')}>请求人工处理</Button></Space><Alert type="info" message="取消工单、取消预约和维修验收首版需物业人工处理。" /></section>
    </Layout.Content>
  </Layout>
}
