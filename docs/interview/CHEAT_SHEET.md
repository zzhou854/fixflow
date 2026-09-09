# FixFlow 面试一页速记

## 一句话

住宅维修协调 Agent：模型理解生活化语言，确定性系统负责权限、状态、预约、事务和恢复。

## 三个设计决定

1. **Single Orchestrator**：范围明确，Typed State 和恢复边界优先于多 Agent 炫技。
2. **LLM 不掌握写权限**：只返回严格 Schema 的证据事实和受约束回复。
3. **PostgreSQL 是事实源**：Checkpoint 是流程状态，恢复时必须刷新业务快照和版本。

## 主链路

```text
React -> FastAPI/JWT -> AgentOrchestrator
      -> DeepSeek Flash（事实提取）
      -> Policy Retrieval（只读证据）
      -> Typed MCP -> Application/UoW -> PostgreSQL
```

## 失败链路

```text
Flash 有限重试 / 一次 Schema 修复
-> 仍失败
-> HUMAN_REVIEW + human_review_case
-> 物业认领 / 记录 / 办结
```

不调用 Pro，不回退 Scripted 冒充成功。

## 最能讲的三个 Bug

- 失败文案误报身份冲突：持久化人工状态并创建工单前任务。
- “明天上午”变凌晨：增加确定性时段解释，09:00–12:00。
- 物业确认按钮无响应：修复 AntD App 上下文与 SSE Abort 清理。

## 并发与可靠性关键词

- 请求幂等 + Payload Fingerprint
- `UPDATE ... WHERE id AND version`
- 部分唯一索引：每工单最多一个 BOOKED
- GiST 排斥：维修人员时间不重叠
- Transactional Outbox
- UNKNOWN_COMMIT 只对账，不盲目重发
- Replay 用 Tape，禁止真实 Mutation

## 产品思维

- “书房开关坏了”已经足够；不要求型号、零件或成因。
- 住户看中文业务进度，不看枚举、Trace 或 MCP 错误。
- 候选最多 6 个，选择前不承诺预约成功。
- 无回复不可接受；每次运行必须成功、失败或转人工。

## 当前边界

已达到：稳定本地面试演示候选。

尚未达到：生产激活、正式站点政策审批、TLS/监控/异地备份、真实运营 SLA。

## 反问面试官

- 贵团队目前如何划分模型判断与确定性业务规则？
- Agent 的线上失败主要来自模型质量、工具调用还是业务系统一致性？
- 团队是否有 Prompt/模型版本、Trace、回放和人工接管的统一治理？
- 这个岗位更偏 Agent 产品闭环、平台基础设施，还是模型评测与优化？
