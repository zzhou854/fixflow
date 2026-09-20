import { Alert, Button, Card, Checkbox, Form, Input, Segmented, Typography } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import type { RegisterResidentInput } from '../types'

type LoginMode = 'resident' | 'operator' | 'register'
type LoginAccount = Exclude<LoginMode, 'register'>
type LoginFormValues = Pick<RegisterResidentInput, 'username' | 'password'>
  & Partial<Omit<RegisterResidentInput, 'username' | 'password'>>
  & { remember_password?: boolean }

const CREDENTIAL_KEY_PREFIX = 'fixflow.remembered.credentials.'

function loginDefaults(account: LoginMode): LoginFormValues {
  if (account === 'register') {
    return {
      username: '',
      password: '',
      community_name: '星河花园',
      building_no: '3',
      unit_no: '2',
      room_no: '1201',
    }
  }
  const fallback = account === 'resident' ? 'resident_test' : 'operator_test'
  try {
    const raw = localStorage.getItem(`${CREDENTIAL_KEY_PREFIX}${account}`)
    if (raw) {
      const saved = JSON.parse(raw) as { username?: unknown; password?: unknown }
      if (typeof saved.username === 'string' && typeof saved.password === 'string') {
        return { username: saved.username, password: saved.password, remember_password: true }
      }
    }
  } catch {
    localStorage.removeItem(`${CREDENTIAL_KEY_PREFIX}${account}`)
  }
  return { username: fallback, password: '', remember_password: true }
}

function updateRememberedCredentials(
  account: LoginAccount,
  values: LoginFormValues,
) {
  const key = `${CREDENTIAL_KEY_PREFIX}${account}`
  if (values.remember_password) {
    localStorage.setItem(key, JSON.stringify({
      username: values.username,
      password: values.password,
    }))
  } else {
    localStorage.removeItem(key)
  }
}

export function LoginPage() {
  const [account, setAccount] = useState<LoginMode>('resident')
  const [error, setError] = useState<string | null>(null)
  const { login, register } = useAuth(); const navigate = useNavigate()
  const defaults = loginDefaults(account)
  return <main className="login-shell"><Card className="login-card">
    <Typography.Title level={2}>FixFlow</Typography.Title>
    <Typography.Paragraph type="secondary">智慧物业维修协调演示</Typography.Paragraph>
    <Segmented block value={account} options={[{ label: '住户登录', value: 'resident' }, { label: '物业登录', value: 'operator' }, { label: '住户注册', value: 'register' }]} onChange={(value) => { setAccount(value as LoginMode); setError(null) }} />
    {error && <Alert type="error" message={error} showIcon />}
    <Form key={account} layout="vertical" initialValues={defaults} onFinish={async (values: LoginFormValues) => {
      try {
        let user
        if (account === 'register') {
          user = await register({
            username: values.username,
            password: values.password,
            community_name: values.community_name ?? '',
            building_no: values.building_no ?? '',
            unit_no: values.unit_no ?? '',
            room_no: values.room_no ?? '',
          })
        } else {
          user = await login(values.username, values.password)
          updateRememberedCredentials(account, values)
        }
        navigate(user.actor_type === 'OPERATOR' ? '/operator' : '/resident')
      }
      catch (reason) { setError(reason instanceof ApiError ? reason.body.message : `${account === 'register' ? '注册' : '登录'}失败，请稍后重试。`) }
    }}>
      <Form.Item label="账号" name="username" rules={[{ required: true }]}><Input autoComplete="username" /></Form.Item>
      <Form.Item label="密码" name="password" rules={[{ required: true }, ...(account === 'register' ? [{ min: 8, message: '密码至少需要 8 个字符' }] : [])]}><Input.Password autoComplete={account === 'register' ? 'new-password' : 'current-password'} /></Form.Item>
      {account === 'register' && <>
        <Form.Item label="小区" name="community_name" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item label="楼栋" name="building_no" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item label="单元" name="unit_no" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item label="房号" name="room_no" rules={[{ required: true }]}><Input /></Form.Item>
        <Typography.Paragraph type="secondary">
          演示房屋范围：星河花园 1–3 栋、每栋 1–2 单元、1–12 层、每层 01–04 室。
        </Typography.Paragraph>
      </>}
      {account !== 'register' && (
        <Form.Item name="remember_password" valuePropName="checked">
          <Checkbox>记住密码</Checkbox>
        </Form.Item>
      )}
      <Button block type="primary" htmlType="submit">{account === 'register' ? '注册并登录' : '登录'}</Button>
    </Form>
  </Card></main>
}
