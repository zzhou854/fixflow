import { Alert, Button, Card, Descriptions, Drawer, Empty, Select, Space, Spin, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ReconciliationCase } from '../types'

const labels: Record<string, string> = {
  PENDING: '核对中',
  PROCESSING: '核对中',
  RESOLVED_COMMITTED: '确认已提交',
  RESOLVED_NOT_COMMITTED: '确认未提交',
  MANUAL_REVIEW: '需要人工审查',
}

export function ReconciliationCases({ token }: { token: string }) {
  const [items, setItems] = useState<ReconciliationCase[]>([])
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState<string>()
  const [action, setAction] = useState<string>()
  const [selected, setSelected] = useState<ReconciliationCase | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const query = new URLSearchParams()
    if (status) query.set('status', status)
    if (action) query.set('operation', action)
    try {
      setItems((await api.reconciliationCases(token, `?${query}`)).items)
    } catch (error) {
      message.error(error instanceof ApiError ? error.body.message : '对账记录加载失败')
    } finally {
      setLoading(false)
    }
  }, [token, status, action])

  useEffect(() => { void load() }, [load])
  if (loading) return <Spin />

  return <Card title="失败对账">
    <Space wrap>
      <Select aria-label="对账状态" allowClear placeholder="状态" onChange={setStatus}
        options={Object.keys(labels).map((value) => ({ value, label: labels[value] }))} />
      <Select aria-label="操作类型" allowClear placeholder="操作" onChange={setAction}
        options={['CREATE_TICKET', 'BOOK_APPOINTMENT', 'RESCHEDULE_APPOINTMENT', 'ESCALATE_TO_OPERATOR'].map((value) => ({ value }))} />
      <Button onClick={() => void load()}>刷新</Button>
    </Space>
    {items.length === 0 ? <Empty description="暂无对账记录" /> : <Table
      rowKey="case_id"
      pagination={{ pageSize: 10 }}
      dataSource={items}
      onRow={(row) => ({ onClick: () => setSelected(row) })}
      columns={[
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{labels[value]}</Tag> },
        { title: '操作', dataIndex: 'operation_type' },
        { title: '目标', dataIndex: 'target_entity_id', ellipsis: true },
        { title: '尝试', dataIndex: 'attempt_count' },
        { title: '结论', dataIndex: 'resolution_code' },
        { title: '只读复查', render: (_, row: ReconciliationCase) => <Button disabled={!row.retry_allowed} onClick={async (event) => {
          event.stopPropagation()
          try { await api.recheckReconciliation(token, row.case_id); await load() }
          catch (error) { message.error(error instanceof ApiError ? error.body.message : '重新检查失败') }
        }}>重新检查</Button> },
      ]}
    />}
    <Alert type="info" message="只能重新触发只读证据检查，不能标记结果或重发业务操作。" />
    <Drawer title="对账详情" open={selected !== null} onClose={() => setSelected(null)}>
      {selected && <Descriptions column={1} size="small">
        <Descriptions.Item label="Case">{selected.case_id}</Descriptions.Item>
        <Descriptions.Item label="Operation">{selected.operation_id_short ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Thread">{selected.thread_id_short ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="状态">{labels[selected.status]}</Descriptions.Item>
        <Descriptions.Item label="证据">{selected.evidence_status ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="结论">{selected.resolution_code ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="最后错误">{selected.last_error_code ?? '-'}</Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </Card>
}
