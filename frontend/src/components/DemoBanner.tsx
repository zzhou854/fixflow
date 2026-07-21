import { Alert } from 'antd'

export function DemoBanner() {
  return <Alert banner type="warning" message="演示模式：当前使用离线 Scripted LLM 与确定性 Embedding，不是在线大模型。" />
}
