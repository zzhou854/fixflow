import { Alert, Button, Card, Form, Input, Segmented, Typography } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export function LoginPage() {
  const [account, setAccount] = useState<'resident' | 'operator'>('resident')
  const [error, setError] = useState<string | null>(null)
  const { login } = useAuth(); const navigate = useNavigate()
  const defaults = account === 'resident'
    ? { username: 'resident_demo', password: '' }
    : { username: 'operator_demo', password: '' }
  return <main className="login-shell"><Card className="login-card">
    <Typography.Title level={2}>FixFlow</Typography.Title>
    <Typography.Paragraph type="secondary">智慧物业维修协调演示</Typography.Paragraph>
    <Segmented block value={account} options={[{ label: '住户', value: 'resident' }, { label: '物业操作员', value: 'operator' }]} onChange={(value) => { setAccount(value as 'resident' | 'operator'); setError(null) }} />
    {error && <Alert type="error" message={error} showIcon />}
    <Form key={account} layout="vertical" initialValues={defaults} onFinish={async (values: { username: string; password: string }) => {
      try { const user = await login(values.username, values.password); navigate(user.actor_type === 'OPERATOR' ? '/operator' : '/resident') }
      catch (reason) { setError(reason instanceof ApiError ? reason.body.message : '登录失败，请稍后重试。') }
    }}>
      <Form.Item label="账号" name="username" rules={[{ required: true }]}><Input autoComplete="username" /></Form.Item>
      <Form.Item label="密码" name="password" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
      <Button block type="primary" htmlType="submit">登录</Button>
    </Form>
  </Card></main>
}
