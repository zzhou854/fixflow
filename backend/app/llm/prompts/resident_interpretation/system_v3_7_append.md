v3.7 独立最终判定表（覆盖前文冲突规则）：

1. 先判安全：燃气、结构/外窗坠落、电梯、人员受伤加
OTHER_REVIEW_REQUIRED；普通电气火情只加 ELECTRICAL_HAZARD 和
IMMEDIATE_DANGER；水接触电线/插线板同时加 ACTIVE_FLOODING、
ELECTRICAL_HAZARD、IMMEDIATE_DANGER；冒烟风险视为 ELECTRICAL 并加后两项。
任一 safety_flags 非空或 requested_human=true 时，缺失字段立即固定为空。
2. 再判意图：燃气、结构、外窗等危险且要求处理是 NEW_REPAIR；卧室不正常、房间有
故障、机器持续异响也是 NEW_REPAIR。纯“坏了、帮我处理、猜哪里坏了、？？、[图片]、
那个东西坏了”无上下文才是 UNKNOWN。回答上一轮追问是 PROVIDE_INFORMATION；纠正
旧说法必须 user_correction=true，撤销预约并转人工也属于纠正。
3. 预约上下文：只回答可用时间（明天下午、周六两点以后、周三上午、日期定了但几点
未知）一律 PROVIDE_INFORMATION，即使说“可以安排”；主动提出预约但没日期、指定师傅、
保证空位、选候选或指定精确钟点才是 SELECT_APPOINTMENT_SLOT。改期上下文始终
RESCHEDULE_APPOINTMENT。
4. 再机械计算缺失：预约已给可用时段后为空；未给时间、未决定或要求保证时只缺
AVAILABILITY。改期给出上下午等替代窗口后为空；只有日期无时段才缺 AVAILABILITY；
朋友家的改期不在此追问。
5. 普通报修逐项检查类别、空间位置、有效症状。模糊无上下文文本三项全缺；卧室不正常
和“A座测试房间有故障”已有位置，只缺类别、描述；机器异响已有描述；“在卫生间”只
补位置；墙上插座提供类别、描述但缺位置；漏水地点不确定保留 WATER_LEAK 且只缺位置；
洗手盆下水堵塞有位置、描述但只缺类别；回答“不知道坏的是什么”沿用已知位置，只缺
类别、描述。
6. 门锁保养仍提取 DOOR_LOCK；所有示例都必须显式输出完整且仅包含仍缺项的数组。
