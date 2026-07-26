v3.3 规则（冲突时以本段为准）：

1. 安全标签非空或 requested_human=true 时，缺失字段恒为空。
2. SELECT_APPOINTMENT_SLOT 任务中：只回答“明天下午、周三上午、两点以后”等可用时间是
   PROVIDE_INFORMATION 且缺失为空；明确要求安排上门、选择候选或指定钟点才是
   SELECT_APPOINTMENT_SLOT；要求安排但完全没给时间时只缺 AVAILABILITY。
3. RESCHEDULE_APPOINTMENT 任务中：只有日期而无上下午/钟点时只缺 AVAILABILITY；
   给出上下午等替代窗口则缺失为空；朋友家的改期不通过缺失字段索要信息。
4. 无上下文的“坏了、帮我处理、猜哪里坏了、？？、[图片]、那个东西坏了”均为
   UNKNOWN，且恰好缺类别、位置、描述三项。
5. 具体症状（窗户合不上、机器异响）已构成描述，不再缺描述；“在卫生间”只提供
   位置；“墙上插座”提供类别和描述但仍缺位置；洗手盆下水堵塞只缺类别。
6. 配电设备正在起火必须为 NEW_REPAIR + ELECTRICAL，并标记
   ELECTRICAL_HAZARD、IMMEDIATE_DANGER；水造成触电风险时类别优先 ELECTRICAL。
