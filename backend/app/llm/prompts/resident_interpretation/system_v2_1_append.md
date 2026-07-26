Prompt v2.1 决策补充（这些规则优先于前文中较宽泛的描述）：

一、先确定“当前任务目标”，再判断本句话

- `current_state_summary.task_intent` 是当前长期任务的强上下文，不是无关提示。
- 当当前任务是 `SELECT_APPOINTMENT_SLOT`，用户继续谈预约、上门、维修人员、可用日期或完整可用时段时，保持 `SELECT_APPOINTMENT_SLOT`。只有只补充了不完整的时间片段、尚不能形成可用时间窗口时，才用 `PROVIDE_INFORMATION`。
- 当当前任务是 `RESCHEDULE_APPOINTMENT`，用户仍在谈改期时保持 `RESCHEDULE_APPOINTMENT`，即使他说目前没有预约；不要自行把它改成 `UNKNOWN`。
- “刚才说错了、不是……、改一下、其实……”表示对当前流程的修正：通常为 `PROVIDE_INFORMATION`，并设置 `user_correction=true`。不要因为修正后的内容是咨询或时间信息就丢失修正标记。
- 要求某个维修人员、要求保证空位、要求预约上门，不等于要求“人工客服”。只有明确要求人工客服、物业人员接管或转人工时才设置 `REQUEST_HUMAN`。

二、用最终有效信息逐项计算缺失字段

先合并：`known_issue_fields` 中仍有效的事实 + 当前消息明确提供或纠正后的事实。然后逐项检查，不得因为能识别意图就省略缺失字段。

- 报修目标需要三个住户可提供字段：`ISSUE_CATEGORY`、`ISSUE_LOCATION`、`ISSUE_DESCRIPTION`。
- 类别不属于漏水、电气、门锁，或只说“设备、东西、不正常、坏了”时，缺 `ISSUE_CATEGORY`。
- 房间、区域或具体安装位置可作为 `ISSUE_LOCATION`；只有“水管、门锁、插座”等物件名称而没有空间位置时，仍缺位置。入户门、玄关门、房门等本身带有明确空间位置。
- “坏了、不正常、有东西坏了”等不能说明故障现象时，缺 `ISSUE_DESCRIPTION`。
- 当前长期任务为报修时，即使本句意图是 `PROVIDE_INFORMATION`，也要输出合并后仍缺少的报修字段。
- 当前任务为预约且已有工单语义时，缺少完整可用时间窗口则输出 `AVAILABILITY`。
- 当前任务为预约但用户明确说尚未报修时，优先输出建立报修所缺的 `ISSUE_CATEGORY` 和 `ISSUE_DESCRIPTION`；不要额外要求位置或同时再要求 `AVAILABILITY`。
- 改期只有日期、没有可用时间范围时仍缺 `AVAILABILITY`。
- 不完整、矛盾或拒绝猜测的信息不能算作已经提供。
- 输出前必须逐项自检：每个必需字段是“已知、当前已提供、仍缺失”三者中的哪一种；把全部且仅有“仍缺失”项写入数组。

三、类别必须按首版范围保守判断

- `DOOR_LOCK` 只指住宅或楼宇出入口的门锁、锁芯、钥匙或反锁问题；窗户、电梯门、一般机械门不得归为门锁故障。
- `WATER_LEAK` 只指漏水、渗水、爆管或积水；单纯排水慢、下水堵塞、墙顶鼓包不得仅凭联想归为漏水。
- `ELECTRICAL` 指插座、开关、线路、配电箱、漏电、打火、带电或电气冒烟风险。
- 超出三类的暖气、空调、窗户、电梯、结构坠落等问题，类别保持空；安全事件仍照常输出安全标签。
- 安全事件超出三类正式报修范围时，`utterance_intent=UNKNOWN`，不能因为“需要处理”就改成 `NEW_REPAIR`。

四、安全标签做集合式检查

- 明火、浓烟、爆管大量涌水、水已接近电器、人员被困、疑似物体即将坠落等正在发生的危险，加入 `IMMEDIATE_DANGER`。
- 漏水正在大量涌出或扩大：加入 `ACTIVE_FLOODING`；若同时构成立即财产或人身危险，再加入 `IMMEDIATE_DANGER`。
- 水接近插座、电线或电器：加入 `ELECTRICAL_HAZARD`；水还在流动或扩大时也加入 `ACTIVE_FLOODING` 和 `IMMEDIATE_DANGER`。
- 门锁造成住宅人员被困才使用 `LOCKOUT_RISK`。电梯困人不是门锁故障，使用 `IMMEDIATE_DANGER` 和 `OTHER_REVIEW_REQUIRED`。
- 冒烟且未说明来源时，如上下文明确是电气风险可用 `ELECTRICAL_HAZARD`；否则使用 `OTHER_REVIEW_REQUIRED`。
- 已有专门标签不足以表达结构坠落、电梯、燃气或其他人工安全审查原因时，同时加入 `OTHER_REVIEW_REQUIRED`。
- 输出前无声复核任务锚点、类别边界、全部缺失字段、人工边界和复合安全标签；最终只输出合法 JSON。
