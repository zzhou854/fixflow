v3.4 最终机械校验（冲突时以本段为准）：

1. safety_flags 非空或 requested_human=true 时，model_suggested_missing_fields 必须为空，
不得再检查普通报修字段。普通明火除 IMMEDIATE_DANGER 外还要 OTHER_REVIEW_REQUIRED；
配电设备起火则为 NEW_REPAIR、ELECTRICAL，并标记 ELECTRICAL_HAZARD 与
IMMEDIATE_DANGER。水造成触电风险时类别优先 ELECTRICAL。
2. SELECT_APPOINTMENT_SLOT 上下文：仅回答已有提问的可用时间是
PROVIDE_INFORMATION 且不缺字段；主动要求预约、指定师傅、要求保证空位、尚未决定日期，
或选择候选均为 SELECT_APPOINTMENT_SLOT。未给可用时间或只要求保证时只缺
AVAILABILITY；已有漏水工单仍提取 WATER_LEAK。
3. RESCHEDULE_APPOINTMENT 上下文始终保持该意图：只有日期无上下午/钟点时只缺
AVAILABILITY；给出替代时段则不缺；朋友家的预约不通过缺失字段追问。
4. 无上下文的纯“坏了、帮我处理、猜哪里坏了、？？、[图片]、那个东西坏了”为
UNKNOWN 且缺类别、位置、描述；但只要给出空间位置、具体故障或类别就是 NEW_REPAIR。
“卧室这里不正常”和“A座测试房间有故障”已有位置，仅缺类别和描述；“漏水但地点想不
起来”只缺位置；具体异响是有效描述。
5. 回答追问时：“墙上插座”提供类别和描述但缺位置；“不知道坏的是什么”沿用已知位置，
只缺类别和描述；洗手盆下水堵塞提供位置和描述，只缺类别。纠正旧事实必须
user_correction=true；撤销预约并转人工也属于纠正。
6. 输出前逐一复核缺失数组，不得凭语感省略或增加；所有示例均强制输出该字段。
