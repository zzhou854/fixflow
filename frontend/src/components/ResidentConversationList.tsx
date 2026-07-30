import {
  DeleteOutlined,
  HistoryOutlined,
  InboxOutlined,
  MoreOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import { Button, Drawer, Empty, Input, List, Modal, Segmented, Space, Tag, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { residentStageLabel, threadTitle } from '../residentDisplay'
import type { ResidentThreadSummary } from '../types'

type ArchiveFilter = 'active' | 'archived' | 'all'

interface Props {
  currentThreadId?: string
  recentThreads: ResidentThreadSummary[]
  allThreads: ResidentThreadSummary[]
  allThreadsLoading: boolean
  drawerOpen: boolean
  renderDrawer?: boolean
  onDrawerOpen: () => void
  onDrawerClose: () => void
  onFilterChange: (filter: ArchiveFilter) => void
  onSelect: (threadId: string) => Promise<void>
  onArchive: (thread: ResidentThreadSummary) => Promise<void>
  onRestore: (thread: ResidentThreadSummary) => Promise<void>
}

function ThreadButton({
  item,
  active,
  onSelect,
}: {
  item: ResidentThreadSummary
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={`thread-history-item ${active ? 'active' : ''}`}
      onClick={onSelect}
    >
      <strong>{threadTitle(item)}</strong>
      <span>
        {residentStageLabel(item.workflow_stage)} ·{' '}
        {new Date(item.updated_at).toLocaleString('zh-CN')}
      </span>
    </button>
  )
}

export function ResidentConversationList(props: Props) {
  const [query, setQuery] = useState('')
  const visible = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    if (!keyword) return props.allThreads
    return props.allThreads.filter((item) =>
      threadTitle(item).toLocaleLowerCase().includes(keyword),
    )
  }, [props.allThreads, query])

  function confirmArchive(item: ResidentThreadSummary) {
    const hasTicket = Boolean(item.active_ticket_id)
    Modal.confirm({
      title: hasTicket ? '归档这段会话？' : '移除这段会话？',
      content: hasTicket
        ? '会话将从最近列表隐藏，但不会取消已有工单、预约或删除审计记录，之后仍可恢复。'
        : '这不是永久删除。会话会被安全归档，之后可在“全部会话”中恢复。',
      okText: hasTicket ? '归档会话' : '确认移除',
      cancelText: '暂不处理',
      onOk: () => props.onArchive(item),
    })
  }

  return (
    <>
      <div className="recent-thread-list" aria-label="最近会话">
        {props.recentThreads.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有报修会话" />
        ) : (
          <List
            size="small"
            dataSource={props.recentThreads.slice(0, 5)}
            renderItem={(item) => (
              <List.Item>
                <ThreadButton
                  item={item}
                  active={item.thread_id === props.currentThreadId}
                  onSelect={() => void props.onSelect(item.thread_id)}
                />
              </List.Item>
            )}
          />
        )}
      </div>
      <Button block icon={<HistoryOutlined />} onClick={props.onDrawerOpen}>
        查看全部会话
      </Button>
      {props.renderDrawer !== false && <Drawer
        title="全部会话"
        width={520}
        open={props.drawerOpen}
        onClose={props.onDrawerClose}
        className="conversation-drawer"
      >
        <Space direction="vertical" size="middle" className="drawer-stack">
          <Typography.Text type="secondary">
            归档只会隐藏会话，不会取消工单、预约或删除历史记录。
          </Typography.Text>
          <Segmented
            block
            options={[
              { label: '进行中', value: 'active' },
              { label: '已归档', value: 'archived' },
              { label: '全部', value: 'all' },
            ]}
            onChange={(value) => props.onFilterChange(value as ArchiveFilter)}
          />
          <Input
            allowClear
            prefix={<MoreOutlined />}
            placeholder="按报修类型或位置查找"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <List
            loading={props.allThreadsLoading}
            locale={{ emptyText: '没有符合条件的会话' }}
            dataSource={visible}
            renderItem={(item) => (
              <List.Item
                actions={[
                  item.lifecycle_status === 'ARCHIVED' ? (
                    <Button
                      key="restore"
                      icon={<RollbackOutlined />}
                      onClick={() => void props.onRestore(item)}
                    >
                      恢复
                    </Button>
                  ) : (
                    <Button
                      key="archive"
                      danger
                      type="text"
                      icon={item.active_ticket_id ? <InboxOutlined /> : <DeleteOutlined />}
                      onClick={() => confirmArchive(item)}
                    >
                      {item.active_ticket_id ? '归档会话' : '移除会话'}
                    </Button>
                  ),
                ]}
              >
                <div className="drawer-thread-row">
                  <ThreadButton
                    item={item}
                    active={item.thread_id === props.currentThreadId}
                    onSelect={() => void props.onSelect(item.thread_id)}
                  />
                  {item.lifecycle_status === 'ARCHIVED' && <Tag>已归档</Tag>}
                </div>
              </List.Item>
            )}
          />
        </Space>
      </Drawer>}
    </>
  )
}
