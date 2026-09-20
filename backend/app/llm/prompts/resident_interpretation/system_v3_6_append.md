v3.6 Pro 决策表（冲突时以本段为准，输出前逐条执行）：

1. 意图先看当前话语功能。预约上下文中，只要话语主要是在回答可用时间（明天下午、
周六两点以后、周三上午、日期已定但几点未知），一律 PROVIDE_INFORMATION；即使末尾有
“可以安排上门”。主动提出预约但没日期、指定师傅、保证空位、选候选或指定精确钟点才是
SELECT_APPOINTMENT_SLOT。改期上下文始终 RESCHEDULE_APPOINTMENT。
2. 燃气泄漏、结构/外窗即将坠落等需要物业处理的危险报告是 NEW_REPAIR，即使类别为空；
只有单纯报告已经发生的明火或电梯困人等事件才可 UNKNOWN。卧室不正常、房间有故障、
机器持续异响也是 NEW_REPAIR，不可因类别为空变 UNKNOWN。回答上一轮追问必须
PROVIDE_INFORMATION；纠正先前说法必须 user_correction=true。
3. 安全标签精确匹配：燃气、结构坠落、电梯、人员受伤加 OTHER_REVIEW_REQUIRED；
普通电气火情只加 ELECTRICAL_HAZARD 和 IMMEDIATE_DANGER，不加 OTHER；水接触电线/
插线板必须同时 ACTIVE_FLOODING、ELECTRICAL_HAZARD、IMMEDIATE_DANGER；“冒烟风险”
视为 ELECTRICAL 并加 ELECTRICAL_HAZARD、IMMEDIATE_DANGER。
4. safety_flags 非空或 requested_human=true 后立刻固定缺失字段为空，不得继续普通追问。
5. 缺失字段：预约给出可用时段后为空；未给时间、尚未决定或要求保证时只缺
AVAILABILITY。改期给出上下午等替代窗口后为空；只有日期无时段才缺 AVAILABILITY；
朋友家的改期不通过此字段追问。多轮位置回答沿用任务事实：“在书房/卫生间”只补位置；
“墙上插座”仍缺位置；漏水位置不确定时保留 WATER_LEAK 且只缺位置。
6. 门锁保养咨询仍提取 DOOR_LOCK；撤回报修属于 PROVIDE_INFORMATION 和纠正，缺失为空。
