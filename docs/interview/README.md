# FixFlow 面试材料包

这套材料用于 Agent 应用开发岗位面试。核心表达不是“我接了一个大模型”，
而是“我把不可靠的语言理解接入了一个可审计、可恢复的确定性业务系统”。

## 建议使用顺序

1. 面试前阅读 [一页速记](CHEAT_SHEET.md)。
2. 按 [现场演示脚本](DEMO_SCRIPT.md) 演练两遍，控制在 8 分钟内。
3. 用 [架构讲解](ARCHITECTURE_WALKTHROUGH.md) 回答系统设计追问。
4. 用 [故障复盘](INCIDENT_STORIES.md) 证明项目不是只写了 Happy Path。
5. 从 [简历与自我介绍](RESUME_AND_INTRO.md) 选择适合岗位的版本。
6. 用 [高频问答](INTERVIEW_QA.md) 做模拟面试。

## 当前可陈述结论

- FixFlow 是面向住宅漏水、电气和门锁报修的可恢复 Agent 应用。
- 本地面试演示候选已经通过真实浏览器纵向验收。
- DeepSeek V4 Flash 仅负责证据化事实提取和受约束表达；权限、状态机、排班、事务和写库由确定性代码负责。
- Flash 有限恢复耗尽后创建人工任务；不调用 Pro，也不以 Scripted Provider 冒充在线处理。
- PostgreSQL 是工单和预约事实源；LangGraph Checkpoint 只保存工作流状态。
- 项目具有 MCP、Policy RAG、Outbox、Trace、UNKNOWN_COMMIT 对账和隔离 Replay。
- 默认 Provider 仍是 `scripted`；在线模型尚未取得生产激活资格。

## 不能夸大的内容

- 不说“已经生产上线”或“达到企业生产 SLA”。
- 不说合成政策就是目标小区的正式规章。
- 不说 Replay 能回滚数据库或重放历史写操作。
- 不说 SSE 是业务事实源或持久消息系统。
- 不说模型可以直接决定状态、维修责任、赔偿或预约结果。
- 不用测试数量代替业务效果；测试只作为具体风险的证据。

最新事实基线见 [产品验收记录](../PRODUCT_ACCEPTANCE_20260909.md)。

