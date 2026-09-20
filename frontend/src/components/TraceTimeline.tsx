import { Alert, Button, Card, Collapse, Empty, Select, Space, Spin, Tag, Timeline, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { AgentRun, TraceEvent } from '../types'

const PAGE_SIZE = 50

function safePayload(payload: TraceEvent['payload']) {
  const entries = Object.entries(payload)
  if (!entries.length) return '无附加摘要'
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(' · ')
}

export function TraceTimeline({ token, threadId }: { token: string; threadId: string }) {
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [selectedRun, setSelectedRun] = useState<string>()
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [source, setSource] = useState<string>()
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()

  const loadRuns = useCallback(async () => {
    setLoading(true); setError(undefined)
    try {
      const result = await api.operatorRuns(token, threadId)
      setRuns(result.items)
      setSelectedRun((current) => current ?? result.items[0]?.run_id)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.body.message : '执行记录加载失败')
    } finally { setLoading(false) }
  }, [token, threadId])

  const loadEvents = useCallback(async (append = false) => {
    if (!selectedRun) return
    const nextOffset = append ? offset : 0
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(nextOffset) })
    if (source) query.set('source', source)
    setLoading(true); setError(undefined)
    try {
      const result = await api.operatorRunEvents(token, selectedRun, `?${query}`)
      setEvents((current) => [...(append ? current : []), ...result.items].sort((left, right) => {
        const leftSequence = left.sequence_number ?? Number.MAX_SAFE_INTEGER
        const rightSequence = right.sequence_number ?? Number.MAX_SAFE_INTEGER
        return leftSequence - rightSequence || left.event_id.localeCompare(right.event_id)
      }))
      setOffset(nextOffset + result.items.length)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.body.message : 'Trace 加载失败')
    } finally { setLoading(false) }
  }, [token, selectedRun, source, offset])

  useEffect(() => { void loadRuns() }, [loadRuns])
  useEffect(() => { setOffset(0); void loadEvents(false) }, [selectedRun, source]) // eslint-disable-line react-hooks/exhaustive-deps

  return <Card title="Agent 执行时间线" size="small">
    <Alert type="info" showIcon message="业务状态历史与 Agent Trace 相互独立；此处仅展示清洗后的执行审计。" />
    <Space wrap className="trace-controls">
      <Select aria-label="选择执行" value={selectedRun} onChange={setSelectedRun} placeholder="选择一次运行" options={runs.map((run) => ({ value: run.run_id, label: `${run.trigger} · ${new Date(run.started_at).toLocaleString()}` }))} />
      <Select aria-label="Trace 来源" allowClear value={source} onChange={setSource} placeholder="全部来源" options={['API','AGENT','MCP','DOMAIN','OUTBOX'].map((value) => ({ value }))} />
      <Button onClick={() => void loadRuns()}>刷新</Button>
    </Space>
    {error && <Alert type="error" message={error} />}
    {loading && !events.length ? <Spin /> : !selectedRun ? <Empty description="暂无执行记录" /> : <Timeline items={events.map((event) => ({
      color: event.event_type.includes('failed') ? 'red' : event.event_type.includes('completed') ? 'green' : 'blue',
      children: <Collapse ghost items={[{
        key: event.event_id,
        label: <Space><Tag>{event.source}</Tag><Typography.Text strong>{event.event_type}</Typography.Text><Typography.Text type="secondary">#{event.sequence_number ?? '外部'} · {new Date(event.occurred_at).toLocaleString()}</Typography.Text></Space>,
        children: <Typography.Text>{safePayload(event.payload)}</Typography.Text>,
      }]} />,
    }))} />}
    {events.length > 0 && events.length % PAGE_SIZE === 0 && <Button loading={loading} onClick={() => void loadEvents(true)}>加载更多</Button>}
  </Card>
}
