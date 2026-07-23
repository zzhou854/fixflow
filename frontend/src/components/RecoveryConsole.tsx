import { Alert, Button, Card, Descriptions, Empty, List, Space, Spin, Tag, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ReplayExecution, ReplayRun, ReplayRunDetail } from '../types'

interface Props { token: string; threadId: string }

const statusColor: Record<string, string> = {
  READY: 'green', PASSED: 'green', DIVERGED: 'red', INCOMPLETE: 'orange',
  UNSUPPORTED_SCHEMA: 'purple', FAILED_SAFE: 'red', UNAVAILABLE: 'default',
}

export function RecoveryConsole({ token, threadId }: Props) {
  const [runs, setRuns] = useState<ReplayRun[]>([])
  const [detail, setDetail] = useState<ReplayRunDetail | null>(null)
  const [execution, setExecution] = useState<ReplayExecution | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setRuns((await api.replayRuns(token, threadId)).items) }
    catch (reason) { setError(reason instanceof ApiError ? reason.body.message : '恢复记录加载失败') }
    finally { setLoading(false) }
  }, [threadId, token])

  useEffect(() => { void load() }, [load])

  async function inspect(run: ReplayRun) {
    try {
      const value = await api.replayRun(token, run.run_id)
      setDetail(value); setExecution(value.bundle?.latest_execution ?? null)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.body.message : '重放详情加载失败')
    }
  }

  async function verify() {
    if (!detail) return
    try {
      const value = await api.verifyReplay(token, detail.run.run_id)
      setExecution(value); await load()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.body.message : '确定性验证失败')
    }
  }

  return <Card title="恢复控制台" size="small">
    <Typography.Paragraph type="secondary">
      这里只验证录制证据下的控制面确定性，不会重发业务操作、恢复数据库或修改会话。
    </Typography.Paragraph>
    {loading && <Spin aria-label="恢复控制台加载中" />}
    {error && <Alert type="error" message={error} />}
    {!loading && !error && runs.length === 0 && <Empty description="暂无可审查的执行" />}
    <List dataSource={runs} renderItem={(run) => <List.Item
      actions={[<Button key="detail" onClick={() => void inspect(run)}>查看证据</Button>]}
    >
      <Space wrap>
        <Typography.Text code>{run.run_id.slice(0, 8)}</Typography.Text>
        <span>{run.trigger_type}</span>
        <Tag>{run.original_run_status}</Tag>
        <Tag color={statusColor[run.bundle_status]}>{run.bundle_status}</Tag>
        {run.latest_replay_status && <Tag color={statusColor[run.latest_replay_status]}>
          {run.latest_replay_status}
        </Tag>}
      </Space>
    </List.Item>} />
    {detail?.bundle && <Card size="small" title="原始重放证据">
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="Bundle Schema">{detail.bundle.schema_version}</Descriptions.Item>
        <Descriptions.Item label="Graph Schema">{detail.bundle.graph_schema_version}</Descriptions.Item>
        <Descriptions.Item label="Runtime Revision">{detail.bundle.runtime_revision}</Descriptions.Item>
        <Descriptions.Item label="Artifact 完整性">{detail.bundle.artifact_integrity}</Descriptions.Item>
        <Descriptions.Item label="Tape Steps">{detail.bundle.step_count}</Descriptions.Item>
      </Descriptions>
      <Button
        type="primary"
        disabled={detail.bundle.status !== 'READY'}
        onClick={() => void verify()}
      >验证确定性重放</Button>
    </Card>}
    {execution && <Card size="small" title="验证结果">
      <Tag color={statusColor[execution.status]}>{execution.status}</Tag>
      <Typography.Paragraph>恢复建议：{execution.recommendation ?? '暂无'}</Typography.Paragraph>
      <List dataSource={execution.mismatches} locale={{ emptyText: '未发现控制面差异' }}
        renderItem={(item) => <List.Item>
          <Space direction="vertical">
            <Typography.Text>{item.mismatch_type}</Typography.Text>
            <Typography.Text type="secondary">{item.step_key ?? '整体比较'}</Typography.Text>
            {item.expected_summary && <Typography.Text type="secondary">
              预期：{item.expected_summary}
            </Typography.Text>}
            {item.actual_summary && <Typography.Text type="secondary">
              实际：{item.actual_summary}
            </Typography.Text>}
          </Space>
        </List.Item>} />
    </Card>}
    {detail?.current_business_state && <Card size="small" title="当前业务状态">
      <Descriptions column={1} size="small">
        {Object.entries(detail.current_business_state).map(([key, value]) =>
          <Descriptions.Item key={key} label={key}>{String(value ?? '—')}</Descriptions.Item>)}
      </Descriptions>
      <Alert type="info" message="当前业务事实与原始重放验证分区展示，不参与重放结果。" />
    </Card>}
  </Card>
}
